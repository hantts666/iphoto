"""Native channel coverage remains an independent, selectable actual output."""
from copy import deepcopy
import json
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QTimer

from iphoto.controllers import channel_auto
from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.document import raster_mask, new_layer
from iphoto.engine import Recipe
from iphoto.matte_compare import parse_compare, compose_regions, render_comparison
from iphoto.matting import channels
from iphoto.masks import encode_bitmap
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_editor import settled
from test_channel_matting import scene, completion
from test_canvas_ui import canvas  # noqa: F401


def test_native_candidate_reuses_fields_and_one_model_without_mixing_alpha(monkeypatch):
    ticks=iter((10.,12.,12.5))
    monkeypatch.setattr(channels,'perf_counter',lambda:next(ticks))
    source,truth,mask,options=scene()
    original=deepcopy(mask)
    calls=[]
    fields=channels._fields
    def counted(*args):
        calls.append('fields')
        return fields(*args)
    monkeypatch.setattr(channels,'_fields',counted)
    def fuzzy(crop,guide,progress=None):
        calls.append('model')
        return np.where(guide==128,80,guide).astype('uint8'),1
    native={}
    result,quality=channels.estimate(source,mask,options,neural=fuzzy,native_candidate=native)
    actual=np.asarray(raster_mask(native['mask'],source.size))/255
    assert np.abs(actual-truth).max()<.004
    assert np.all(np.asarray(raster_mask(result,source.size))[60:70,110:120]==80)
    assert calls==['fields','model'] and mask==original
    assert quality['ai_refined'] and not native['quality']['ai_refined']
    assert native['quality']['tiles']==0 and native['quality']['backend']=='通道透明度'
    assert quality['elapsed_ms']==2000. and native['quality']['elapsed_ms']==2500.
    monkeypatch.setattr(channels,'perf_counter',lambda:13.)
    unused={}
    manual,_=channels.estimate(source,mask,{**options,'ai':False},native_candidate=unused)
    assert manual==native['mask'] and unused=={}


@pytest.mark.parametrize('names', [['native','channel'],['native','channel','hair']])
def test_native_choices_are_bound_to_the_actually_prepared_names(names):
    context={'comparison_candidates':names}
    chosen={'status':'select','candidate':'native','summary':'按实际像素选择纯通道'}
    assert parse_compare(completion(chosen),context)==chosen
    with pytest.raises(ValueError):
        parse_compare(completion(chosen),{'comparison_candidates':['channel','hair']})
    if len(names)==2:
        with pytest.raises(ValueError):
            parse_compare(completion({**chosen,'candidate':'hair'}),context)
    with pytest.raises(ValueError):
        parse_compare(completion({**chosen,'mask':{}}),context)
    payload=build_payload(AISettings.validated('openai','https://example.com/v1','vision'),
        '按实际输出选用','',[],'unused','matte_review',context)
    assert payload['response_format']['json_schema']['schema']['properties']['candidate']['enum']==[*names,'none']


def test_three_regional_choices_keep_exact_interiors_and_original_outside():
    _,_,seed,_=scene()
    size=(1000,600)
    masks={name:{**seed,'bitmap':encode_bitmap(Image.new('L',size,value),sampling='alpha',preserve_resolution=True)}
           for name,value in [('native',30),('channel',110),('hair',220)]}
    before=deepcopy(masks)
    boxes=[[10,20,310,320],[350,20,650,320],[680,20,980,320]]
    regions=[{'edge':i+1,'candidate':name} for i,name in enumerate(masks)]
    context={'comparison_candidates':list(masks),'regional_comparison':True,'edge_count':3}
    plan={'status':'select','candidate':'regional','regions':regions,'summary':'各区域使用已有候选'}
    assert parse_compare(completion(plan),context)==plan
    output=np.asarray(raster_mask(compose_regions(masks,boxes,regions,size),size))
    assert np.all(output[52:288,42:278]==30)
    assert np.all(output[52:288,382:618]==110)
    assert np.all(output[52:288,712:948]==220)
    assert np.all(output[350:]==110) and masks==before
    assert np.unique(output[160,10:42]).size>10
    protected=deepcopy(masks);protected['native']['semantic_target']='face_skin'
    with pytest.raises(ValueError,match='保护'):
        compose_regions(protected,boxes,regions,size)


