"""A better channel result survives later hair refinement as one exact transaction."""
from copy import deepcopy
import json
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QTimer

from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.controllers import channel_auto
from iphoto.cutout import compose_cutout
from iphoto.document import new_layer, raster_mask, render_layers
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matte_compare import render_comparison
from iphoto.matte_review import parse_review
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_editor import settled
from test_matte_review import calculation_scene, completion
from test_canvas_ui import canvas  # noqa: F401


@pytest.mark.parametrize('change',[
    {'status':'accept'}, {'candidate':'other'}, {'candidate':'none'}, {'status':'reject'},
    {'summary':''}, {'corrections':[]}, {'recipe':{}}, {'candidate':1}])
def test_comparison_never_accepts_edits_or_an_unbound_candidate(change):
    context={'comparison_candidates':['channel','hair']}
    plan={'status':'select','candidate':'channel','summary':'细丝连续，另一份产生灰雾'}
    assert parse_review(completion(plan),context)==plan
    with pytest.raises(ValueError):parse_review(completion({**plan,**change}),context)
    with pytest.raises(ValueError):parse_review(completion(plan),{'comparison_candidates':['hair','channel']})
    rejected={'status':'reject','candidate':'none','summary':'两份都漏丝'}
    assert parse_review(completion(rejected),context)==rejected


def test_comparison_payload_has_only_labeled_pixels_and_selection_schema():
    pictures=[{'label':'channel 原像素','url':'data:image/png;base64,a'},
              {'label':'hair 原像素','url':'data:image/png;base64,b'}]
    context={'comparison_candidates':['channel','hair'],'review_images':pictures,'target':'头发'}
    body=build_payload(AISettings.validated('openai','https://example.com/v1','vision'),
                       '保留发丝',Recipe().to_dict(),[],'unused','matte_review',context)
    assert '不修改或混合' in body['messages'][0]['content']
    assert set(body['response_format']['json_schema']['schema']['properties'])=={'status','candidate','summary'}
    content=body['messages'][1]['content']
    assert [v['image_url']['url'] for v in content if v['type']=='image_url']==[v['url'] for v in pictures]
    assert json.loads(content[0]['text'])['comparison_candidates']==['channel','hair']


def test_qwen_format_retry_uses_json_mode_without_relaxing_the_local_parser():
    settings=AISettings.validated('qwen','https://example.com/v1','qwen3.8-max')
    for context in ({'comparison_candidates':['channel','hair']},{'revision':0,'point_budget':8}):
        first=build_payload(settings,'保留细丝',Recipe().to_dict(),[],'unused','matte_review',context)
        assert first['enable_thinking'] is True and 'response_format' not in first
        retry=build_payload(settings,'保留细丝',Recipe().to_dict(),[],'unused','matte_review',
                            {**context,'_validation_feedback':'局部抠图纠错点过多，原范围保留'})
        assert retry['enable_thinking'] is False and 'thinking_budget' not in retry
        assert retry['response_format']=={'type':'json_object'}
        assert '局部抠图纠错点过多' in retry['messages'][0]['content']


def candidates():
    source,truth,seed=calculation_scene()
    channel={**seed,'bitmap':encode_bitmap(Image.fromarray(np.rint(truth*255).astype('uint8')),
                                          sampling='alpha',preserve_resolution=True)}
    bad=np.rint(truth*255).astype('uint8');bad[30:70,120:200]=120;bad[90:130,140:190]=0
    hair={**seed,'bitmap':encode_bitmap(Image.fromarray(bad),sampling='alpha',preserve_resolution=True)}
    return source,seed,{'channel':channel,'hair':hair}


