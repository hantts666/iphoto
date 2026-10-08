from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask,new_layer
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matte_review import edge_boxes,parse_review,render_review,exclude_candidates,keep_candidates
from iphoto.matting.channels import estimate,suggest
from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings


def completion(plan):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan)}}]}


@pytest.mark.parametrize('status',['accept','reject','uncertain'])
def test_terminal_review_cannot_return_editing_instructions(status):
    plan={'status':status,'summary':'白底发丝可见灰块'}
    assert parse_review(completion(plan))==plan
    with pytest.raises(ValueError):parse_review(completion({**plan,'recipe':Recipe().to_dict()}))
    with pytest.raises(ValueError):parse_review(completion({**plan,'status':'revise'}))
    with pytest.raises(ValueError):parse_review(completion({**plan,'summary':''}))
    with pytest.raises(ValueError):parse_review({'choices':[{'finish_reason':'length','message':{'content':json.dumps(plan)}}]})


def test_visual_exclusions_have_an_explicit_capability_and_preserve_opaque_references():
    plan={'status':'revise','summary':'原片此处为背景，候选误选为灰云',
          'corrections':[{'edge':1,'radius':24,'points':[[700,800,1],[470,470,0]]}]}
    context={'revision':0,'correction_available':True,'edge_count':1,
             'keep_candidates':{'1':[[700,800]]},'exclude_candidates':{'1':[[160,160]]}}
    with pytest.raises(ValueError,match='已提供的背景参照'):
        parse_review(completion(plan),context)
    context['visual_exclusions']=True
    assert parse_review(completion(plan),context)==plan
    for coordinate in ([20,470],[470,960]):
        wrong={**plan,'corrections':[{'edge':1,'radius':24,'points':[[700,800,1],[*coordinate,0]]}]}
        with pytest.raises(ValueError,match='过近裁片边界'):
            parse_review(completion(wrong),context)
    wrong={**plan,'corrections':[{'edge':1,'radius':24,'points':[[710,800,1],[470,470,0]]}]}
    with pytest.raises(ValueError,match='不透明参照'):
        parse_review(completion(wrong),context)
    # Explicit visual mode keeps its boundary guard even without an N list.
    del context['exclude_candidates']
    with pytest.raises(ValueError,match='过近裁片边界'):
        parse_review(completion({**plan,'corrections':[{'edge':1,'radius':24,'points':[[700,800,1],[10,470,0]]}]}),context)


def calculation_scene():
    rng=np.random.default_rng(37)
    a=np.zeros((160,256),np.float32);a[30:130,55:200]=.6
    a[50:110,90:110]=1
    a[35:125:4,130:185]=np.linspace(.08,.85,55)[None,:]
    a[78:84,130:140]=0
    noise=rng.uniform(-65,65,(*a.shape,1))
    pixels=a[...,None]*np.array([120,80,100])+(1-a[...,None])*np.array([80,120,100])+noise
    image=Image.fromarray(np.rint(pixels).astype(np.uint8))
    seed=Image.fromarray((a>0).astype(np.uint8)*255)
    mask={**empty_mask(),'bitmap':encode_bitmap(seed,sampling='alpha',preserve_resolution=True)}
    return image,a,mask


def test_strand_references_need_hair_capability_and_cannot_replace_opaque_anchors():
    plan={'status':'revise','summary':'补充原片可见的半透明细丝',
          'corrections':[{'edge':1,'radius':24,'points':[[700,800,1],[470,470,0],[320,640,2]]}]}
    context={'revision':0,'correction_available':True,'edge_count':1,
             'correction_method':'hair','strand_points':True,
             'keep_candidates':{'1':[[700,800]]},'exclude_candidates':{'1':[[470,470]]}}
    assert parse_review(completion(plan),context)==plan
    for change in ({'strand_points':False},{'strand_points':'true'},{'correction_method':'semantic'},{'revision':1}):
        with pytest.raises(ValueError):parse_review(completion(plan),{**context,**change})
    for target in ([20,640,2],[320,960,2],[320,640,3]):
        wrong=deepcopy(plan);wrong['corrections'][0]['points'][2]=target
        with pytest.raises(ValueError):parse_review(completion(wrong),context)
    wrong=deepcopy(plan);wrong['corrections'][0]['points'][0][2]=2
    with pytest.raises(ValueError,match='保留与排除'):parse_review(completion(wrong),context)
    wrong=deepcopy(plan);wrong['corrections'][0]['points'][0][0]=710
    with pytest.raises(ValueError,match='不透明参照'):parse_review(completion(wrong),context)


