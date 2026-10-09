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
    with pytest.raises(ValueError,match='结构无效'):
        parse_points(completion({**result,'status':'revised'}),context)
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


def test_proposed_location_is_exact_and_bound_to_its_original_source_edge():
    context=workspace();context['strand_candidates']={'1':[[398,263],[500,550]]}
    revised=deepcopy(context['corrections']);revised[0]['points'][-1]=[398,263,2]
    result={'status':'revise','summary':'选择原片C1对应可见细丝','corrections':revised}
    assert parse_points(completion(result),context)['corrections']==revised
    revised[0]['points'][-1]=[399,263,2]
    with pytest.raises(ValueError,match='同一原片候选'):parse_points(completion(result),context)
    # An unchanged real identity point can still be kept; no feature proposal
    # is allowed to decide that this pixel belongs to hair by itself.
    revised[0]['points'][-1]=context['corrections'][0]['points'][-1][:]
    assert parse_points(completion(result),{**context,'strand_candidates':{'1':[]}})['corrections']==revised
    revised[0]['points'][-1]=[398,263,2]
    with pytest.raises(ValueError):parse_points(completion(result),{**context,'strand_candidates':{'2':[[398,263]]}})
    for wrong in ([],{'1':[[398.,263]]},{'1':[[True,263]]},{'1':[[1000,263]]},{'1':[[398,263,2]]},{'1':[[398,263]]*17}):
        with pytest.raises(ValueError,match='原片候选无效'):
            parse_points(completion({'status':'keep','summary':'保持原落点','corrections':[]}),{**context,'strand_candidates':wrong})


def test_thin_source_candidates_have_no_opacity_and_respect_native_scope():
    from iphoto.matting.strand_candidates import propose
    source=Image.new('RGB',(900,900),(80,90,70));array=np.array(source)
    # Independently specified native bright and dark filaments; known photo
    # structure, not a copy of the feature calculation used by propose().
    array[200:700,590:594]=[200,180,130];array[200:700,420:424]=[10,20,10]
    source=Image.fromarray(array);original=source.tobytes();core=[250,200,762,712]
    frame=point_frame(core,source.size);bounds=point_bounds(core,frame)
    points=[[100,500,1],[700,700,0],[500,500,2]]
    offered=propose(source,core,frame,points,bounds)
    assert 1<=len(offered)<=16 and all(len(point)==2 and all(type(v) is int for v in point) for point in offered)
    native=[(frame[0]+round(x/999*(frame[2]-frame[0]-1)),frame[1]+round(y/999*(frame[3]-frame[1]-1))) for x,y in offered]
    assert any(419<=x<=424 for x,y in native) and any(589<=x<=594 for x,y in native)
    assert all(bounds[0]<=x<=bounds[2] and bounds[1]<=y<=bounds[3] for x,y in offered)
    assert source.tobytes()==original
    assert propose(Image.new('RGB',source.size,(80,90,70)),core,frame,points,bounds)==[]
    assert propose(source,core,frame,points[:2],bounds)==[]


@pytest.mark.parametrize('color',[(190,200,150),(80,110,190)])
def test_misplaced_t_window_cannot_hide_a_true_structure_in_the_same_core(color):
    from PIL import ImageDraw
    from iphoto.matting.strand_candidates import propose
    from iphoto.matte_review import point_window
    source=Image.new('RGB',(900,900),(80,110,70));drawing=ImageDraw.Draw(source)
    drawing.arc((600,400,700,520),0,340,fill=color,width=4)
    core=[250,250,762,762];frame=point_frame(core,source.size);bounds=point_bounds(core,frame)
    points=[[100,500,1],[700,700,0],[300,500,2]]
    window,_=point_window(source.size,frame,points[-1]);assert window[2]<600
    offered=propose(source,core,frame,points,bounds);assert offered and len(offered)<=16
    native=[(frame[0]+round(x/999*703),frame[1]+round(y/999*703)) for x,y in offered]
    assert any(x>window[2] and source.getpixel((x,y))==color for x,y in native)
    assert all(bounds[0]<=x<=bounds[2] and bounds[1]<=y<=bounds[3] for x,y in offered)
    assert propose(source,core,frame,points,bounds)==offered