@pytest.mark.parametrize('bound',[False,True])
def test_native_comparison_uses_identical_boxes_and_each_actual_layer_mask(tmp_path,bound):
    source,seed,masks=candidates()
    layer=new_layer('颜色调整',False);layer['mask']=seed;layer['recipe']=Recipe(exposure=.35).to_dict()
    layers=[new_layer('原图',True),layer];before=deepcopy((layers,masks));source_bytes=source.tobytes()
    box=[0,0,source.width,source.height]
    result=render_comparison(source,layers,masks,tmp_path,'pair',target=layer['id'] if bound else None,boxes=[box])
    assert result['boxes']==[box] and result['comparison_candidates']==['channel','hair']
    labels=[v['label'] for v in result['review_images']]
    assert len(labels)==6 and 'channel' in labels[2] and 'hair' in labels[3]
    for key in ('channel','hair'):
        staged=deepcopy(layers)
        if bound:staged[-1]['mask']=masks[key]
        alpha=raster_mask(masks[key],source.size)
        expected=Image.alpha_composite(Image.new('RGBA',source.size,'white'),
                                       compose_cutout(render_layers(source,staged),alpha)).convert('RGB')
        path=next(item['path'] for item in result['images'] if item['path'].endswith(f'pair-{key}-white-0.png'))
        assert Image.open(path).tobytes()==expected.tobytes()
    assert (layers,masks)==before and source.tobytes()==source_bytes
    with pytest.raises(ValueError,match='已变化'):
        render_comparison(source,layers,masks,tmp_path,'stale',target='removed',boxes=[box])


@pytest.mark.parametrize('bound',[False,True])
@pytest.mark.parametrize('outcome',['channel','hair','reject','cancel','stale','reject_review'])
def test_real_comparison_workers_choose_exact_pixels_and_commit_once(qt_app,ai_store,tmp_path,bound,outcome):
    source,seed,masks=candidates();path=tmp_path/'original.png';source.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(seed);editor._layer()['recipe']=Recipe(exposure=.35).to_dict()
        editor._load_layer();editor._commit()
        if not bound:
            editor._set_candidate(seed);wait_for(lambda:settled(editor))
        before=deepcopy((editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor))
        token=uuid4().hex
        state={'token':token,'generation':editor._generation,'candidate':deepcopy(editor._candidate),
               'target_id':editor._selection_target_id,'hair':True,'hair_prepared':True,'revision':0,
               'bound':bound,'mask':deepcopy(seed),'result':{'mask':masks['channel'],
                   'quality':{'warnings':[],'elapsed_ms':1.,'channel':'red_green'}}}
        editor._pending_request={'text':'保留细丝','binding':editor._document_signature(),
                                 'layer_id':editor._selected,'layer_snapshot':deepcopy(editor._layers),'channel_auto':state}
        def server(payload):
            if '抠图候选比较员' in payload['messages'][0]['content']:
                return completion({'status':'reject' if outcome=='reject' else 'select',
                                   'candidate':'none' if outcome=='reject' else 'hair' if outcome=='hair' else 'channel',
                                   'regions':[],
                                   'summary':'控制候选选择，视觉质量另行判断'})
            return completion({'status':'reject' if outcome=='reject_review' else 'accept',
                               'summary':'控制最终事务检查','corrections':[]})
        with mock_api(server,delay=.15) as (endpoint,requests):
            configure(editor.ai,endpoint)
            channel_auto.complete(editor,{'mask':masks['hair'],'quality':{
                'warnings':[],'elapsed_ms':2.,'edge_refinement':True}},token)
            assert editor._candidate==before[1] and editor._layers==before[0]
            wait_for(lambda:state.get('comparing') and editor.ai.busy)
            assert '比较通道' in editor.ai.requestProgress and '可取消' in editor.ai.requestProgress
            if outcome=='cancel':editor.selection.cancelTask()
            elif outcome=='stale':editor._generation+=1
            wait_for(lambda:not editor.busy and settled(editor),seconds=25)
        if outcome in ('channel','hair'):
            assert state['result']['quality'].get('candidate_comparison'), (editor.status,editor._conversation)
            expected=masks[outcome]
            if bound:
                assert editor._layers[-1]['mask']==expected and editor._candidate is None
                assert editor._cursor==before[2]+1
                assert editor._layers[-1]['recipe']==before[0][-1]['recipe']
            else:
                assert editor._candidate==expected and editor._layers==before[0] and editor._cursor==before[2]
                assert editor._draft_cursor==before[4]+1
            exported=tmp_path/'chosen.png';assert editor.exportRange(str(exported),'mask')
            wait_for(lambda:exported.exists() and settled(editor))
            assert Image.open(exported).tobytes()==raster_mask(expected,source.size).tobytes()
            final=deepcopy((editor._layers,editor._candidate));editor.undo()
            assert (editor._layers,editor._candidate)==before[:2]
            editor.redo();assert (editor._layers,editor._candidate)==final
            assert state['result']['quality']['candidate_comparison']['selected']==outcome
            assert bool(state['result']['quality'].get('hair_refinement'))==(outcome=='hair')
            assert len(requests)==2
        else:
            assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor)==before
    finally:editor.close()


