"""Local candidate choices retain native pixels and require actual output review."""
from copy import deepcopy
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QTimer

from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.controllers import channel_auto
from iphoto.cutout import compose_cutout
from iphoto.document import empty_mask, raster_mask, render_layers
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matte_compare import compose_regions, parse_compare, validate_regions
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_matte_compare import candidates
from test_matte_review import completion


def mask(alpha):
    return {**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(alpha),sampling='alpha',preserve_resolution=True)}


def choices(count):
    return [{'edge':i+1,'candidate':'hair' if i%2 else 'channel'} for i in range(count)]


def large_candidates():
    source,seed,masks=candidates();size=(1280,800)
    seed=mask(np.asarray(raster_mask(seed,source.size).resize(size,Image.Resampling.NEAREST)))
    masks={key:mask(np.asarray(raster_mask(value,source.size).resize(size,Image.Resampling.NEAREST)))
           for key,value in masks.items()}
    return source.resize(size,Image.Resampling.NEAREST),seed,masks


@pytest.mark.parametrize('change',[
    {'regions':[]}, {'regions':[{'edge':1,'candidate':'hair'},{'edge':1,'candidate':'channel'}]},
    {'regions':[{'edge':True,'candidate':'hair'},{'edge':2,'candidate':'channel'}]},
    {'regions':[{'edge':0,'candidate':'hair'},{'edge':2,'candidate':'channel'}]},
    {'regions':[{'edge':1,'candidate':'hair'},{'edge':3,'candidate':'channel'}]},
    {'regions':[{'edge':1,'candidate':'hair'},{'edge':2,'candidate':'hair'}]},
    {'regions':[{'edge':1,'candidate':'none'},{'edge':2,'candidate':'channel'}]},
    {'regions':[{'edge':1,'candidate':'hair','box':[0,0,3,4]},{'edge':2,'candidate':'channel'}]},
    {'regions':[{'edge':1,'candidate':'hair','radius':1},{'edge':2,'candidate':'channel'}]},
    {'candidate':'channel'}, {'status':'reject'}, {'corrections':[]}, {'summary':''}])
def test_regional_choice_requires_complete_bound_edges_and_no_free_controls(change):
    plan={'status':'select','candidate':'regional','regions':choices(2),'summary':'分别核对两处真实细丝'}
    context={'comparison_candidates':['channel','hair'],'regional_comparison':True,'edge_count':2}
    assert parse_compare(completion(plan),context)==plan
    with pytest.raises(ValueError):parse_compare(completion({**plan,**change}),context)
    with pytest.raises(ValueError):parse_compare(completion(plan),{**context,'regional_comparison':False})
    for value in (None,True,0,5):
        with pytest.raises(ValueError):validate_regions(plan['regions'],value)
    whole={**plan,'candidate':'channel','regions':[]}
    assert parse_compare(completion(whole),context)==whole
    rejected={**plan,'status':'reject','candidate':'none','regions':[]}
    assert parse_compare(completion(rejected),context)==rejected


def test_regional_payload_exposes_numbered_choices_without_free_geometry():
    context={'comparison_candidates':['channel','hair'],'regional_comparison':True,'edge_count':2,
             'review_images':[{'label':'同位置原像素','url':'data:image/png;base64,a'}]}
    for provider,model in (('openai','vision'),('qwen','qwen3.8-max')):
        body=build_payload(AISettings.validated(provider,'https://example.com/v1',model),
                           '头发抠图',Recipe().to_dict(),[],'unused','matte_review',context)
        prompt=body['messages'][0]['content']
        assert '{status,candidate,regions,summary}' in prompt and '{status,candidate,summary}' not in prompt
        assert '32原像素' in prompt and '重新渲染和检查' in prompt
        assert json_workspace(body)['edge_count']==2
        if provider=='openai':
            schema=body['response_format']['json_schema']['schema']
            assert set(schema['properties'])=={'status','candidate','regions','summary'}
            assert set(schema['properties']['regions']['items']['properties'])=={'edge','candidate'}


