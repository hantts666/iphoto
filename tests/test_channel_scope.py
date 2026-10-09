"""Semantic filtering is bounded before channels and after native matting."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_protocol import parse_auto
from iphoto.document import empty_mask, new_layer, raster_mask, MAX_LAYERS
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matting.scope import restrict
from iphoto.paths import ROOT
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_channel_matting import completion, plan, scene
from test_editor import settled


def target():
    return {'name':'左侧织物','reason':'排除右侧误选对象','mask_target':'object',
            'face_scope':'full','face_part':'all','parts':[],
            'box':[220,180,680,820],'point':[360,450],'recipe':Recipe().to_dict()}


@pytest.mark.parametrize('scope,current',[('current_layer','local'),('current_selection','selection')])
def test_bounded_channel_target_can_be_located_without_new_layers(scope,current):
    value={**plan(scope),'regions':[target()]}
    result=parse_auto(completion(value),Recipe().to_dict(),[],current_scope=current,
                      workspace={'channel_mask_available':True,'max_new_layers':0})
    assert result['scope']==scope and result['regions'][0]['name']=='左侧织物'
    assert 'bitmap' not in result['regions'][0]['mask']


@pytest.mark.parametrize('change',[
    {'regions':None},{'regions':{}},{'regions':[target(),target()]},
    {'regions':[{**target(),'recipe':Recipe(exposure=.1).to_dict()}]},
    {'regions':[{**target(),'mask_target':'face_skin'}]},
    {'scope':'whole_image'},
])
def test_channel_filter_cannot_expand_to_global_or_smuggle_other_edits(change):
    with pytest.raises(ValueError):
        parse_auto(completion({**plan(),'regions':[target()],**change}),Recipe().to_dict(),[],
                   current_scope='local',workspace={'channel_mask_available':True})


def test_native_upper_bound_keeps_gray_coverage_holes_and_semantic_identity():
    width,height=133,91
    limit={**empty_mask(True),'semantic_target':'face_skin',
           'face_binding':{'face_id':'same-face','source_sha256':'a'*64},
           'ops':[{'kind':'rect','mode':'subtract','points':[[.3,.2],[.45,.8]]}],
           'feather':.01,'edge_shift':-1}
    ramp=np.broadcast_to(np.arange(width,dtype=np.uint8)*1,(height,width))
    proposed={**empty_mask(),'label':'filtered target','color_recovery':True,
              'bitmap':encode_bitmap(Image.fromarray(ramp),sampling='alpha',preserve_resolution=True)}
    before=deepcopy((proposed,limit))
    actual=restrict(proposed,limit,(width,height))
    alpha=np.asarray(raster_mask(actual,(width,height)))
    allowed=np.asarray(raster_mask(limit,(width,height)))
    assert np.all(alpha<=allowed) and np.all(alpha<=ramp)
    assert np.array_equal(alpha[allowed==255],ramp[allowed==255])
    assert np.array_equal(alpha[ramp>=allowed],allowed[ramp>=allowed])
    assert (alpha[allowed==0]==0).all() and ((alpha>0)&(alpha<255)).any()
    assert actual['face_binding']==limit['face_binding'] and actual['semantic_target']=='face_skin'
    assert actual['color_recovery'] and actual.get('edge_shift',0)==0 and actual['feather']==0
    assert actual['bitmap']['width']==width and actual['bitmap']['height']==height
    assert raster_mask(restrict(actual,limit,(width,height)),(width,height)).tobytes()==alpha.tobytes()
    assert (proposed,limit)==before


def test_disjoint_target_does_not_silently_erase_existing_selection():
    first={**empty_mask(),'ops':[{'kind':'rect','mode':'add','points':[[.1,.1],[.3,.9]]}]}
    second={**empty_mask(),'ops':[{'kind':'rect','mode':'add','points':[[.7,.1],[.9,.9]]}]}
    with pytest.raises(ValueError,match='原范围保留'):restrict(first,second,(160,100))


def test_actual_matte_worker_preserves_original_continuous_limit(tmp_path):
    image,_,mask,options=scene();source=tmp_path/'source.png';image.save(source)
    original=np.asarray(raster_mask(mask,image.size)).copy()
    original[:,110:]=0
    original[60:80,85:105]=74
    limit={**empty_mask(),'label':'original allowed coverage',
           'bitmap':encode_bitmap(Image.fromarray(original),sampling='alpha',preserve_resolution=True)}
    request={'id':7,'op':'matte','generation':2,'method':'channel','source_path':str(source),
             'source_sha':hashlib.sha256(source.read_bytes()).hexdigest(),'mask':mask,
             'channel_options':{**options,'ai':False},'scope_limit':limit}
    child=subprocess.run([sys.executable,str(ROOT/'run.py'),'--matte-worker'],
                         input=json.dumps(request)+'\n',capture_output=True,text=True,encoding='utf8',timeout=30)
    assert child.returncode==0
    final=[json.loads(line) for line in child.stdout.splitlines() if line.startswith('{')][-1]
    assert final['ok'] and final['result']['quality']['scope_limited']
    actual=np.asarray(raster_mask(final['result']['mask'],image.size))
    assert (actual[:,110:]==0).all() and (actual[original==0]==0).all()
    assert (actual<=original).all() and (actual[60:80,85:95]==74).all()
    assert final['result']['quality']['partial_pixels']==int(((actual>0)&(actual<255)).sum())


@pytest.mark.parametrize('scope',['current_layer','current_selection'])
@pytest.mark.parametrize('outcome',['complete','reject','cancel','stale','stale_matte','cancel_review','failure'])
def test_filter_and_real_channels_remain_pending_until_review(qt_app,ai_store,tmp_path,monkeypatch,pixel_protocol_stub,scope,outcome):
    # The box bitmap is a declared segmentation protocol fixture. Channels,
    # neural matting, worker transport and review transaction are real; the
    # natural-photo original application QA exercises real segmentation too.
    image,_,mask,_=scene();path=tmp_path/'source.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(mask)
        editor._layer()['recipe']=Recipe(exposure=.3,contrast=7).to_dict()
        editor._load_layer();editor._commit()
        if scope=='current_selection':
            editor.beginSelection('current');wait_for(lambda:settled(editor))
        before=deepcopy((editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor))
        original=editor._request;prepared=[]
        def request(op,**data):
            if op=='channel_preview' and data.get('initial'):
                assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor)==before
                assert data['scope_limit']==mask
                prepared.append(1)
            return original(op,**data)
        monkeypatch.setattr(editor,'_request',request)
        def server(payload):
            if '前景颜色检查员' in payload['messages'][0]['content']:
                return completion({'status':'accept','summary':'受控颜色通过','corrections':[]})
            if '通道参数操作员' in payload['messages'][0]['content']:
                return completion({'status':'keep','options':None,'summary':'继续检查实际透明输出'})
            if '独立抠图质量检查员' in payload['messages'][0]['content']:
                return completion({'status':'reject' if outcome=='reject' else 'accept','summary':'受控事务结果'})
            return completion({**plan(scope),'recipe':dict(editor._recipe),'regions':[target()]})
        with mock_api(server) as (endpoint,requests):
            configure(editor.ai,endpoint)
            assert editor.sendMessage('只保留左侧薄纱，排除右侧对象，颜色保持','auto')
            wait_for(lambda:bool(prepared))
            if outcome=='cancel':editor.selection.cancelTask()
            elif outcome=='stale':editor._generation+=1
            elif outcome=='failure':path.unlink()
            elif outcome=='stale_matte':
                wait_for(lambda:editor.matteBusy);editor._generation+=1
            elif outcome=='cancel_review':
                wait_for(lambda:(editor._pending_request or {}).get('channel_auto',{}).get('reviewing'))
                editor.selection.cancelTask()
            wait_for(lambda:not editor.busy and settled(editor),seconds=45)
        if outcome=='complete':
            assert len(editor._layers)==len(before[0]) and editor._cursor==before[2]+1,editor.status
            assert editor._layer()['recipe']==before[0][-1]['recipe']
            actual=np.asarray(raster_mask(editor._layer()['mask'],image.size))
            allowed=np.asarray(raster_mask(mask,image.size))
            assert (actual<=allowed).all() and not actual[:,170:].any() and actual[:,65:95].any()
            changed=deepcopy(editor._layers);editor.undo();assert editor._layers==before[0]
            editor.redo();assert editor._layers==changed
        else:
            assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor)==before
        if outcome in ('complete','reject'):
            assert len(requests)==4 and '独立抠图质量检查员' in requests[-1][2]['messages'][0]['content']
        assert prepared==[1] and not editor.aiChannelPreparing
    finally:editor.close()


def test_filter_existing_layer_is_available_at_layer_limit(qt_app,ai_store,tmp_path,pixel_protocol_stub,monkeypatch):
    from iphoto.controllers import channel_auto
    from iphoto.ai_tasks import parse_regions
    image,_,mask,_=scene();path=tmp_path/'source.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layers.extend(new_layer('existing') for _ in range(MAX_LAYERS-1))
        editor._layer()['mask']=deepcopy(mask);editor._commit()
        before=deepcopy(editor._layers)
        editor._pending_request={'text':'只保留左侧目标','binding':editor._document_signature(),
                                 'layer_snapshot':deepcopy(editor._layers),'layer_id':editor._selected}
        regions=parse_regions(completion({'status':'planned','summary':'过滤','regions':[target()]}))['regions']
        calls=[]
        monkeypatch.setattr(channel_auto,'prepare',lambda e,mask,token:calls.append(mask))
        channel_auto.begin(editor,{'scope':'current_layer','summary':'过滤','regions':regions})
        assert calls and editor._layers==before and editor._candidate is None
    finally:editor.close()
