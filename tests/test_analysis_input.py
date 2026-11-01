"""Spatial planning, proxy/native segmentation and scene identity use applied pixels."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

from iphoto.analysis_content import key,ContentCache
from iphoto.controllers import analysis_input,pixel_selections,worker_bridge
from iphoto.document import empty_mask,new_layer
from iphoto.segmentation import pixel_worker,service
from iphoto.workspace import Editor
from test_ai import wait_for
from test_analysis_content import scene
from test_editor import settled


@pytest.mark.parametrize('change',['recipe','locked','name','mask','opacity','visibility','pixels','box','group'])
def test_content_identity_changes_only_for_visible_pixel_composition(tmp_path,change):
    source,layers=scene(tmp_path);before=key(layers)
    if change=='recipe':layers[0]['recipe']['exposure']=2
    elif change=='locked':layers[-1]['locked']=['exposure']
    elif change=='name':layers[-1]['name']='renamed'
    elif change=='mask':layers[-1]['mask']=empty_mask()
    elif change=='opacity':layers[-1]['opacity']=.7
    elif change=='visibility':layers[-1]['visible']=False
    elif change=='box':layers[-1]['pixel_patch']['box'][0]=10
    elif change=='pixels':
        from iphoto.pixel_patch import encode_patch
        layers[-1]['pixel_patch']=encode_patch(Image.new('RGB',source.image.size,'green'),list(source.image.size),[0,0,*source.image.size])
    else:
        group=new_layer('group',True,kind='group');group['opacity']=.6
        layers[-1]['parent_id']=group['id'];layers.append(group)
    assert (key(layers)==before)==(change in ('recipe','locked','name'))


def prepare_editor(editor,layers):
    editor._layers=deepcopy(layers);editor._selected=layers[0]['id']
    editor._load_layer();editor._commit();editor._change()
    wait_for(lambda:settled(editor))


def test_real_spatial_preparation_reuses_verified_content_and_repairs_a_changed_proxy(qt_app,ai_store,tmp_path,monkeypatch):
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store);calls=[];preparations=[]
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        assert editor.ai.save('openai','http://127.0.0.1:9/v1','test-vision','sk-test-only-not-real',False,True)
        prepare_editor(editor,layers)
        request=editor._request
        def track(op,**data):
            if op=='analysis_input':preparations.append(deepcopy(data))
            return request(op,**data)
        monkeypatch.setattr(editor,'_request',track)
        def plan(*args):
            with Image.open(args[3]) as image:assert image.convert('RGB').getpixel((20,20))==(90,30,90)
            calls.append(args);return True
        monkeypatch.setattr(editor.ai,'plan',plan)
        for index in range(3):
            if index==2:Path(editor._analysis_input['path']).write_bytes(b'damaged proxy')
            assert editor.sendMessage('分析已修改的内容','scene')
            wait_for(lambda:len(calls)==index+1 and settled(editor))
            assert editor._analysis_input['content_reference']
        assert len(preparations)==2
        native=pixel_worker.load_native({'source_path':str(source.path),'source_sha':source.digest,
                                        'content_reference':editor._analysis_input['content_reference']})
        assert native.convert('RGB').getpixel((20,20))==(90,30,90)
        assert sha256(source.path.read_bytes()).hexdigest()==source.digest
    finally:editor.close()


def test_content_change_invalidates_scene_and_faces_but_tone_keeps_them(qt_app,ai_store,tmp_path):
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        original_key=editor._scene_key();prepare_editor(editor,layers);applied_key=editor._scene_key()
        assert applied_key!=original_key
        editor._scene.set({'summary':'current','objects':[{'id':'target','name':'target','category':'object',
                                                          'mask':empty_mask(True)}]});revision=editor._scene.revision
        editor._face_hints=[{'id':'current-content-face'}]
        editor.setParameter('exposure',.4);editor.finishGesture();wait_for(lambda:settled(editor))
        assert editor._scene_key()==applied_key and editor._scene.revision==revision
        assert editor._face_hints==[{'id':'current-content-face'}]
        editor._layers[-1]['opacity']=1;editor._change();wait_for(lambda:settled(editor))
        assert editor._scene_key()!=applied_key and editor._scene.catalog is None and not editor._face_hints
    finally:editor.close()


def test_cancel_during_prepare_never_dispatches_late_plan_or_pixels(qt_app,ai_store,tmp_path,monkeypatch):
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store);held=[]
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        prepare_editor(editor,layers)
        monkeypatch.setattr(editor,'_request',lambda op,**data:held.append({'op':op,'context':data['context']}))
        assert analysis_input.defer(editor,'segment',{'jobs':[{'id':'target'}],'context':{'purpose':'points'}})
        active=held[0];editor._active=active
        assert editor.busy and editor.selection.taskKind=='ai' and editor.selection.taskCancellable
        editor.selection.cancelTask()
        assert active['cancelled'] and not editor._analysis_token and editor._analysis_waiters==[]
        analysis_input.ready(editor,{},active)
        assert len(held)==1 and editor._candidate is None
        editor._active=None
    finally:editor.close()


class Stream(io.StringIO):
    def reconfigure(self,**kwargs):pass


@pytest.mark.parametrize('failure',['cancel','failure'])
def test_coalesced_background_preparation_cannot_leave_catalog_items_pending(qt_app,ai_store,tmp_path,monkeypatch,failure):
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store);held=[]
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        prepare_editor(editor,layers);before=deepcopy(editor._layers)
        editor._scene.set({'summary':'current','objects':[{'id':name,'name':name,'category':'object',
            'mask':empty_mask(True)} for name in ('one','two')]})
        editor._scene.mark_pixel_status(['one','two'],'pending')
        monkeypatch.setattr(editor,'_request',lambda op,**data:held.append({'op':op,'context':data['context']}))
        for name in ('one','two'):
            analysis_input.defer(editor,'segment',{'jobs':[{'id':name}],'priority':'low','context':{
                'purpose':'precache','source_sha':editor._sha,'scene_revision':editor._scene.revision}})
        assert len(held)==1 and len(editor._analysis_waiters)==2
        if failure=='cancel':
            editor._active=held[0];editor.selection.cancelTask();editor._active=None
        else:analysis_input.failed(editor,'controlled preparation failure',held[0]['context'])
        assert editor._analysis_waiters==[] and editor._analysis_token is None
        assert editor._scene.pixel_status=={'one':'unavailable','two':'unavailable'}
        analysis_input.ready(editor,{},held[0]);assert len(held)==1 and editor._layers==before
    finally:editor._active=None;editor.close()


@pytest.mark.parametrize('case',['native','background','proxy-tampered','snapshot-tampered','source-tampered'])
def test_worker_binds_rgb_proxy_and_native_content_before_any_model_call(tmp_path,monkeypatch,case):
    source,layers=scene(tmp_path);cache=ContentCache(tmp_path)
    image,ref=cache.get(source,layers=layers);proxy=tmp_path/'content.png';image.convert('RGB').save(proxy)
    request={'op':'segment','id':1,'generation':7,'proxy_path':str(proxy),'source_path':str(source.path),
        'source_sha':source.digest,'source_size':list(source.image.size),'content_reference':ref,
        'proxy_sha256':sha256(proxy.read_bytes()).hexdigest(),'jobs':[{'id':'target','mask_target':'object'}]}
    if case=='background':request['priority']='low'
    elif case=='proxy-tampered':proxy.write_bytes(b'bad proxy')
    elif case=='snapshot-tampered':Path(ref['path']).write_bytes(b'bad snapshot')
    elif case=='source-tampered':Image.new('RGB',source.image.size,'green').save(source.path)
    seen=[]
    def segment(proxy,jobs,*,source,**kwargs):
        assert proxy.getpixel((20,20))==(90,30,90)
        if case=='background':assert source is None
        else:assert source.convert('RGB').getpixel((20,20))==(90,30,90)
        seen.append(True);return {'items':[]}
    monkeypatch.setattr(service,'segment_jobs',segment)
    output=Stream();monkeypatch.setattr(sys,'stdin',Stream(json.dumps(request)+'\n'));monkeypatch.setattr(sys,'stdout',output)
    pixel_worker.main();result=json.loads(output.getvalue().splitlines()[-1])
    assert result['ok']==(case in ('native','background'))
    if result['ok']:assert seen and result['result']['content_sha256']==ref['sha256']
    else:assert not seen and 'result' not in result


def test_wrong_pixel_content_ack_cannot_publish(qt_app,ai_store,tmp_path,monkeypatch):
    source,_=scene(tmp_path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        before=deepcopy(editor._layers)
        active={'op':'segment','id':123,'generation':editor._generation,'jobs':[{'id':'target'}],
                'context':{'purpose':'points'},'content_reference':{'sha256':'a'*64}}
        editor._pixel_active=active
        response={'op':'segment','id':123,'generation':editor._generation,'ok':True,
                  'result':{'items':[],'content_sha256':'b'*64}}
        monkeypatch.setattr(editor._pixel_process,'readAllStandardOutput',lambda:(json.dumps(response)+'\n').encode())
        monkeypatch.setattr(pixel_selections,'complete',lambda *args:pytest.fail('Wrong frame cannot publish'))
        worker_bridge._pixel_read(editor)
        assert editor._layers==before and editor._candidate is None and '内容已变化' in editor.status
    finally:editor.close()


@pytest.mark.parametrize('kind',['object','repair','mask','review','verify','apply'])
def test_real_close_up_operations_use_the_same_applied_content(qt_app,ai_store,tmp_path,monkeypatch,kind):
    from PIL import ImageDraw
    from iphoto.controllers import conversation,object_grounding,mask_refinement
    from iphoto.ai_mask_review import prepare_review
    from iphoto.document import raster_mask
    from iphoto.masks import encode_bitmap
    from test_ai_mask_refinement import typed_mask
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store);received=[]
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        prepare_editor(editor,layers);before=deepcopy(editor._layers)
        mask=typed_mask(source.image.size);alpha=Image.new('L',source.image.size)
        draw=ImageDraw.Draw(alpha);draw.rectangle((20,20,60,70),fill=255);draw.rectangle((100,25,110,35),fill=255)
        mask['bitmap']=encode_bitmap(alpha,preserve_resolution=True)
        operations={'object':('object_crop',object_grounding,'crop_ready'),
                    'repair':('repair_crop',conversation,'_repair_crop_ready'),
                    'apply':('mask_refinement_apply',mask_refinement,'applied_regions')}
        op,module,callback=operations.get(kind,('mask_refinement_crop',mask_refinement,'crop_ready'))
        monkeypatch.setattr(module,callback,lambda e,result,*args:received.append(result))
        data={'source_sha':source.digest,'context':{}}
        if kind in ('object','repair'):data['crop']=[.15,.15,.85,.85]
        else:data['mask']=mask
        if kind=='review':data.update(review=True,reference_mask=mask,context_crop=[0,0,1,1])
        elif kind=='verify':data.update(verify=True,reference_mask=mask,context_crop=[0,0,1,1])
        elif kind=='apply':
            *_,regions=prepare_review(source.image,mask)
            data.update(regions=regions,exclude_regions=[2])
        editor._request(op,**data);wait_for(lambda:bool(received) and settled(editor))
        result=received[0];assert result['content_sha256']==editor._analysis_input['content_reference']['sha256']
        if kind=='apply':
            actual=raster_mask(result['mask'],source.image.size)
            assert actual.getpixel((30,30))==255 and actual.getpixel((105,30))==0
        else:
            with Image.open(result['path']) as picture:assert picture.convert('RGB').getpixel((10,10))==(90,30,90)
            if kind in ('review','verify'):
                assert result['context_path'] and result['changes_path'] and result['reference_path']
        assert editor._layers==before and not editor.hasSelectionDraft
    finally:editor.close()


def test_preparing_content_does_not_replace_an_explicit_stale_crop_source(qt_app,ai_store,tmp_path,monkeypatch):
    from iphoto.controllers import object_grounding
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        prepare_editor(editor,layers);before=deepcopy(editor._layers)
        monkeypatch.setattr(object_grounding,'crop_ready',lambda *args:pytest.fail('Stale source cannot be cropped'))
        editor._request('object_crop',crop=[.1,.1,.9,.9],source_sha='0'*64,context={})
        wait_for(lambda:settled(editor) and editor._analysis_input is not None)
        assert '照片已变化' in editor.status and editor._layers==before
    finally:editor.close()


def test_wrong_close_up_content_ack_cannot_reach_ai(qt_app,ai_store,tmp_path,monkeypatch):
    from iphoto.controllers import object_grounding
    source,_=scene(tmp_path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        before=deepcopy(editor._layers)
        editor._active={'op':'object_crop','id':123,'generation':editor._generation,'context':{},
                        'content_reference':{'sha256':'a'*64}}
        response={'op':'object_crop','id':123,'generation':editor._generation,'ok':True,
                  'result':{'content_sha256':'b'*64}}
        monkeypatch.setattr(editor.process,'readAllStandardOutput',lambda:(json.dumps(response)+'\n').encode())
        monkeypatch.setattr(object_grounding,'crop_ready',lambda *args:pytest.fail('Wrong frame cannot reach AI'))
        worker_bridge._read(editor)
        assert editor._layers==before and '局部分析未应用' in editor.status
    finally:editor.close()


@pytest.mark.parametrize('kind',['matching','missing','wrong','raw-legacy','raw-wrong'])
def test_project_catalog_restores_only_with_matching_applied_content(qt_app,ai_store,tmp_path,monkeypatch,kind):
    from iphoto.document import read_project
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store);precache=[]
    if kind.startswith('raw'):layers=layers[:1]
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        prepare_editor(editor,layers)
        expected=key(editor._layers)
        editor._scene.set({'summary':'saved','objects':[{'id':'saved-target','name':'target','category':'object','mask':empty_mask(True)}]})
        saved=tmp_path/'catalog.iphoto';editor.saveProject(str(saved))
        wait_for(lambda:editor._project_save_future is None and saved.is_file())
        data=json.loads(saved.read_text(encoding='utf8'))
        if kind=='matching':assert data['scene_catalog']['content_key']==expected
        elif kind in ('missing','raw-legacy'):data['scene_catalog'].pop('content_key',None)
        else:data['scene_catalog']['content_key']='0'*64
        saved.write_text(json.dumps(data),encoding='utf8')
        monkeypatch.setattr(pixel_selections,'precache',lambda e,ids:precache.append(list(ids)))
        key([])  # Reopening must not rely on this process remembering a prior key.
        editor.openProject(str(saved));wait_for(lambda:settled(editor) and editor._project_path==str(saved.resolve()))
        assert editor._analysis_key==expected and editor._layers==read_project(saved)['layers']
        assert bool(editor._scene.catalog)==(kind in ('matching','raw-legacy'))
        assert bool(precache)==(kind in ('matching','raw-legacy'))
    finally:editor.close()


@pytest.mark.parametrize('case',['ordinary','draft','newer-render','newer-status'])
def test_prepared_face_jobs_recover_current_anchors_and_handoff_status_without_overwriting_newer_work(qt_app,ai_store,tmp_path,monkeypatch,case):
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store);held=[]
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        prepare_editor(editor,layers)
        image,ref=ContentCache(tmp_path).get(source,layers=layers);proxy=tmp_path/'prepared.png';image.convert('RGB').save(proxy)
        editor._status='后台预计算元素蒙版'
        data={'jobs':[{'id':'restored-face','mask_target':'face','recover_face_anchor':False},
                      {'id':'manual-face','mask_target':'face','recover_face_anchor':False}],
              'priority':'low','context':{'purpose':'precache','status_epoch':editor._status_epoch}}
        original=deepcopy(data)
        def request(op,**payload):
            if op=='analysis_input':
                editor._status_epoch+=1;payload['context']['status_epoch']=editor._status_epoch
            held.append({'op':op,**payload})
        monkeypatch.setattr(editor,'_request',request)
        analysis_input.defer(editor,'segment',data)
        active=held[0]
        if case=='draft':editor._candidate=empty_mask(True)
        elif case=='newer-render':editor._status_epoch+=1
        elif case=='newer-status':editor._status='用户刚开始的新操作';editor._status_epoch+=1
        epoch=editor._status_epoch
        result={'path':str(proxy),'proxy_sha256':sha256(proxy.read_bytes()).hexdigest(),
            'content_reference':ref,'source_sha':source.digest,'source_size':list(source.image.size),
            'content_key':key(layers),'faces':[{'id':'restored-face','name':'face','mask':empty_mask(True),
                                             'mask_target':'face','anchor':[.5,.5]}]}
        analysis_input.ready(editor,result,active)
        assert len(held)==2 and held[1]['op']=='segment' and data==original
        assert held[1]['jobs'][0]['recover_face_anchor'] is True
        assert held[1]['jobs'][1]['recover_face_anchor'] is False
        if case=='newer-status':
            assert editor._status=='用户刚开始的新操作' and held[1]['context']['status_epoch']!=editor._status_epoch
        else:
            assert held[1]['context']['status_epoch']==epoch
            assert editor._status==('图像内容已准备，已有范围保留' if case=='draft' else '后台预计算元素蒙版')
    finally:editor.close()
