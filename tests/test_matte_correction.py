"""Bounded semantic repair cannot publish a wider or unchecked selection."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.matte_review import parse_review
from iphoto.matting.correction import correct


def completion(plan):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan)}}]}


def correction_plan():
    return {'status':'revise','summary':'衣物被误选，需要排除',
            'corrections':[{'edge':1,'radius':24,'points':[[375,375,1],[750,425,0]]}]}


CONTEXT={'revision':0,'edge_count':1,'correction_available':True}


def test_correction_requires_native_frame_and_one_revision():
    plan=correction_plan()
    assert parse_review(completion(plan),CONTEXT)==plan
    for context in ({},{**CONTEXT,'revision':1},{**CONTEXT,'correction_available':False},{**CONTEXT,'edge_count':0}):
        with pytest.raises(ValueError):parse_review(completion(plan),context)
    with pytest.raises(ValueError):parse_review(completion({**plan,'status':'accept'}),CONTEXT)


def test_guidance_must_use_offered_references_of_the_correct_class():
    plan=correction_plan()
    context={**CONTEXT,'keep_candidates':{'1':[[375,375]]},'exclude_candidates':{'1':[[750,425]]}}
    assert parse_review(completion(plan),context)==plan
    with pytest.raises(ValueError,match='保留点'):
        parse_review(completion(plan),{**context,'keep_candidates':{'1':[[100,100]]}})
    with pytest.raises(ValueError,match='排除点'):
        parse_review(completion(plan),{**context,'exclude_candidates':{'1':[[200,100]]}})


@pytest.mark.parametrize('patch',[
    {'edge':2,'radius':24,'points':[[375,375,1],[750,425,0]]},
    {'edge':True,'radius':24,'points':[[375,375,1],[750,425,0]]},
    {'edge':1,'radius':49,'points':[[375,375,1],[750,425,0]]},
    {'edge':1,'radius':24,'points':[[375,375,1],[750,425,1]]},
    {'edge':1,'radius':24,'points':[[375,375,1],[375,375,0]]},
    {'edge':1,'radius':24,'points':[[375,375,1],[-1,425,0]]},
    {'edge':1,'radius':24,'points':[[375,375,1],[1000,425,0]]},
    {'edge':1,'radius':24,'points':[[375.,375,1],[750,425,0]]},
    {'edge':1,'radius':24,'points':[[375,375,1],[750,425,2]]},
])
def test_invalid_guidance_is_not_executed(patch):
    plan={**correction_plan(),'corrections':[patch]}
    with pytest.raises(ValueError):parse_review(completion(plan),CONTEXT)


class Semantic:
    def __init__(self,score=.96):self.score=score
    def predict_with_prior(self,image,coords,labels,guide):
        # Independent source content identifies the actual target; the old
        # mask also includes a blue background rectangle.
        assert np.min(guide)>=64 and np.max(guide)<=191
        hard=np.asarray(image)[:,:,0]>100
        return np.where(hard,5.,-5.)[None],np.array([self.score]),{'model':'fixture'}


class Matte:
    provider='fixture';fallback=''
    def predict(self,pixels):
        return np.where(pixels[0,0]>0,.98,.01).astype(np.float32)


def scene():
    pixels=np.full((900,900,3),(30,100,160),np.uint8)
    pixels[200:600,200:600]=(200,40,40)
    a=np.zeros((900,900),np.uint8);a[200:600,200:600]=255;a[450:600,600:710]=255
    mask={**empty_mask(),'color_recovery':True,
          'bitmap':encode_bitmap(Image.fromarray(a),sampling='alpha',preserve_resolution=True)}
    return Image.fromarray(pixels),mask,[[350,350,750,750]]


def test_repair_removes_wrong_foreground_and_keeps_outside_pixels_exact():
    image,mask,boxes=scene();original=deepcopy(mask);photo=image.tobytes()
    output,quality=correct(image,mask,correction_plan()['corrections'],boxes,semantic=Semantic(),matte=Matte())
    before=np.asarray(raster_mask(mask,image.size));after=np.asarray(raster_mask(output,image.size))
    outside=np.ones(before.shape,bool);outside[350:750,350:750]=False
    assert np.array_equal(before[outside],after[outside])
    assert np.max(after[470:580,630:700])<5 and np.min(after[400:550,400:550])==255
    assert quality['corrections'][0]['changed_pixels']>10000
    assert output['color_recovery'] is True and mask==original and image.tobytes()==photo


def test_weak_prediction_and_protected_keep_point_fail_without_changes():
    image,mask,boxes=scene();original=deepcopy(mask)
    with pytest.raises(ValueError,match='信心不足'):
        correct(image,mask,correction_plan()['corrections'],boxes,semantic=Semantic(.6),matte=Matte())
    mask['semantic_target']='face_skin'
    patch=deepcopy(correction_plan()['corrections']);patch[0]['points'][0]=[900,900,1]
    with pytest.raises(ValueError,match='保留点'):
        correct(image,mask,patch,boxes,semantic=Semantic(),matte=Matte())
    assert {key:value for key,value in mask.items() if key!='semantic_target'}==original


def test_invalid_foreground_reference_does_not_load_semantic_model(monkeypatch):
    image,mask,boxes=scene()
    points=deepcopy(correction_plan()['corrections']);points[0]['points'][0]=[900,900,1]
    from iphoto.segmentation import precise_sam
    monkeypatch.setattr(precise_sam,'backend',lambda:pytest.fail('Invalid reference loaded SAM2'))
    with pytest.raises(ValueError,match='保留点'):
        correct(image,mask,points,boxes,matte=Matte())


def test_negative_reference_keeps_neighboring_strands_unknown():
    image,mask,boxes=scene()
    patch=deepcopy(correction_plan()['corrections'])
    patch[0]['points'][1]=[625,375,0]
    class InspectMatte(Matte):
        inspected=False
        def predict(self,pixels):
            if not self.inspected:
                self.inspected=True
                # Native point is (600,500), crop origin (254,254). The
                # adjacent foreground 3px away remains available to matting.
                assert pixels[0,3,246,346]==0
                assert pixels[0,3,246,343]>0
            return super().predict(pixels)
    matte=InspectMatte()
    correct(image,mask,patch,boxes,semantic=Semantic(),matte=matte)
    assert matte.inspected
