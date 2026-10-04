"""Facial phases are status only; masks and layers publish once at completion."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.controllers import worker_bridge
from iphoto.document import empty_mask, raster_mask
from iphoto.paths import ROOT
from iphoto.segmentation import face_precision, face_skin, service
from test_ai import wait_for
from test_face_parts import labels, rect
from test_import_export import ui  # noqa: F401


@pytest.mark.parametrize('case', ['valid','source','cached','fallback','wrong_id','wrong_op','old_active_op',
    'stale','old_active','cancelled','background','closing','bad_part','boolean','bad_total','bad_phase',
    'wrong_target','tile','non_dict'])
def test_facial_status_never_releases_task_or_publishes_partial_layers(case):
    progress={'kind':'face','phase':'face_infer','part':1,'total':1}
    if case in ('source','cached','fallback'):progress['phase']='face_'+case
    if case=='bad_part':progress['part']=0
    if case=='boolean':progress['part']=True
    if case=='bad_total':progress['total']=2
    if case=='bad_phase':progress['phase']='face_done'
    if case=='tile':progress.update(tile=1,tiles=1)
    if case=='non_dict':progress=[]
    active={'id':7,'op':'open' if case=='old_active_op' else 'segment',
            'generation':9 if case=='old_active' else 10,
            'jobs':[{'mask_target':'object' if case=='wrong_target' else 'face','face_part':'lips'}],
            'cancelled':case=='cancelled','priority':'low' if case=='background' else 'normal'}
    message={'id':8 if case=='wrong_id' else 7,'op':'open' if case=='wrong_op' else 'segment',
             'generation':9 if case=='stale' else 10,'progress':progress}
    owner=SimpleNamespace(_pixel_buffer=b'',_pixel_active=active,_generation=10,_closing=case=='closing',
        _status='previous',_layers=[{'id':'kept'}],_candidate=empty_mask(),_warm_ready_sha='unprepared',
        _pixel_process=SimpleNamespace(readAllStandardOutput=lambda:(json.dumps(message)+'\n').encode()),
        changed=SimpleNamespace(emit=lambda:None),_pump_pixel=lambda:pytest.fail('Progress cannot finish a task'))
    before=deepcopy((owner._layers,owner._candidate,owner._generation))
    worker_bridge._pixel_read(owner)
    assert owner._pixel_active is active and owner._warm_ready_sha=='unprepared'
    assert before==(owner._layers,owner._candidate,owner._generation)
    assert (owner._status!='previous')==(case in ('valid','source','cached','fallback'))


def test_mixed_batch_progress_identifies_the_current_face_without_partial_result(monkeypatch):
    statuses=[];completed=[]
    def parse(image,hint,points,*,progress,**kwargs):
        progress('face_prepare');progress('face_infer')
        assert not completed  # The whole batch returns only after all jobs.
        return hint,{'fixture':True}
    monkeypatch.setattr(face_skin,'segment',parse)
    monkeypatch.setattr(service,'segment',lambda *args,**kwargs:(rect(),{'fixture':True}))
    jobs=[{'id':'tree','mask_target':'object'}, {'id':'face1','mask_target':'face','hint':rect()},
          {'id':'face2','mask_target':'face_skin','hint':rect()}]
    result=service.segment_jobs(Image.new('RGB',(240,120)),jobs,detail_progress=lambda *event:statuses.append(event))
    completed.append(result)
    assert statuses==[('face_prepare',2,3),('face_infer',2,3),('face_prepare',3,3),('face_infer',3,3)]
    assert [item['id'] for item in result['items']]==['tree','face1','face2']


def test_neural_and_cached_phases_reflect_actual_work_and_keep_same_classes():
    parser=face_precision.FaceParser.__new__(face_precision.FaceParser);parser._cached=None;calls=[]
    scores=np.zeros((1,11,512,512),np.float32);scores[:,1]=1
    parser.session=SimpleNamespace(run=lambda *args:calls.append(True) or [scores])
    points=np.array([[2,3],[8,3],[5,6],[3,9],[7,9]],np.float32)
    image=Image.new('RGB',(12,12));phases=[]
    first=parser.predict_native(image,points,progress=phases.append)
    assert phases==['face_infer','face_boundary'] and len(calls)==1
    phases.clear();second=parser.predict_native(image,points,progress=phases.append)
    assert phases==['face_cached'] and second is first and len(calls)==1
    assert not first.flags.writeable


def test_fallback_status_is_visible_and_the_original_protection_policy_remains(monkeypatch):
    classes=labels();phases=[]
    monkeypatch.setattr(face_precision,'available',lambda:True)
    def failed(**kwargs):
        kwargs['progress']('face_model');raise ValueError('Unavailable precision model')
    monkeypatch.setattr(face_precision,'backend',failed)
    def predict(patch,*,progress):
        progress('face_infer');progress('face_boundary');return classes
    monkeypatch.setattr(face_skin,'backend',lambda **kwargs:SimpleNamespace(predict_native=predict))
    mask,quality=face_skin.segment(Image.new('RGB',(240,120)),rect(),[[55/240,47/120,1]],
        crop=[0,0,1,1],target='face',scope='region',part='lips',
        features={'eyes':[[.1,.2],[.4,.2]],'mouth':[[.15,.6],[.3,.6]]},progress=phases.append)
    assert phases==['face_prepare','face_model','face_fallback','face_infer','face_boundary','face_protect','face_encode']
    assert quality['model'].startswith('BiSeNet') and any('基础面部' in text for text in quality['warnings'])
    assert not np.any(np.asarray(raster_mask(mask,(240,120)))[~np.isin(classes,(12,13))])


@pytest.mark.parametrize('failure',['missing','changed'])
def test_native_face_source_phase_precedes_failure_without_partial_mask(tmp_path,failure):
    path=tmp_path/'source.png';Image.new('RGB',(180,120)).save(path)
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    proxy=tmp_path/'proxy.png';Image.new('RGB',(180,120)).save(proxy)
    if failure=='missing':path.unlink()
    else:Image.new('RGB',(180,120),'white').save(path)
    request={'id':7,'op':'segment','generation':10,'proxy_path':str(proxy),'source_path':str(path),'source_sha':sha,
             'jobs':[{'id':'face','mask_target':'face'}]}
    child=subprocess.run([sys.executable,str(ROOT/'run.py'),'--pixel-worker'],input=json.dumps(request)+'\n',
        capture_output=True,text=True,encoding='utf8',timeout=30)
    messages=[json.loads(line) for line in child.stdout.splitlines() if line.startswith('{')]
    assert child.returncode==0 and messages[0]['progress']=={'kind':'face','phase':'face_source','part':1,'total':1}
    assert messages[-1]['ok'] is False and all('result' not in message for message in messages)


def test_pixel_wait_elapsed_updates_without_editor_repaint_and_cancel_is_reachable(ui):  # noqa: F811
    editor,window,find,warnings,_=ui;window.resize(1080,700);window.setProperty('chatOpen',False)
    before=deepcopy((editor._layers,editor._candidate,editor._cursor,editor._generation))
    editor._pixel_active={'id':999,'op':'segment','generation':editor._generation,
        'jobs':[{'mask_target':'face','face_part':'lips'}],'context':{'purpose':'points'}}
    editor._status='正在首次加载面部分区模型 1/1…可随时取消';editor.changed.emit()
    changes=[];editor.changed.connect(lambda:changes.append(True))
    elapsed=find('localTaskElapsedText');wait_for(lambda:elapsed.property('text')!='已用 0 秒',seconds=2.5)
    assert elapsed.isVisible() and not changes
    cancel=find('cancelAiRequest');origin=cancel.mapToScene(QPointF(0,0))
    assert 0<=origin.x()<window.width()-cancel.width() and 0<=origin.y()<window.height()-cancel.height()
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,cancel.mapToScene(QPointF(cancel.width()/2,cancel.height()/2)).toPoint())
    wait_for(lambda:editor.selection.taskKind=='none')
    assert not elapsed.isVisible() and before==(editor._layers,editor._candidate,editor._cursor,editor._generation)
    assert '已停止' in editor.status and not warnings