def test_channel_calculations_separate_shared_lighting_from_transparency():
    image,a,mask=calculation_scene()
    before=deepcopy(mask)
    options,metrics=suggest(image,mask,8)
    assert options['channel']=='red_green'
    from iphoto.document import raster_mask
    # The selected cloth is mostly translucent, so automatic foreground
    # percentiles cannot know its opaque reference. Supply the independently
    # known black/white endpoints; test separation rather than assume a
    # semantic binary selection identifies opaque material.
    output,_=estimate(image,mask,{**options,'black':88,'white':168,'ai':False,'interior':True})
    alpha=np.asarray(raster_mask(output,image.size))/255
    assert np.abs(alpha-a).mean()<.003
    assert np.all(alpha[78:84,130:140]==0) and mask==before
    assert max(v['score'] for v in metrics if v['channel'] in ('red','green','blue','luminance'))<2
    from iphoto.matting.channels import whole_options
    full,_=estimate(image,empty_mask(True),{**whole_options(),'channel':'red_green','black':88,'white':168})
    assert np.abs(np.asarray(raster_mask(full,image.size))/255-a).mean()<.003


def test_review_native_crops_match_actual_export_composition(tmp_path):
    from iphoto.cutout import compose_cutout
    a=np.zeros((900,1200),np.uint8);a[300:600,420:780]=150;a[340:560,490:710]=255
    source=Image.new('RGB',(1200,900),(85,102,68));original=source.tobytes()
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(a),sampling='alpha',preserve_resolution=True)}
    result=render_review(source,[new_layer('原图',True)],mask,tmp_path,1)
    assert 3<=len(result['images'])<=18 and 2<=len(result['review_images'])<=6
    expected=Image.alpha_composite(Image.new('RGBA',source.size,'white'),compose_cutout(source,Image.fromarray(a))).convert('RGB')
    box=result['boxes'][0]
    native=next(v for v in result['images'] if v['label']=='边缘1原像素候选白底')
    image=Image.open(native['path'])
    assert image.size==(box[2]-box[0],box[3]-box[1]) and image.tobytes()==expected.crop(box).tobytes()
    checker=Image.open(next(v['path'] for v in result['images'] if '边缘1原像素候选紫色棋盘格' in v['label']))
    assert checker.size==image.size
    transparent=np.asarray(Image.fromarray(a).crop(box))==0
    yy,xx=np.indices((checker.height,checker.width));pattern=(xx//20+yy//20)%2
    colors=np.array([[119,82,166],[170,130,200]],np.uint8)
    assert np.array_equal(np.asarray(checker)[transparent],colors[pattern][transparent])
    panel=Image.open(next(v['path'] for v in result['images'] if '边缘1原像素四格质量对照' in v['label']))
    assert panel.size==(image.width*2,(image.height+24)*2)
    assert panel.crop((0,image.height+48,image.width,panel.height)).tobytes()==image.tobytes()
    assert source.tobytes()==original
    assert edge_boxes(Image.new('L',(16,18)))==[]


def test_review_payload_sends_labeled_evidence_once_and_strict_schema():
    settings=AISettings.validated('openai','https://example.com/v1','vision')
    pictures=[{'label':'原像素白底','url':'data:image/png;base64,a'},{'label':'原像素黑底','url':'data:image/png;base64,b'}]
    body=build_payload(settings,'头发抠图',Recipe().to_dict(),[],'unused','matte_review',{'review_images':pictures})
    content=body['messages'][1]['content']
    assert [v['image_url']['url'] for v in content if v['type']=='image_url']==[v['url'] for v in pictures]
    assert set(body['response_format']['json_schema']['schema']['properties'])=={'status','summary','corrections'}


def test_review_source_stays_original_and_target_context_preserves_its_coordinates(tmp_path):
    from iphoto.document import render_layers
    from iphoto.cutout import compose_cutout
    from iphoto.engine import preview
    source=Image.new('RGB',(2400,1800),(85,102,68));original=source.tobytes()
    a=np.zeros((1800,2400),np.uint8);a[700:1200,900:1400]=255
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(a),sampling='alpha',preserve_resolution=True)}
    layer=new_layer('已有曝光调整',True);layer['recipe']=Recipe(exposure=1).to_dict()
    layers=[layer];snapshot=deepcopy((layers,mask))
    box=[840,640,1352,1152]
    result=render_review(source,layers,mask,tmp_path,2,[box],target_context=True)
    whole=Image.open(result['images'][0]['path'])
    assert whole.tobytes()==preview(source,1280).tobytes()
    context=result['review_images'][1]
    assert '上下文' in context['label'] and '纠错坐标只相对编号定位图' in context['label']
    overview=Image.open(context['path'])
    assert '蓝框数字对应边缘编号' in context['label']
    assert overview.size==(1012,1140) and max(overview.size)<=1280 and result['boxes']==[box]
    # The frame is lifted from source coordinates into this crop. Interior
    # and outside source samples retain their exact unadjusted colors.
    assert overview.getpixel((196,500))==(80,196,245)
    for xy in ((50,50),(400,400),(400,600),(900,1000)):
        assert overview.getpixel(xy)==source.getpixel((xy[0]+644,xy[1]+188))
    native=next(item for item in result['images'] if item['label']=='边缘1原像素原照片')
    assert Image.open(native['path']).tobytes()==source.crop(box).tobytes()
    panel=Image.open(next(item['path'] for item in result['images'] if '四格质量对照' in item['label']))
    assert panel.crop((0,24,512,536)).tobytes()==source.crop(box).tobytes()
    adjusted=render_layers(source,layers);assert adjusted.tobytes()!=original
    expected=Image.alpha_composite(Image.new('RGBA',source.size,'white'),compose_cutout(adjusted,Image.fromarray(a))).convert('RGB')
    white=next(item for item in result['images'] if item['label']=='边缘1原像素候选白底')
    assert Image.open(white['path']).tobytes()==expected.crop(box).tobytes()
    assert source.tobytes()==original and (layers,mask)==snapshot
    assert len({item['path'] for item in result['review_images']})==len(result['review_images'])
    with pytest.raises(ValueError,match='上下文选项无效'):
        render_review(source,layers,mask,tmp_path,3,[box],target_context='hair')


