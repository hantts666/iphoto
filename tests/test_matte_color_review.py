"""Focused color evidence and the additional pre-commit quality boundary."""
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
import pytest

from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.cutout import color_patch,compose_cutout
from iphoto.document import new_layer,raster_mask
from iphoto.engine import Recipe
from iphoto.matte_color import PROMPT
from iphoto.matte_review import parse_review,render_review
from iphoto.workspace import Editor
from test_ai import configure,mock_api,wait_for
from test_channel_matting import completion,plan,scene
from test_cutout_colors import scene as color_scene
from test_editor import settled


def test_color_evidence_is_actual_native_straight_rgb_with_an_exact_black_composite(tmp_path):
    source,mask,_,_=color_scene()
    before=source.tobytes(),deepcopy(mask)
    layers=[new_layer('原图',True)]
    evidence=render_review(source,layers,mask,tmp_path,1)
    alpha=raster_mask(mask,source.size)
    rgba=compose_cutout(source,alpha,color_patch(source,layers,mask,alpha))
    panels=evidence['color_review_images']
    assert len(panels)==2*len(evidence['boxes']) and panels
    for index in range(0,len(panels),2):
        panel,black=panels[index:index+2]
        box=panel['box'];w,h=box[2]-box[0],box[3]-box[1]
        assert w<=256 and h<=256 and black['box']==box
        pixels=Image.open(panel['path'])
        assert pixels.size==(w*2,h+24)
        assert pixels.crop((0,24,w,h+24)).tobytes()==source.crop(box).tobytes()
        part=rgba.crop(box)
        expected=Image.composite(part.convert('RGB'),Image.new('RGB',part.size,'black'),part.getchannel('A').point([0]*32+[255]*224))
        assert pixels.crop((w,24,w*2,h+24)).tobytes()==expected.tobytes()
        expected_black=Image.alpha_composite(Image.new('RGBA',part.size,'black'),part).convert('RGB')
        assert Image.open(black['path']).tobytes()==expected_black.tobytes()
        assert all(v['path'] in {i['path'] for i in evidence['images']} for v in (panel,black))
    assert (source.tobytes(),mask)==before
    assert render_review(source,layers,{**mask,'color_recovery':False},tmp_path,2)['color_review_images']==[]


def test_color_phase_has_a_short_separate_prompt_and_cannot_plan_corrections():
    settings=AISettings.validated('openai','https://example.com/v1','vision')
    context={'color_only':True,'revision':1,'correction_available':False,'target':'蓝色物体',
             'keep_candidates':{'1':[[10,20]]},'point_bounds':{'1':[80,80,920,920]},
             'review_images':[{'label':'颜色对照','url':'data:image/png;base64,a'}]}
    body=build_payload(settings,'检查实际前景颜色',Recipe().to_dict(),[],'unused','matte_review',context)
    assert body['messages'][0]['content']==PROMPT
    schema=body['response_format']['json_schema']['schema']
    assert schema['properties']['status']['enum']==['accept','reject','uncertain']
    assert schema['properties']['corrections']['maxItems']==0
    sent=json.loads(body['messages'][1]['content'][0]['text'])
    assert sent=={'request':'检查实际前景颜色','mode':'matte_review','target':'蓝色物体','revision':1,'color_only':True}
    for status in ('accept','reject','uncertain'):
        assert parse_review(completion({'status':status,'summary':'颜色检查','corrections':[]}),context)['status']==status
    with pytest.raises(ValueError,match='前景颜色检查'):
        parse_review(completion({'status':'revise','summary':'非法取点','corrections':[]}),{**context,'revision':0,'correction_available':True})


@pytest.mark.parametrize('outcome',['accept','reject','uncertain','cancel','stale','invalid','geometry_reject','missing_evidence'])
def test_real_worker_color_check_precedes_geometry_and_never_commits_alone(qt_app,ai_store,tmp_path,outcome):
    image,_,mask,_=scene();path=tmp_path/'source.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(mask);editor._load_layer();editor._commit()
        before=deepcopy((editor._layers,editor._candidate,editor._cursor))
        phases=[]
        def server(payload):
            context=json.loads(payload['messages'][1]['content'][0]['text'])
            if context.get('color_only'):
                phases.append('color')
                assert (editor._layers,editor._candidate,editor._cursor)==before
                assert '核对前景颜色与串色' in editor.ai.requestProgress and '可取消' in editor.ai.requestProgress
                if outcome=='missing_evidence':
                    path=editor._pending_request['channel_auto']['review_evidence']['review_images'][0]['path']
                    Path(path).unlink()
                if outcome=='invalid':return completion({'status':'revise','summary':'串色不能用点修','corrections':[]})
                return completion({'status':outcome if outcome in ('reject','uncertain') else 'accept','summary':'受控颜色检查','corrections':[]})
            if context.get('mode')=='matte_review':
                phases.append('geometry')
                assert (editor._layers,editor._candidate,editor._cursor)==before
                return completion({'status':'reject' if outcome=='geometry_reject' else 'accept','summary':'受控范围检查','corrections':[]})
            return completion(plan())
        with mock_api(server,delay=.12) as (endpoint,requests):
            configure(editor.ai,endpoint)
            assert editor.sendMessage('结合通道和AI修边','auto')
            if outcome in ('cancel','stale'):
                wait_for(lambda:(editor._pending_request or {}).get('channel_auto',{}).get('color_reviewing'))
                if outcome=='cancel':editor.selection.cancelTask()
                else:editor._generation+=1
            wait_for(lambda:not editor.busy and settled(editor),seconds=45)
        if outcome=='accept':
            assert phases==['color','geometry'] and editor._cursor==before[2]+1
            after=deepcopy(editor._layers);editor.undo();assert editor._layers==before[0]
            editor.redo();assert editor._layers==after
        else:
            assert (editor._layers,editor._candidate,editor._cursor)==before
            if outcome=='geometry_reject':assert phases==['color','geometry']
            else:assert 'geometry' not in phases
        assert not editor.aiChannelPreparing
    finally:editor.close()
