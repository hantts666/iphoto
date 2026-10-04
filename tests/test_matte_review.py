from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask,new_layer
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matte_review import edge_boxes,parse_review,render_review
from iphoto.matting.channels import estimate,suggest
from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings


def completion(plan):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan)}}]}


@pytest.mark.parametrize('status',['accept','reject','uncertain'])
def test_review_is_an_observation_only_contract(status):
    plan={'status':status,'summary':'白底发丝可见灰块'}
    assert parse_review(completion(plan))==plan
    with pytest.raises(ValueError):parse_review(completion({**plan,'recipe':Recipe().to_dict()}))
    with pytest.raises(ValueError):parse_review(completion({**plan,'status':'revise'}))
    with pytest.raises(ValueError):parse_review(completion({**plan,'summary':''}))
    with pytest.raises(ValueError):parse_review({'choices':[{'finish_reason':'length','message':{'content':json.dumps(plan)}}]})


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


def test_review_native_crops_match_actual_export_composition(tmp_path):
    from iphoto.cutout import compose_cutout
    a=np.zeros((900,1200),np.uint8);a[300:600,420:780]=150;a[340:560,490:710]=255
    source=Image.new('RGB',(1200,900),(85,102,68));original=source.tobytes()
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(a),sampling='alpha',preserve_resolution=True)}
    result=render_review(source,[new_layer('原图',True)],mask,tmp_path,1)
    assert 3<=len(result['images'])<=9
    expected=Image.alpha_composite(Image.new('RGBA',source.size,'white'),compose_cutout(source,Image.fromarray(a))).convert('RGB')
    box=result['boxes'][0]
    native=next(v for v in result['images'] if v['label']=='边缘1原像素候选白底')
    image=Image.open(native['path'])
    assert image.size==(box[2]-box[0],box[3]-box[1]) and image.tobytes()==expected.crop(box).tobytes()
    assert source.tobytes()==original
    assert edge_boxes(Image.new('L',(16,18)))==[]


def test_review_payload_sends_labeled_evidence_once_and_strict_schema():
    settings=AISettings.validated('openai','https://example.com/v1','vision')
    pictures=[{'label':'原像素白底','url':'data:image/png;base64,a'},{'label':'原像素黑底','url':'data:image/png;base64,b'}]
    body=build_payload(settings,'头发抠图',Recipe().to_dict(),[],'unused','matte_review',{'review_images':pictures})
    content=body['messages'][1]['content']
    assert [v['image_url']['url'] for v in content if v['type']=='image_url']==[v['url'] for v in pictures]
    assert set(body['response_format']['json_schema']['schema']['properties'])=={'status','summary'}