def json_workspace(body):
    import json
    return json.loads(body['messages'][1]['content'][0]['text'])


def test_composition_exact_interiors_continuous_seams_and_unchanged_outside():
    yy,xx=np.indices((600,1000));a=((xx+yy)%170+15).astype('uint8');b=(245-(xx+yy)%150).astype('uint8')
    masks={'channel':mask(a),'hair':mask(b)};before=deepcopy(masks)
    boxes=[[20,30,420,430],[500,100,900,500]];regions=choices(2)
    result=compose_regions(masks,boxes,regions,(1000,600));out=np.asarray(raster_mask(result,(1000,600)))
    expected_outside=np.ones(a.shape,bool);expected_outside[100:500,500:900]=False
    assert np.array_equal(out[expected_outside],a[expected_outside])
    assert np.array_equal(out[132:468,532:868],b[132:468,532:868])
    # Independent scalar smoothstep oracle at a non-corner seam. The selected
    # candidate's interior is exact; only its bounded seam blends gray alpha.
    for distance in range(1,33):
        x,y=499+distance,300;t=distance/32;w=t*t*(3-2*t)
        assert out[y,x]==round(float(a[y,x])+w*(float(b[y,x])-float(a[y,x])))
    assert np.unique(out[100:132,600]).size>10
    assert masks==before
    assert compose_regions(masks,boxes,list(reversed(regions)),(1000,600))==result


def test_overlap_ownership_is_deeper_region_then_lower_number():
    masks={'channel':mask(np.full((400,600),40,'uint8')),'hair':mask(np.full((400,600),200,'uint8'))}
    boxes=[[0,0,400,400],[100,0,500,400]]
    a=np.asarray(raster_mask(compose_regions(masks,boxes,choices(2),(600,400)),(600,400)))
    assert a[200,200]==40 and a[200,350]==200
    # At x=249 the first box is one pixel deeper. The replacement starts at
    # x=250, with a 32px seam inside the second region, without shifting pixels.
    assert a[200,249]==40 and a[200,250]==40 and a[200,282]==200
    same=[[0,0,400,400],[0,0,400,400]]
    assert np.all(np.asarray(raster_mask(compose_regions(masks,same,choices(2),(600,400)),(600,400)))==40)
    reversed_candidates=[{'edge':1,'candidate':'hair'},{'edge':2,'candidate':'channel'}]
    tie=np.asarray(raster_mask(compose_regions(masks,same,reversed_candidates,(600,400)),(600,400)))
    assert tie[200,200]==200


def test_composition_retains_color_policy_and_rejects_excessive_spans_before_allocation():
    masks={'channel':mask(np.full((400,600),40,'uint8')),'hair':mask(np.full((400,600),200,'uint8'))}
    for value in masks.values():value['color_recovery']=True;value['semantic_target']='face'
    result=compose_regions(masks,[[0,0,400,400],[200,0,600,400]],choices(2),(600,400))
    assert result['color_recovery'] is True and result['semantic_target']=='face'
    with pytest.raises(ValueError,match='跨度过大'):
        compose_regions(masks,[[0,0,512,512],[3488,3488,4000,4000]],choices(2),(4000,4000))


@pytest.mark.parametrize('change',[
    {'boxes':[[0,0,513,400],[200,0,600,400]]},
    {'boxes':[[0,0,400,400],[200,0,601,400]]},
    {'boxes':[[0,0,400,400],[True,0,400,400]]},
    {'boxes':None}, {'regions':[{'edge':1,'candidate':'hair'}]},
    {'policy':'semantic_target'}, {'policy':'color_recovery'}])
