"""Exact alpha reuse, process scope and bounded manual-preview lifetime."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QProcess

from iphoto.document import empty_mask,raster_mask
from iphoto.controllers import matte_process
from iphoto.matting import session
from iphoto.matting.channels import whole_options
from iphoto.matting.worker import channel_session
from iphoto.paths import ROOT
from iphoto.workspace import Editor
from test_ai import wait_for
from test_channel_matting import scene
from test_editor import settled


def test_exact_rgb_guide_and_dimensions_are_required_and_outputs_are_owned():
    calls=[];events=[]
    def solve(image,guide,progress=None):
        calls.append((image.size,guide.copy()))
        return np.full(guide.shape,len(calls),dtype='uint8'),3
    solver=session.ChannelSolver(solve)
    image=Image.new('RGB',(16,16),(20,30,40));guide=np.full((16,16),128,dtype='uint8')
    first,_=solver(image,guide);first[:]=99
    actual,tiles=solver(image,guide,progress=lambda **event:events.append(event))
    assert len(calls)==1 and tiles==3 and (actual==1).all()
    assert solver.reused and solver.computed_tiles==0 and events==[{'phase':'reuse'}]
    actual[:]=98;assert (solver(image,guide)[0]==1).all()
    changed=guide.copy();changed[2,3]=0
    solver(image,changed);assert len(calls)==2 and not solver.reused
    edited=image.copy();edited.putpixel((2,3),(21,30,40))
    solver(edited,changed);assert len(calls)==3 and not solver.reused
    solver(Image.new('RGB',(8,32),(20,30,40)),guide.reshape(32,8))
    assert len(calls)==4


def test_single_entry_evicts_old_inputs_and_oversized_work_is_not_retained(monkeypatch):
    monkeypatch.setattr(session,'MAX_PIXELS',256);calls=[]
    def solve(image,guide,progress=None):calls.append(image.size);return guide.copy(),1
    solver=session.ChannelSolver(solve);guide=np.full((16,16),128,dtype='uint8')
    red=Image.new('RGB',(16,16),'red');blue=Image.new('RGB',(16,16),'blue')
    for image in (red,blue,red):solver(image,guide)
    assert len(calls)==3 and solver._entry[1].nbytes<=256
    for _ in range(2):solver(Image.new('RGB',(17,17),'red'),np.full((17,17),128,dtype='uint8'))
    assert len(calls)==5 and solver._entry is None


def test_failed_solve_cannot_install_a_new_cache_entry():
    calls=[]
    def solve(image,guide,progress=None):
        calls.append(1)
        if image.getpixel((0,0))==(255,0,0):raise ValueError('inference failed')
        return guide.copy(),1
    solver=session.ChannelSolver(solve);guide=np.full((16,16),128,dtype='uint8')
    good=Image.new('RGB',(16,16),'blue');bad=Image.new('RGB',(16,16),'red')
    solver(good,guide)
    for _ in range(2):
        with pytest.raises(ValueError,match='inference failed'):solver(bad,guide)
    solver(good,guide)
    assert len(calls)==3 and solver.reused


@pytest.mark.parametrize('changes',[{'channel_session':True},{'channel_session':'bad'},
    {'channel_session':'g'*32},{'method':'hair'},{'preview_only':1},{'channel_token':'b'*32}])
def test_worker_rejects_invalid_session_contract(changes):
    request={'method':'channel','preview_only':True,'channel_token':'a'*32,'channel_session':'a'*32}
    with pytest.raises(ValueError,match='会话无效'):channel_session({**request,**changes})


def raw_request(path):
    return {'id':1,'op':'matte','generation':7,'source_path':str(path),
            'source_sha':hashlib.sha256(path.read_bytes()).hexdigest(),'method':'channel',
            'mask':empty_mask(True),'channel_options':whole_options(),'preview_only':True,
            'channel_token':'a'*32,'channel_session':'a'*32}


def run_worker(requests):
    child=subprocess.run([sys.executable,str(ROOT/'run.py'),'--matte-worker'],
        input=''.join(json.dumps(request)+'\n' for request in requests),capture_output=True,
        text=True,encoding='utf8',timeout=35)
    assert child.returncode==0,child.stderr
    return [json.loads(line) for line in child.stdout.splitlines() if line.startswith('{') and '"progress"' not in line]


def test_real_worker_serves_bound_requests_then_exits_at_eof_and_keeps_each_curve(tmp_path):
    path=tmp_path/'source.png';Image.new('RGB',(96,64),(64,128,192)).save(path)
    request=raw_request(path)
    requests=[{**request,'id':i,'channel_options':{**request['channel_options'],'gamma':gamma}}
              for i,gamma in enumerate((1.,2.,.5),1)]
    responses=run_worker(requests)
    assert [item['id'] for item in responses]==[1,2,3]
    assert all(item['ok'] and item['channel_session']=='a'*32 for item in responses)
    alphas=[raster_mask(item['result']['mask'],(96,64)).getpixel((0,0)) for item in responses]
    luminance=round(64*.2126+128*.7152+192*.0722)
    assert alphas==[round((luminance/255)**(1/gamma)*255) for gamma in (1.,2.,.5)]


@pytest.mark.parametrize('changes',[{'channel_token':'b'*32,'channel_session':'b'*32},
    {'source_path':'another-photo.png'},{'source_sha':'0'*64},{'method':'neural','channel_session':None}])
def test_real_worker_never_reuses_a_session_for_another_scope(tmp_path,changes):
    path=tmp_path/'source.png';Image.new('RGB',(96,64),'gray').save(path);request=raw_request(path)
    responses=run_worker([request,{**request,'id':2,**changes},{**request,'id':3}])
    assert [item['id'] for item in responses]==[1,2]
    assert responses[0]['ok'] and not responses[1]['ok'] and 'result' not in responses[1]
    assert 'channel_session' not in responses[1]


def test_live_worker_rechecks_file_bytes_before_another_bound_request(qt_app,tmp_path):
    path=tmp_path/'source.png';Image.new('RGB',(96,64),'gray').save(path);request=raw_request(path)
    process=QProcess();messages=[];buffer=bytearray()
    def read():
        buffer.extend(bytes(process.readAllStandardOutput()))
        while b'\n' in buffer:
            line,_,tail=buffer.partition(b'\n');buffer[:]=tail
            if line.startswith(b'{'):messages.append(json.loads(line))
    process.readyReadStandardOutput.connect(read)
    try:
        process.start(sys.executable,[str(ROOT/'run.py'),'--matte-worker'])
        wait_for(lambda:process.state()==QProcess.Running)
        process.write((json.dumps(request)+'\n').encode());wait_for(lambda:len(messages)==1)
        assert messages[0]['ok'] and process.state()==QProcess.Running
        Image.new('RGB',(96,64),'red').save(path)
        process.write((json.dumps({**request,'id':2})+'\n').encode())
        wait_for(lambda:len(messages)==2 and process.state()==QProcess.NotRunning)
        assert not messages[1]['ok'] and '源照片已变化' in messages[1]['error']
    finally:
        if process.state()!=QProcess.NotRunning:process.kill();process.waitForFinished(2000)


@pytest.mark.parametrize('marker',[None,'b'*32])
def test_missing_or_wrong_session_ack_cannot_publish_or_keep_worker(marker):
    active={'id':1,'op':'matte','generation':7,'source_sha':'photo','method':'channel',
            'channel_token':'a'*32,'channel_session':'a'*32,'channel_revision':1,'preview_only':True}
    response={'id':1,'op':'matte','generation':7,'ok':True,'result':{'fixture':'never publish'}}
    if marker is not None:response['channel_session']=marker
    calls=[]
    owner=SimpleNamespace(_matte_active=active,_matte_pending=None,_matte_buffer=b'',_generation=7,
        _sha='photo',_closing=False,_matte_aborting=False,_matte_fresh=False,
        _matte_session=('a'*32,'source','photo'),changed=SimpleNamespace(emit=lambda:None),
        _notify=lambda *args:calls.append('error'),_channel_mask=SimpleNamespace(
            matches_request=lambda request:True,result_ready=lambda *args:calls.append('published'),
            failed=lambda *args:calls.append('failed')),
        _matte_process=SimpleNamespace(readAllStandardOutput=lambda:(json.dumps(response)+'\n').encode(),
            state=lambda:QProcess.Running,kill=lambda:calls.append('killed')))
    matte_process.read(owner)
    assert owner._matte_session is None and not owner._matte_fresh and owner._matte_aborting
    assert calls==['killed','failed','error']


def ready(editor):
    wait_for(lambda:not editor.channelMask.loading and editor.channelMask.previewUrl and settled(editor))


@pytest.mark.parametrize('ending',['close','idle','invalidate','cancel','crash'])
def test_real_preview_reuses_exact_alpha_and_releases_session(qt_app,ai_store,tmp_path,ending):
    image,_,mask,_=scene();path=tmp_path/'local.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._set_candidate(mask);wait_for(lambda:settled(editor))
        before=deepcopy((editor._layers,editor._candidate,editor._cursor,editor._draft_history))
        channel=editor.channelMask;channel.open();ready(editor)
        channel.setOption('color',False);ready(editor)
        channel.previewResult();wait_for(lambda:channel.hasResult and settled(editor),seconds=45)
        first=deepcopy(channel._result);pid=editor._matte_process.processId()
        assert pid and editor._matte_fresh and editor._matte_session[0]==channel.state['token']
        assert not first['quality']['reused_alpha'] and first['quality']['computed_tiles']>0
        assert channel.sessionTimer.interval()==30000 and channel.sessionTimer.isActive()
        channel.setOption('gamma',1.2);ready(editor)
        channel.previewResult();wait_for(lambda:channel.hasResult and settled(editor),seconds=30)
        assert editor._matte_process.processId()==pid and channel._result['quality']['reused_alpha']
        assert '沿用相同的 AI 透明边缘' in channel.note
        assert channel._result['quality']['computed_tiles']==0
        assert raster_mask(channel._result['mask'],image.size).tobytes()==raster_mask(first['mask'],image.size).tobytes()
        assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history)==before
        if ending=='close':channel.close()
        elif ending=='idle':channel.sessionTimer.start(1)
        elif ending=='invalidate':editor._generation+=1;editor.changed.emit()
        elif ending=='cancel':
            channel.setOption('gamma',1.3);ready(editor);channel.previewResult()
            wait_for(lambda:editor.matteBusy);editor.cancelMatte()
        else:editor._matte_process.kill()
        wait_for(lambda:editor._matte_process.state()==QProcess.NotRunning and not editor.matteBusy,seconds=30)
        assert editor._matte_session is None
        assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history)==before
        if ending in ('idle','crash'):
            # Losing the accelerator does not invalidate a completed preview.
            assert channel.hasResult
            channel.setOption('gamma',1.3);ready(editor);channel.previewResult()
            wait_for(lambda:channel.hasResult and settled(editor),seconds=45)
            assert editor._matte_process.processId()!=pid and not channel._result['quality']['reused_alpha']
            channel.close();wait_for(lambda:editor._matte_process.state()==QProcess.NotRunning)
    finally:editor.close()


def test_changed_guide_recomputes_in_same_process_and_matches_fresh_worker(qt_app,ai_store,tmp_path):
    image,_,mask,_=scene();path=tmp_path/'changed.png';image.save(path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._set_candidate(mask);wait_for(lambda:settled(editor))
        channel=editor.channelMask;channel.open();ready(editor)
        channel.setOption('color',False);ready(editor);channel.previewResult()
        wait_for(lambda:channel.hasResult and settled(editor),seconds=45)
        pid=editor._matte_process.processId()
        channel.setOption('radius',8);ready(editor);channel.previewResult()
        wait_for(lambda:channel.hasResult and settled(editor),seconds=30)
        assert editor._matte_process.processId()==pid and not channel._result['quality']['reused_alpha']
        assert channel._result['quality']['computed_tiles']>0
        channel._release_session();wait_for(lambda:editor._matte_process.state()==QProcess.NotRunning)
        response=run_worker([{'id':1,'op':'matte','generation':1,'method':'channel','mask':mask,
            'source_path':str(path),'source_sha':hashlib.sha256(path.read_bytes()).hexdigest(),
            'channel_options':channel.options}])[0]
        assert response['ok'] and 'channel_session' not in response
        assert raster_mask(response['result']['mask'],image.size).tobytes()==raster_mask(channel._result['mask'],image.size).tobytes()
    finally:editor.close()