def test_point_evidence_is_original_rgb_with_unpainted_centers_and_bounded_context(tmp_path):
    y,x=np.indices((900,1000));source=Image.fromarray(np.stack((x%251,y%251,(x+y)%251),axis=-1).astype(np.uint8))
    original=source.tobytes();core=[250,200,762,712];frame=point_frame(core,source.size)
    patch={'edge':1,'radius':36,'points':[[100,500,1],[700,700,0],[600,250,2]]};snapshot=deepcopy(patch)
    result=render_points(source,[patch],[core],tmp_path,1,context_points=True)
    assert len(result['images'])==2+(len(result['strand_candidates']['1'])+7)//8+bool(result['strand_candidates']['1']) and result['point_bounds']=={'1':point_bounds(core,frame)}
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
    for px,py in [p[:2] for p in patch['points']]+result['strand_candidates']['1']:
        x=round(px/999*703);y=round(py/999*703)
        assert native.getpixel((x,y))==source.getpixel((frame[0]+x,frame[1]+y))
        zx=frame[0]+x-window['source_box'][0];zy=frame[1]+y-window['source_box'][1]
        if 0<=zx<256 and 0<=zy<256:
            assert zoom.getpixel((zx*2,zy*2))==source.getpixel((frame[0]+x,frame[1]+y))
    assert source.tobytes()==original and patch==snapshot
    for boxes in ([[250,200,763,712]],[[250,200,1001,712]],[[250.,200,762,712]]):
        with pytest.raises(ValueError):render_points(source,[patch],boxes,tmp_path,2,context_points=True)
    with pytest.raises(ValueError):render_points(source,[patch],[core],tmp_path,2,context_points='true')


def test_channel_structure_evidence_keeps_native_geometry_and_original_center():
    from PIL import ImageDraw
    from iphoto.matting.strand_structures import render_structures
    source=Image.new('RGB',(900,900),(80,110,70));drawing=ImageDraw.Draw(source)
    # A known curved filament and a long stripe intentionally clipped by the
    # native core. Both are merely features; the latter could be clothing.
    drawing.arc((450,380,570,520),20,340,fill=(190,200,150),width=4)
    drawing.line((350,200,350,800),fill=(10,20,10),width=4)
    core=[250,250,762,762];frame=point_frame(core,source.size);original=source.tobytes()
    locations=[(550,500),(350,460)]
    assert source.getpixel(locations[0])==(190,200,150)
    coordinates=[[round((x-frame[0])/703*999),round((y-frame[1])/703*999)] for x,y in locations]
    records,sheets=render_structures(source,core,frame,coordinates);sheet=sheets[0]
    assert sheet.size==(1024,276) and [r['coordinate'] for r in records]==coordinates
    assert records[0]['features'] and any(f['polarity']=='bright' and not f['clipped'] for f in records[0]['features'])
    assert records[1]['features'] and all(f['clipped'] for f in records[1]['features'])
    for number,record in enumerate(records):
        assert set(record)=={'candidate','coordinate','source_box','center_box','features'}
        box=record['source_box'];assert box[2]-box[0]==256 and box[3]-box[1]==256
        assert sheet.crop((number*512,20,number*512+256,276)).tobytes()==source.crop(box).tobytes()
        center=source.crop(record['center_box']).resize((256,256),Image.Resampling.NEAREST)
        assert sheets[-1].crop((number*256,20,number*256+256,276)).tobytes()==center.tobytes()
        for feature in record['features']:
            assert set(feature)=={'channel','polarity','threshold','source_box','pixels','clipped'}
            assert core[0]<=feature['source_box'][0]<feature['source_box'][2]<=core[2]
            assert core[1]<=feature['source_box'][1]<feature['source_box'][3]<=core[3]
        x=frame[0]+round(coordinates[number][0]/999*703);y=frame[1]+round(coordinates[number][1]/999*703)
        for dx,dy in ((-1,-1),(0,0),(1,1)):
            assert sheet.getpixel((number*512+256+x-box[0]+dx,20+y-box[1]+dy))==source.getpixel((x+dx,y+dy))
    assert source.tobytes()==original
    assert render_structures(source,core,frame,[])==([],[])
    flat=Image.new('RGB',source.size,(80,110,70))
    records,_=render_structures(flat,core,frame,coordinates)
    assert all(r['features']==[] for r in records)