def test_composition_rejects_unbound_geometry_or_different_protection_policies(change):
    masks={'channel':mask(np.full((400,600),40,'uint8')),'hair':mask(np.full((400,600),200,'uint8'))}
    boxes=[[0,0,400,400],[200,0,600,400]];regions=choices(2)
    if 'policy' in change:
        # Both masks are independently valid, but cannot share a composition
        # if their target/protection/foreground-color policy differs.
        if change['policy']=='semantic_target':masks['hair']['semantic_target']='face'
        else:masks['hair']['color_recovery']=True
    with pytest.raises(ValueError):compose_regions(masks,change.get('boxes',boxes),change.get('regions',regions),(600,400))


@pytest.mark.parametrize('bound,outcome',[(False,'accept'),(True,'accept'),(True,'reject_review'),
                                         (True,'cancel'),(True,'stale'),(True,'hash_failure')])
def test_regional_worker_reviews_same_pixels_and_only_commits_after_acceptance(qt_app,ai_store,tmp_path,monkeypatch,bound,outcome):
    source,seed,masks=large_candidates();path=tmp_path/'source.png';source.save(path);original_bytes=path.read_bytes()
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(seed);editor._layer()['recipe']=Recipe(exposure=.35).to_dict()
        editor._load_layer();editor._commit()
        if not bound:editor._set_candidate(seed);wait_for(lambda:settled(editor))
        before=deepcopy((editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor))
        token=uuid4().hex
        state={'token':token,'generation':editor._generation,'candidate':deepcopy(editor._candidate),
               'target_id':editor._selection_target_id,'hair':True,'hair_prepared':True,'revision':0,
               'bound':bound,'mask':deepcopy(seed),'result':{'mask':masks['channel'],
                   'quality':{'warnings':[],'elapsed_ms':1.,'channel':'red_green'}}}
        editor._pending_request={'text':'保留细丝','binding':editor._document_signature(),
                                 'layer_id':editor._selected,'layer_snapshot':deepcopy(editor._layers),'channel_auto':state}
        actual_request=editor._request;compositions=[];reviews=[]
        def request(op,**data):
            if op=='matte_candidate' and 'composition_regions' in data:
                assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor)==before
                compositions.append(deepcopy(data))
                assert '可取消' in editor.status
                if outcome=='hash_failure':data['expected_sha256']='old'
                value=actual_request(op,**data)
                if outcome=='cancel':editor.selection.cancelTask()
                elif outcome=='stale':editor._generation+=1
                return value
            return actual_request(op,**data)
        monkeypatch.setattr(editor,'_request',request)
        original_plan=editor.ai.plan
        def plan(*args,**kwargs):
            if args[5]=='matte_review':reviews.append(deepcopy(args[6]))
            return original_plan(*args,**kwargs)
        monkeypatch.setattr(editor.ai,'plan',plan)
        def server(payload):
            if '抠图候选比较员' in payload['messages'][0]['content']:
                count=json_workspace(payload)['edge_count'];assert count>=2
                return completion({'status':'select','candidate':'regional','regions':choices(count),'summary':'受控逐处选择，不能证明画质'})
            return completion({'status':'reject' if outcome=='reject_review' else 'accept',
                               'summary':'受控实际输出检查','corrections':[]})
        with mock_api(server,delay=.1) as (endpoint,requests):
            configure(editor.ai,endpoint)
            channel_auto.complete(editor,{'mask':masks['hair'],'quality':{
                'warnings':[],'elapsed_ms':2.,'edge_refinement':True}},token)
            wait_for(lambda:not editor.busy and settled(editor),seconds=40)
        assert len(compositions)==1,(editor.status,editor._conversation)
        composition=compositions[0];assert path.read_bytes()==original_bytes
        if outcome=='accept':
            expected=compose_regions(masks,composition['review_boxes'],composition['composition_regions'],source.size)
            if bound:
                assert editor._layers[-1]['mask']==expected and editor._candidate is None and editor._cursor==before[2]+1
                assert editor._layers[-1]['recipe']==before[0][-1]['recipe']
            else:
                assert editor._candidate==expected and editor._layers==before[0] and editor._cursor==before[2]
                assert editor._draft_cursor==before[4]+1
            assert state['review_boxes']==composition['review_boxes'] and len(reviews)==2 and len(requests)==2
            assert reviews[1]['edge_count']==len(composition['review_boxes'])
            assert state['result']['quality']['candidate_comparison']['selected']=='regional'
            exported=tmp_path/'chosen.png';assert editor.exportRange(str(exported),'cutout')
            wait_for(lambda:exported.exists() and settled(editor))
            actual=Image.open(exported)
            assert actual.mode=='RGBA' and actual.getchannel('A').tobytes()==raster_mask(expected,source.size).tobytes()
            expected_cutout=compose_cutout(render_layers(source,editor._layers),raster_mask(expected,source.size))
            assert actual.tobytes()==expected_cutout.tobytes()
            final=deepcopy((editor._layers,editor._candidate));editor.undo();assert (editor._layers,editor._candidate)==before[:2]
            editor.redo();assert (editor._layers,editor._candidate)==final
        else:assert (editor._layers,editor._candidate,editor._cursor,editor._draft_history,editor._draft_cursor)==before
    finally:editor.close()


