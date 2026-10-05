from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.engine import Recipe
from iphoto.matte_points import parse_points,render_points
from iphoto.matte_review import point_bounds,point_frame


def completion(result):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}


def workspace():
    return {'correction_method':'hair','revision':0,'context_points':True,'edge_count':1,
            'point_bounds':{'1':[195,195,804,804]},'keep_candidates':{'1':[[100,500]]},
            'corrections':[{'edge':1,'radius':36,'points':[[100,500,1],[700,700,0],[600,250,2]]}]}


@pytest.mark.parametrize('status',['keep','reject','uncertain'])
def test_point_identity_decision_cannot_claim_a_finished_cutout_or_edit(status):
    original=workspace();snapshot=deepcopy(original)
    result={'status':status,'summary':'原片十字中心为背景','corrections':[]}
    assert parse_points(completion(result),original)==result and original==snapshot
    for change in ({'status':'accept'},{'corrections':original['corrections']},{'recipe':Recipe().to_dict()},{'summary':''}):
        with pytest.raises(ValueError):parse_points(completion({**result,**change}),original)
    for change in ({'revision':1},{'correction_method':'semantic'},{'corrections':[]}):
        with pytest.raises(ValueError):parse_points(completion(result),{**original,**change})


def test_only_existing_strand_points_can_move_or_be_removed():
    context=workspace();snapshot=deepcopy(context)
    revised=deepcopy(context['corrections']);revised[0]['points'][-1]=[398,263,2]
    result={'status':'revise','summary':'T从绿色背景移到可见发丝','corrections':revised}
    assert parse_points(completion(result),context)==result
    dropped=deepcopy(revised);dropped[0]['points'].pop()
    assert parse_points(completion({**result,'corrections':dropped}),context)['corrections']==dropped
    for wrong in (
        [{'edge':2,'radius':36,'points':revised[0]['points']}],
        [{'edge':1,'radius':40,'points':revised[0]['points']}],
        [{'edge':1,'radius':36,'points':[[110,500,1],[700,700,0],[398,263,2]]}],
        [{'edge':1,'radius':36,'points':[[100,500,1],[690,700,0],[398,263,2]]}],
        [{'edge':1,'radius':36,'points':revised[0]['points']+[[500,500,2]]}],
        [{'edge':1,'radius':36,'points':[[100,500,1],[700,700,0],[900,300,2]]}],
    ):
        with pytest.raises(ValueError):parse_points(completion({**result,'corrections':wrong}),context)
    assert context==snapshot


def test_point_verification_cannot_reorder_overlapping_correction_jobs():
    context=workspace();context['edge_count']=2;context['point_bounds']['2']=[195,195,804,804]
    second=deepcopy(context['corrections'][0]);second['edge']=2
    context['corrections'].append(second)
    result={'status':'revise','summary':'调整第二处发丝','corrections':list(reversed(context['corrections']))}
    with pytest.raises(ValueError,match='不能改变纠错区域'):parse_points(completion(result),context)


def test_point_evidence_is_original_rgb_with_unpainted_centers_and_bounded_context(tmp_path):
    y,x=np.indices((900,1000));source=Image.fromarray(np.stack((x%251,y%251,(x+y)%251),axis=-1).astype(np.uint8))
    original=source.tobytes();core=[250,200,762,712];frame=point_frame(core,source.size)
    patch={'edge':1,'radius':36,'points':[[100,500,1],[700,700,0],[600,250,2]]};snapshot=deepcopy(patch)
    result=render_points(source,[patch],[core],tmp_path,1,context_points=True)
    assert len(result['images'])==2 and result['point_bounds']=={'1':point_bounds(core,frame)}
    native=Image.open(result['images'][0]['path']);zoom=Image.open(result['images'][1]['path'])
    assert native.size==(704,704) and zoom.size==(512,512)
    window=result['windows'][0];assert window['coordinate']==[600,250] and window['source_box'][2]-window['source_box'][0]==256
    gx=frame[0]+round(600/999*703);gy=frame[1]+round(250/999*703)
    assert zoom.getpixel((256,256))==source.getpixel((gx,gy))
    # Samples away from the marker prove the zoom uses source pixels, with
    # nearest-neighbor doubling and no candidate alpha or edited composition.
    for px,py in ((10,10),(60,100),(430,450)):
        assert zoom.getpixel((px,py))==source.getpixel((window['source_box'][0]+px//2,window['source_box'][1]+py//2))
    assert native.getpixel((96,146))==(80,196,245)
    assert source.tobytes()==original and patch==snapshot
    for boxes in ([[250,200,763,712]],[[250,200,1001,712]],[[250.,200,762,712]]):
        with pytest.raises(ValueError):render_points(source,[patch],boxes,tmp_path,2,context_points=True)
    with pytest.raises(ValueError):render_points(source,[patch],[core],tmp_path,2,context_points='true')


@pytest.mark.parametrize('provider',['openai','qwen'])
def test_point_payload_sends_only_labeled_evidence_once_and_its_own_contract(provider):
    settings=AISettings.validated(provider,'https://example.com/v1','qwen3.8-max' if provider=='qwen' else 'gpt-5')
    pictures=[{'label':'真实落点原片','url':'data:a'},{'label':'十字中心放大','url':'data:b'}]
    payload=build_payload(settings,'细化发丝',Recipe().to_dict(),[],'unused','matte_points',{**workspace(),'review_images':pictures})
    content=payload['messages'][1]['content']
    assert [item['image_url']['url'] for item in content if item['type']=='image_url']==['data:a','data:b']
    assert '尚未执行像素纠错' in payload['messages'][0]['content']
    if provider=='openai':
        assert payload['response_format']['json_schema']['schema']['properties']['status']['enum']==['keep','revise','reject','uncertain']
    else:
        assert payload['enable_thinking'] is True and payload['thinking_budget']==512 and 'response_format' not in payload