def test_native_structure_sheet_does_not_change_existing_candidate_identity(tmp_path):
    from PIL import ImageDraw
    from iphoto.matting.strand_candidates import propose
    source=Image.new('RGB',(900,900),(80,110,70));drawing=ImageDraw.Draw(source)
    drawing.arc((390,380,630,610),0,340,fill=(190,200,150),width=4)
    core=[250,250,762,762];frame=point_frame(core,source.size);bounds=point_bounds(core,frame)
    patch={'edge':1,'radius':36,'points':[[100,500,1],[750,730,0],[500,500,2]]}
    expected=propose(source,core,frame,patch['points'],bounds);assert expected
    result=render_points(source,[patch],[core],tmp_path,1,context_points=True)
    assert result['strand_candidates']=={'1':expected}
    assert [r['coordinate'] for r in result['strand_structures']['1']]==expected
    sheet=Image.open(result['images'][-1]['path']);assert sheet.width==1024 and sheet.height<=1104
    assert len(result['images'])==3+(len(expected)+7)//8
    # C numbering and native Source registration must survive a sheet break;
    # an otherwise plausible tile attached to the wrong C could misdirect AI.
    for index,record in enumerate(result['strand_structures']['1']):
        sheet=Image.open(result['images'][2+index//8]['path'])
        x=index%2*512;y=(index%8)//2*276+20;box=record['source_box']
        assert sheet.crop((x,y,x+box[2]-box[0],y+box[3]-box[1])).tobytes()==source.crop(box).tobytes()
        centers=Image.open(result['images'][-1]['path']);x=index%4*256;y=index//4*276+20
        original=source.crop(record['center_box']);size=(original.width*4,original.height*4)
        assert centers.crop((x,y,x+size[0],y+size[1])).tobytes()==original.resize(size,Image.Resampling.NEAREST).tobytes()
    # Extra evidence stays within the existing preflight request; it grants
    # no permission for new coordinates or foreground opacity.
    context={**workspace(),'corrections':[patch],'point_bounds':{'1':bounds},
             'strand_candidates':result['strand_candidates'],'strand_structures':result['strand_structures']}
    revised=deepcopy(context['corrections']);revised[0]['points'][-1]=[expected[0][0]+1,expected[0][1],2]
    with pytest.raises(ValueError,match='同一原片候选'):
        parse_points(completion({'status':'revise','summary':'偏移到图中附近坐标','corrections':revised}),context)


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


def test_point_format_retry_preserves_strict_status_and_coordinate_contract():
    settings=AISettings.validated('qwen','https://example.com/v1','qwen3.8-max')
    context={**workspace(),'_validation_feedback':'AI未返回有效发丝落点检查，原范围保留'}
    payload=build_payload(settings,'细化发丝',Recipe().to_dict(),[],'unused','matte_points',context)
    assert payload['response_format']=={'type':'json_object'} and payload['enable_thinking'] is False
    assert 'thinking_budget' not in payload
    prompt=payload['messages'][0]['content']
    assert 'keep、revise、reject、uncertain' in prompt and '不写revised' in prompt
    assert 'JSON前后不得附加' in prompt and '精确复制' in prompt
    result={'status':'revise','summary':'格式合法仍须检查实际范围','corrections':context['corrections']}
    assert parse_points(completion(result),context)==result
    wrong=deepcopy(result);wrong['corrections'][0]['radius']=40
    with pytest.raises(ValueError,match='只能移动或删除'):parse_points(completion(wrong),context)