def test_full_qml_conversation_composes_regions_and_undoes_once(canvas,tmp_path,monkeypatch):  # noqa: F811
    from test_channel_matting import plan
    ui=canvas;source,seed,masks=large_candidates();path=tmp_path/'source.png';source.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    ui.e._layer()['mask']=deepcopy(seed);ui.e._layer()['recipe']=Recipe(exposure=.35).to_dict()
    ui.e._load_layer();ui.e._commit();before=deepcopy(ui.e._layers);cursor=ui.e._cursor
    actual_request=ui.e._request;composition=[]
    def request(op,**data):
        if op=='matte' and data.get('method') in ('channel','hair'):
            key='channel' if data['method']=='channel' else 'hair'
            quality={'warnings':[],'elapsed_ms':1.,'channel':'red_green'}
            if key=='hair':quality['edge_refinement']=True
            QTimer.singleShot(0,lambda:channel_auto.complete(ui.e,{'mask':masks[key],'quality':quality},data['auto_token']))
            return
        if op=='matte_candidate' and 'composition_regions' in data:composition.append(deepcopy(data))
        return actual_request(op,**data)
    monkeypatch.setattr(ui.e,'_request',request)
    def server(payload):
        system=payload['messages'][0]['content']
        if '通道参数操作员' in system:return completion({'status':'keep','options':None,'summary':'受控保持参数'})
        if '抠图候选比较员' in system:
            return completion({'status':'select','candidate':'regional','regions':choices(json_workspace(payload)['edge_count']),
                               'summary':'受控逐处选择'})
        if '独立抠图质量检查员' in system:return completion({'status':'accept','summary':'受控通过事务','corrections':[]})
        result=plan();result['strategy']='hair_matte';result['recipe']=Recipe(exposure=.35).to_dict();return completion(result)
    with mock_api(server,delay=.1) as (endpoint,requests):
        configure(ui.e.ai,endpoint);ui.w.setProperty('chatOpen',True)
        ui.click('descriptionInput');ui.type('refine hair with channels');ui.click('applyDescriptionButton')
        wait_for(lambda:not ui.e.busy and settled(ui.e),seconds=45)
    assert len(composition)==1 and len(requests)==4,(ui.e.status,ui.e._conversation)
    expected=compose_regions(masks,composition[0]['review_boxes'],composition[0]['composition_regions'],source.size)
    assert ui.e._layers[-1]['mask']==expected and ui.e._cursor==cursor+1
    assert '按边缘分别选用' in ui.e.selectionQuality and ui.e._layers[-1]['recipe']==before[-1]['recipe']
    ui.click('undoButton');wait_for(lambda:settled(ui.e));assert ui.e._layers==before
