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