def test_three_native_outputs_render_all_candidates_at_identical_coordinates(tmp_path):
    source,_,seed,options=scene()
    native,_=channels.estimate(source,seed,{**options,'ai':False})
    masks={'native':native,'channel':deepcopy(seed),'hair':deepcopy(seed)}
    layer=new_layer('颜色',False);layer['mask']=deepcopy(seed);layer['recipe']=Recipe(exposure=.25).to_dict()
    before=deepcopy((masks,layer))
    box=[0,0,*source.size]
    result=render_comparison(source,[layer],masks,tmp_path,'triple',target=layer['id'],boxes=[box])
    assert result['comparison_candidates']==['native','channel','hair'] and result['boxes']==[box]
    assert len(result['review_images'])==11
    for name in masks:
        assert any(f'候选 {name}' in item['label'] and item['lossless'] for item in result['review_images'])
        black=next(item for item in result['review_images'] if item['path'].endswith(f'-{name}-black-0.png'))
        assert Image.open(black['path']).size==source.size
    assert (masks,layer)==before


@pytest.mark.parametrize('outcome', ['native','draft_native','native_limited','reject','cancel','stale','reject_final'])
def test_real_channel_worker_native_selection_is_atomic(qt_app,ai_store,tmp_path,outcome):
    source,_,seed,options=scene()
    if outcome=='native_limited':
        pixels=np.array(source);pixels[60:65,50:54]=120;source=Image.fromarray(pixels)
    path=tmp_path/'source.png';source.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(seed);editor._load_layer();editor._commit()
        if outcome=='draft_native':
            editor._set_candidate(seed);wait_for(lambda:settled(editor))
        before=deepcopy((editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor))
        token=uuid4().hex
        state={'token':token,'generation':editor._generation,'candidate':deepcopy(editor._candidate),
               'target_id':editor._selection_target_id,'hair':False,'revision':0,'options':options,
               'bound':outcome!='draft_native','mask':deepcopy(seed)}
        if outcome=='native_limited':state['scope_limit']=deepcopy(seed)
        editor._pending_request={'text':'通道和AI比较透明织物','binding':editor._document_signature(),
            'layer_id':editor._selected,'layer_snapshot':deepcopy(editor._layers),'channel_auto':state}
        def server(payload):
            if '抠图候选比较员' in payload['messages'][0]['content']:
                context=json.loads(payload['messages'][1]['content'][0]['text'])
                assert context['comparison_candidates']==['native','channel']
                return completion({'status':'reject' if outcome=='reject' else 'select',
                    'candidate':'none' if outcome=='reject' else 'native','regions':[],
                    'summary':'受控比较，不是模型画质验收'})
            return completion({'status':'reject' if outcome=='reject_final' else 'accept',
                'summary':'受控最终事务检查','corrections':[]})
        with mock_api(server,delay=.15) as (endpoint,requests):
            configure(editor.ai,endpoint)
            channel_auto._extract(editor,state)
            wait_for(lambda:state.get('comparing') and editor.ai.busy,seconds=25)
            assert (editor._layers,editor._candidate)==before[:2]
            if outcome=='cancel':editor.selection.cancelTask()
            elif outcome=='stale':editor._generation+=1
            wait_for(lambda:not editor.busy and settled(editor),seconds=25)
        if outcome in ('native','draft_native','native_limited'):
            manual,_=channels.estimate(source,seed,{**options,'ai':False})
            if outcome=='native_limited':
                from iphoto.matting.scope import restrict
                assert np.any(np.asarray(raster_mask(manual,source.size))[60:65,50:54]>0)
                manual=restrict(manual,seed,source.size)
                assert np.all(np.asarray(raster_mask(manual,source.size))[60:65,50:54]==0)
            assert state['result']['mask']==manual
            assert state['result']['quality']['candidate_comparison']['selected']=='native'
            assert not state['result']['quality']['ai_refined']
            if outcome in ('native','native_limited'):
                assert editor._layers[-1]['mask']==manual and editor._cursor==before[2]+1
            else:
                assert editor._candidate==manual and editor._layers==before[0]
                assert editor._draft_cursor==before[4]+1
            project=tmp_path/'chosen.iphoto';editor.saveProject(str(project))
            wait_for(lambda:project.exists() and settled(editor))
            saved=json.loads(project.read_text(encoding='utf8'))
            assert saved['layers']==editor._layers
            output=tmp_path/'chosen.png';assert editor.exportRange(str(output),'mask')
            wait_for(lambda:output.exists() and settled(editor))
            assert Image.open(output).tobytes()==raster_mask(manual,source.size).tobytes()
            final=deepcopy((editor._layers,editor._candidate));editor.undo()
            assert (editor._layers,editor._candidate)==before[:2]
            editor.redo();assert (editor._layers,editor._candidate)==final
        else:
            assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor)==before
        assert not editor._pending_request and Image.open(path).tobytes()==source.tobytes()
    finally:
        editor.close()