@pytest.mark.parametrize('tune_status',['keep','propose'])
def test_full_window_conversation_keeps_the_chosen_channel_result(canvas,tmp_path,monkeypatch,tune_status):  # noqa: F811
    from test_channel_matting import plan
    ui=canvas;source,seed,masks=candidates();path=tmp_path/'source.png';source.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    ui.e._layer()['mask']=deepcopy(seed);ui.e._layer()['recipe']=Recipe(exposure=.35).to_dict()
    ui.e._load_layer();ui.e._commit();before=deepcopy(ui.e._layers);cursor=ui.e._cursor
    actual_request=ui.e._request;channel_options=[]
    # Controlled solver outputs isolate UI/transaction behavior; real process
    # rendering, native evidence, networking and final mask publication run.
    def request(op,**data):
        if op=='matte' and data.get('method') in ('channel','hair'):
            key='channel' if data['method']=='channel' else 'hair'
            if key=='channel':channel_options.append(deepcopy(data['channel_options']))
            quality={'warnings':[],'elapsed_ms':1.,'channel':'red_green'}
            if key=='hair':quality['edge_refinement']=True
            QTimer.singleShot(0,lambda:channel_auto.complete(ui.e,{'mask':masks[key],'quality':quality},data['auto_token']))
            return
        return actual_request(op,**data)
    monkeypatch.setattr(ui.e,'_request',request)
    progress=[]
    ui.e.ai.progressChanged.connect(lambda:progress.append(ui.e.ai.requestProgress))
    def server(payload):
        system=payload['messages'][0]['content']
        if '通道参数操作员' in system:
            return completion({'status':tune_status,'options':None if tune_status=='keep' else {
                'channel':'blue','black':35,'white':210,'gamma':1.4,'invert':False},'summary':'控制通道参数操作'})
        if '抠图候选比较员' in system:
            return completion({'status':'select','candidate':'channel','regions':[],'summary':'控制选择通道'})
        if '独立抠图质量检查员' in system:
            return completion({'status':'accept','summary':'控制应用选中结果','corrections':[]})
        value=plan();value['strategy']='hair_matte';value['recipe']=Recipe(exposure=.35).to_dict()
        return completion(value)
    with mock_api(server,delay=.1) as (endpoint,requests):
        configure(ui.e.ai,endpoint)
        ui.w.setProperty('chatOpen',True)
        ui.click('descriptionInput');ui.type('refine hair with channels');ui.click('applyDescriptionButton')
        wait_for(lambda:not ui.e.busy and settled(ui.e),seconds=25)
    assert len(requests)==4 and ui.e._layers[-1]['mask']==masks['channel']
    assert ui.e._cursor==cursor+1 and ui.e._layers[-1]['recipe']==before[-1]['recipe']
    assert len(channel_options)==1 and channel_options[0]['radius']==32
    if tune_status=='propose':assert channel_options[0]['channel']=='blue' and channel_options[0]['gamma']==1.4
    assert any('比较通道与 AI 候选' in value and '可取消' in value for value in progress)
    assert any('比较原像素通道参数' in value and '可取消' in value for value in progress)
    ui.click('undoButton');wait_for(lambda:settled(ui.e))
    assert ui.e._layers==before
