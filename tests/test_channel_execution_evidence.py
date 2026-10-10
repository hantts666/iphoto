"""What vision sees must be the actual native constraint/curve operation."""
from copy import deepcopy

import numpy as np
from PIL import Image
import pytest

from iphoto.channel_advisor import render_evidence
from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.matting import channels
from iphoto.matting.scope import restrict
from test_channel_matting import scene


def cell(atlas,index,size):
    width,height=size
    x=index%3*width;y=index//3*(height+24)+24
    return atlas.crop((x,y,x+width,y+height)).convert('L')


@pytest.mark.parametrize('semantic',[False,True])
@pytest.mark.parametrize('limited',[False,True])
def test_evidence_matches_the_executed_guide_and_native_output(tmp_path,semantic,limited):
    source,_,mask,options=scene()
    if semantic:mask={**mask,'semantic_target':'face_skin'}
    limit=None
    if limited:
        pixels=np.full((source.height,source.width),74,dtype='uint8')
        pixels[:,150:]=0;pixels[:30]=255
        limit={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(pixels),sampling='alpha',preserve_resolution=True)}
    before=deepcopy((mask,options,limit));source_pixels=source.tobytes();calls=[]
    def neural(crop,guide,progress=None):
        calls.append((crop.size,guide.copy()))
        return guide.copy(),1
    channels.estimate(source,mask,options,neural=neural)
    assert len(calls)==1
    native,_=channels.estimate(source,mask,{**options,'ai':False})
    if limited:native=restrict(native,limit,source.size)
    expected_native=raster_mask(native,source.size)
    original,box,*_=channels._region(source,mask,options['radius'])
    expected_guide=Image.new('L',source.size,0)
    expected_guide.paste(Image.fromarray(calls[0][1]),box[:2])
    evidence=render_evidence(source,mask,options,tmp_path,'execution',scope_limit=limit)
    scope=evidence['channel_scope']
    assert scope['semantic_limited'] is semantic and scope['upper_bound_limited'] is limited
    assert scope['radius']==options['radius']
    assert len(scope['regions'])==len(evidence['boxes'])
    for index,(item,frame) in enumerate(zip(evidence['images'][1:],evidence['boxes'])):
        size=(frame[2]-frame[0],frame[3]-frame[1]);atlas=Image.open(item['path'])
        actual_guide=cell(atlas,10,size)
        assert cell(atlas,9,size).tobytes()==original.crop(frame).tobytes()
        assert actual_guide.tobytes()==expected_guide.crop(frame).tobytes()
        assert cell(atlas,11,size).tobytes()==expected_native.crop(frame).tobytes()
        assert set(np.unique(actual_guide))<={0,128,255}
        counts=actual_guide.histogram();record=scope['regions'][index]
        assert record['edge']==index+1
        assert [record['known_background_pixels'],record['unknown_pixels'],record['known_foreground_pixels']]==[counts[v] for v in (0,128,255)]
        if limited:
            assert record['upper_bound_nonzero_pixels']==size[0]*size[1]-raster_mask(limit,source.size).crop(frame).histogram()[0]
    assert (mask,options,limit)==before and source.tobytes()==source_pixels


def test_raw_curve_cannot_masquerade_as_a_growing_selection(tmp_path):
    source,_,mask,options=scene()
    pixels=np.array(source);pixels[10:20,10:20]=210
    source=Image.fromarray(pixels)
    evidence=render_evidence(source,mask,options,tmp_path,'outside')
    # This bright patch looks fully selected in the curve, but it is outside
    # both the current object and its growth allowance. Vision must see that.
    index=next(i for i,b in enumerate(evidence['boxes']) if b[0]<=10<20<=b[2] and b[1]<=10<20<=b[3])
    frame=evidence['boxes'][index];size=(frame[2]-frame[0],frame[3]-frame[1])
    local=(10-frame[0],10-frame[1],20-frame[0],20-frame[1])
    atlas=Image.open(evidence['images'][index+1]['path'])
    assert cell(atlas,8,size).crop(local).getextrema()==(255,255)
    for number in (9,10,11):assert cell(atlas,number,size).crop(local).getextrema()==(0,0)


def test_large_reference_keeps_true_distances_but_new_constraint_work_is_bounded(tmp_path,monkeypatch):
    from iphoto import channel_advisor
    pixels=np.full((1536,2048,3),35,dtype='uint8')
    pixels[320:1216,300:1700]=210
    source=Image.fromarray(pixels)
    alpha=np.zeros(pixels.shape[:2],dtype='uint8');alpha[320:1216,300:1700]=255
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(alpha),sampling='alpha',preserve_resolution=True)}
    options={'channel':'red','black':35,'white':210,'gamma':1.,'invert':False,'radius':8,'ai':True,'interior':False}
    calls=[]
    def neural(crop,guide,progress=None):
        calls.append(guide.copy());return guide.copy(),1
    channels.estimate(source,mask,options,neural=neural)
    cache={};_,box,_=channels.native_reference(source,mask,8,cache=cache)
    expected=Image.new('L',source.size,0);expected.paste(Image.fromarray(calls[0]),box[:2])
    dimensions=[];actual=channel_advisor._constraints
    def constraints(a,*args):
        dimensions.append(a.shape);return actual(a,*args)
    monkeypatch.setattr(channel_advisor,'_constraints',constraints)
    monkeypatch.setattr(channels,'_fields',lambda *_:pytest.fail('Reference fields must be reused'))
    evidence=render_evidence(source,mask,options,tmp_path,'bounded',cache=cache)
    assert len(dimensions)==len(evidence['boxes'])==4
    assert all(height<=256 and width<=256 for height,width in dimensions)
    for item,frame in zip(evidence['images'][1:],evidence['boxes']):
        size=(frame[2]-frame[0],frame[3]-frame[1])
        assert cell(Image.open(item['path']),10,size).tobytes()==expected.crop(frame).tobytes()