def test_context_anchors_reach_opaque_hair_without_expanding_reviewed_pixels(tmp_path):
    from iphoto.matte_review import point_bounds,point_frame
    source=Image.new('RGB',(1000,900),(75,130,110));a=np.zeros((900,1000),np.uint8)
    core=[400,200,912,712];a[260:650,315:390]=255;a[280:660,400:520]=40
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(a),sampling='alpha',preserve_resolution=True)}
    before=source.tobytes(),deepcopy(mask)
    assert keep_candidates(Image.fromarray(a).crop(core))==[]
    result=render_review(source,[new_layer('原图',True)],mask,tmp_path,4,[core],target_context=True)
    frame=point_frame(core,source.size)
    assert result['context_points'] is True and result['point_boxes']==[frame]
    assert result['boxes']==[core] and result['point_bounds']=={'1':point_bounds(core,frame)}
    assert result['keep_candidates']['1']
    for x,y in result['keep_candidates']['1']:
        px=round(frame[0]+x/999*(frame[2]-frame[0]-1));py=round(frame[1]+y/999*(frame[3]-frame[1]-1))
        assert 315<=px<390 and a[py,px]>=245  # Outside the edited core.
    locator=Image.open(next(item['path'] for item in result['images'] if '-locate-' in item['path']))
    assert locator.size==(frame[2]-frame[0],frame[3]-frame[1])
    assert locator.getpixel((core[0]-frame[0],core[1]-frame[1]+50))==(80,196,245)
    assert locator.getpixel((650,650))==source.getpixel((frame[0]+650,frame[1]+650))
    native=Image.open(next(item['path'] for item in result['images'] if '-source-0' in item['path']))
    assert native.tobytes()==source.crop(core).tobytes() and native.size==(512,512)
    assert len(result['review_images'])==5 and (source.tobytes(),mask)==before
    assert result['strand_detail_count']==0