def test_full_window_chat_can_choose_native_and_undo_without_an_extra_user_step(canvas,tmp_path,monkeypatch):  # noqa: F811
    from test_channel_matting import plan
    ui=canvas
    source,_,seed,options=scene()
    path=tmp_path/'source.png';source.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    ui.e._layer()['mask']=deepcopy(seed);ui.e._load_layer();ui.e._commit()
    before=deepcopy(ui.e._layers);cursor=ui.e._cursor
    native,native_quality=channels.estimate(source,seed,{**options,'ai':False})
    actual_request=ui.e._request
    def request(op,**data):
        if op=='matte' and data.get('method') in ('channel','hair'):
            quality={'warnings':[],'elapsed_ms':1.,'channel':'red','ai_refined':True}
            result={'mask':deepcopy(seed),'quality':quality}
            if data['method']=='channel':
                assert data['compare_native'] is True
                result['native_candidate']={'mask':native,'quality':native_quality}
            else:
                quality['edge_refinement']=True
            QTimer.singleShot(0,lambda:channel_auto.complete(ui.e,result,data['auto_token']))
            return
        return actual_request(op,**data)
    monkeypatch.setattr(ui.e,'_request',request)
    def server(payload):
        system=payload['messages'][0]['content']
        if '通道参数操作员' in system:
            return completion({'status':'keep','options':None,'summary':'受控参数'})
        if '抠图候选比较员' in system:
            context=json.loads(payload['messages'][1]['content'][0]['text'])
            assert context['comparison_candidates']==['native','channel','hair']
            return completion({'status':'select','candidate':'native','regions':[],'summary':'受控选择纯通道，继续检查'})
        if '独立抠图质量检查员' in system:
            return completion({'status':'accept','summary':'受控事务检查，不是自然照片画质验收','corrections':[]})
        result=plan();result['strategy']='hair_matte'
        return completion(result)
    with mock_api(server) as (endpoint,requests):
        configure(ui.e.ai,endpoint)
        ui.w.setProperty('chatOpen',True)
        ui.click('descriptionInput');ui.type('refine hair with channels');ui.click('applyDescriptionButton')
        wait_for(lambda:not ui.e.busy and settled(ui.e),seconds=25)
    assert len(requests)==4 and ui.e._layers[-1]['mask']==native
    assert ui.e._cursor==cursor+1 and '纯通道候选' in ui.e.selectionQuality
    ui.click('undoButton');wait_for(lambda:settled(ui.e))
    assert ui.e._layers==before