def test_context_coordinates_require_hair_capability_and_keep_edit_points_inside():
    plan={'status':'revise','summary':'上下文头发参照与蓝框内细丝',
          'corrections':[{'edge':1,'radius':24,'points':[[80,700,1],[400,500,0],[650,650,2]]}]}
    context={'revision':0,'correction_available':True,'edge_count':1,'correction_method':'hair',
             'strand_points':True,'context_points':True,'point_bounds':{'1':[200,200,800,800]},
             'keep_candidates':{'1':[[80,700]]},'exclude_candidates':{'1':[]},'visual_exclusions':True}
    assert parse_review(completion(plan),context)==plan
    for role in (0,2):
        wrong=deepcopy(plan);wrong['corrections'][0]['points'][role//2+1]=[850,600,role]
        with pytest.raises(ValueError,match='过近裁片边界'):parse_review(completion(wrong),context)
    for change in ({'correction_method':'semantic'},{'point_bounds':None},{'keep_candidates':{}},
                   {'point_bounds':{'1':[200,200,800,True]}},{'point_bounds':{'2':[200,200,800,800]}}):
        with pytest.raises(ValueError):parse_review(completion(plan),{**context,**change})
    wrong=deepcopy(plan);wrong['corrections'][0]['points'][0]=[90,700,1]
    with pytest.raises(ValueError,match='不透明参照'):parse_review(completion(wrong),context)


def test_verified_strand_quality_window_matches_original_and_actual_alpha_output(tmp_path,monkeypatch):
    from iphoto.document import render_layers
    from iphoto.cutout import compose_cutout
    from iphoto.matte_review import point_frame,point_window
    source=Image.new('RGB',(1000,900),(80,110,70));original=source.tobytes()
    a=np.zeros((900,1000),np.uint8);a[150:800,150:900]=255;a[250:600,500:800]=35
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(a),sampling='alpha',preserve_resolution=True)}
    layer=new_layer('已有调整',True);layer['recipe']=Recipe(exposure=.5).to_dict()
    core=[350,200,862,712];patch={'edge':1,'radius':36,'points':[[200,500,1],[750,700,0],[600,400,2]]}
    result=render_review(source,[layer],mask,tmp_path,5,[core],target_context=True,detail_points=[patch])
    frame=point_frame(core,source.size);box,_=point_window(source.size,frame,patch['points'][-1]);size=(512,512)
    item=next(item for item in result['review_images'] if '发丝参照T3局部质量' in item['label'])
    panel=Image.open(item['path']);assert panel.size==(1024,1072) and len(result['review_images'])==6
    assert result['strand_detail_count']==1
    assert panel.crop((0,24,512,536)).tobytes()==source.crop(box).resize(size,Image.Resampling.NEAREST).tobytes()
    expected_alpha=Image.fromarray(a).crop(box).convert('RGB').resize(size,Image.Resampling.NEAREST)
    assert panel.crop((512,24,1024,536)).tobytes()==expected_alpha.tobytes()
    composed=compose_cutout(render_layers(source,[layer]),Image.fromarray(a))
    white=Image.alpha_composite(Image.new('RGBA',(256,256),'white'),composed.crop(box)).convert('RGB').resize(size,Image.Resampling.NEAREST)
    assert panel.crop((0,560,512,1072)).tobytes()==white.tobytes()
    monkeypatch.setattr('iphoto.matte_review.keep_candidates',lambda *args:pytest.fail('最终复查不应重新找P'))
    monkeypatch.setattr('iphoto.matte_review.exclude_candidates',lambda *args:pytest.fail('最终复查不应重新找N'))
    final=render_review(source,[layer],mask,tmp_path,7,[core],target_context=True,detail_points=[patch],final_review=True)
    assert len(final['review_images'])==5
    assert '发丝参照T3局部质量' in final['review_images'][2]['label']
    assert '四格质量对照' in final['review_images'][3]['label']
    assert final['review_images'][-1]['label']=='候选整体紫色棋盘格'
    assert all('定位图（' not in entry['label'] for entry in final['review_images'])
    for entry in final['review_images']:
        original_entry=(result['review_images'][1] if '上下文' in entry['label'] else
                        next(v for v in result['review_images'] if v['label']==entry['label']))
        assert Image.open(entry['path']).tobytes()==Image.open(original_entry['path']).tobytes()
        assert entry['lossless']==('质量对照' in entry['label'])
    assert final['boxes']==result['boxes'] and final['point_bounds']==result['point_bounds']
    assert final['keep_candidates']=={'1':[]} and final['exclude_candidates']=={'1':[]}
    assert all('-locate-' not in entry['path'] for entry in final['images'])
    with pytest.raises(ValueError,match='最终检查选项无效'):
        render_review(source,[layer],mask,tmp_path,8,[core],final_review=1)
    assert source.tobytes()==original
    with pytest.raises(ValueError,match='缺少头发上下文'):
        render_review(source,[layer],mask,tmp_path,6,[core],detail_points=[patch])


@pytest.mark.parametrize('invert',[False,True])
def test_visual_exclusion_references_can_reach_wrong_opaque_foreground(invert):
    # The coarse model calls a whole light/dark cloth patch foreground. An
    # exclusion list derived only from alpha==0 cannot repair that mistake.
    a=np.zeros((512,512),np.uint8);a[60:450,60:320]=255
    a[180:300,320:430]=255
    rgb=np.full((512,512,3),180,np.uint8);rgb[60:450,60:320]=35
    if invert:rgb=255-rgb
    image,alpha=Image.fromarray(rgb),Image.fromarray(a)
    before=image.tobytes(),alpha.tobytes()
    old=keep_candidates(alpha,False);points=exclude_candidates(image,alpha)
    assert points[:len(old)]==old and len(old)<len(points)<=len(old)+3
    selected=[(round(x/999*511),round(y/999*511)) for x,y in points[len(old):]]
    assert any(320<=x<430 and 180<=y<300 and a[y,x]==255 for x,y in selected)
    assert all(40<=x<472 and 40<=y<472 for x,y in selected)
    assert (image.tobytes(),alpha.tobytes())==before


def test_weak_color_contrast_does_not_invent_exclusion_references():
    a=np.zeros((512,512),np.uint8);a[60:450,60:320]=255
    alpha=Image.fromarray(a);image=Image.new('RGB',alpha.size,(95,95,95))
    assert exclude_candidates(image,alpha)==keep_candidates(alpha,False)


def test_tested_qwen_review_has_bounded_reasoning_without_incompatible_json_mode():
    settings=AISettings.validated('qwen','https://example.com/v1','qwen3.8-max')
    body=build_payload(settings,'头发抠图',Recipe().to_dict(),[],'unused','matte_review',{})
    assert body['enable_thinking'] is True and body['thinking_budget']==512
    assert body['max_tokens']==4096 and 'response_format' not in body and 'reasoning_effort' not in body
    ordinary=build_payload(settings,'回答',Recipe().to_dict(),[],'unused','advice',{})
    assert ordinary['enable_thinking'] is False and 'thinking_budget' not in ordinary and 'response_format' in ordinary
    older=AISettings.validated('qwen','https://example.com/v1','qwen-vl-max')
    assert build_payload(older,'修图',Recipe().to_dict(),[],'unused','matte_review',{})['enable_thinking'] is False


@pytest.mark.parametrize('provider',['qwen','qianwen','qianwen_token_plan'])
def test_final_source_detail_review_is_focused_with_the_existing_bounded_budget(provider):
    url=('https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
         if provider=='qianwen_token_plan' else 'https://example.com/v1')
    settings=AISettings.validated(provider,url,'qwen3.8-max')
    context={'correction_method':'hair','context_points':True,'strand_detail_count':1,
             'revision':1,'correction_available':False,'target':'头发','keep_candidates':{'1':[[500,500]]},
             'point_bounds':{'1':[200,200,800,800]},'point_budget':8,'previous_check':'不能借用旧结论'}
    body=build_payload(settings,'核对原片细丝',Recipe().to_dict(),[],'unused','matte_review',context)
    assert body['enable_thinking'] is True and body['thinking_budget']==512
    assert body['max_tokens']==4096
    assert 'response_format' not in body and 'reasoning_effort' not in body
    assert body['messages'][1]['content'][0]['type']=='text'
    sent=json.loads(body['messages'][1]['content'][0]['text'])
    assert sent=={'request':'核对原片细丝','mode':'matte_review','target':'头发',
                  'revision':1,'correction_available':False,'correction_method':'hair','strand_detail_count':1}
    assert '不能再提供取点或工具计划' in body['messages'][0]['content']


@pytest.mark.parametrize('change',[{'revision':0,'correction_available':True},{'revision':True},
    {'revision':'1'},{'revision':1.,'correction_available':False},{'correction_available':0},
    {'correction_available':True}])
def test_only_actual_final_review_removes_point_planning_instructions(change):
    settings=AISettings.validated('openai','https://example.com/v1','vision')
    context={'revision':1,'correction_available':False,'keep_candidates':{'1':[[500,500]]},**change}
    body=build_payload(settings,'复查',Recipe().to_dict(),[],'unused','matte_review',context)
    assert 'keep_candidates' in json.loads(body['messages'][1]['content'][0]['text'])
    assert '不能再提供取点或工具计划' not in body['messages'][0]['content']


@pytest.mark.parametrize('change',[{'strand_detail_count':0},{'strand_detail_count':True},
    {'strand_detail_count':-1},{'strand_detail_count':5},{'strand_detail_count':'1'},
    {'strand_detail_count':None},{'context_points':False},{'context_points':1},
    {'correction_method':'semantic'}])
def test_missing_or_invalid_source_detail_does_not_increase_remote_reasoning_cost(change):
    settings=AISettings.validated('qwen','https://example.com/v1','qwen3.8-max')
    context={'correction_method':'hair','context_points':True,'strand_detail_count':1,**change}
    for mode in ('matte_review','matte_points'):
        body=build_payload(settings,'核对',Recipe().to_dict(),[],'unused',mode,context)
        assert body['thinking_budget']==512 and body['max_tokens']==4096
