"""Learned person constraints must stay hair-specific and respect review scope."""
from copy import deepcopy
from hashlib import sha256

import numpy as np
import pytest

from iphoto.document import raster_mask
from iphoto.matting import learned_models
from iphoto.matting.hair import HairContext
from iphoto.matting.learned import prompts, native_trimap
from iphoto.controllers.matte_process import _progress
from types import SimpleNamespace
from test_hair_matting import fixture, Parser, Matte


class Learned:
    def __init__(self, invalid=None):
        self.invalid = invalid
        self.calls = []

    def predict(self, image, points, *, progress=None, exclusions=None):
        self.calls.append((image.size,deepcopy(points),deepcopy(exclusions)))
        value = np.full((image.height,image.width),255,np.uint8)
        if self.invalid == 'shape': value = value[:20]
        if self.invalid == 'dtype': value = value.astype(float)
        if self.invalid == 'class': value[0,0] = 129
        return value, {'trimap_backend':'MattePro','trimap_provider':'fixture','trimap_ms':1}


def test_learned_uncertainty_can_recover_hair_with_zero_coarse_support():
    image,mask,portrait,_=fixture();original=raster_mask(mask,image.size)
    coarse=np.array(portrait.predict_image(image));coarse[300:550,480:535]=0
    from PIL import Image
    portrait.predict_image=lambda image:Image.fromarray(coarse)
    learned=Learned();box=(240,200,720,650)
    context=HairContext(image,original,portrait=portrait,parser=Parser(),learned=learned)
    previous=np.asarray(original.crop(box));before=previous.copy();photo=image.tobytes()
    points=[[400/999,430/899,1],[690/999,430/899,0],[505/999,430/899,2]]
    pixels,detail=context.solve(image,previous,box,semantic=previous>127,semantic_radius=40,points=points,engine=Matte())
    assert coarse[430,505]==0 and 0<pixels[230,265]<255
    assert pixels[230,160]==255 and pixels[230,450]==0
    assert pixels[230,360]==0  # Person foreground includes clothes, not hair.
    observed=learned.calls[0][1]
    assert observed==[[160,230,1],[450,230,0],[265,230,2]]
    assert len(learned.calls[0][2])==1 and learned.calls[0][2][0][2]==0
    assert detail['trimap_part_exclusions']==1
    assert detail['outer_tiles']==0 and detail['trimap_backend']=='MattePro'
    assert np.array_equal(previous,before) and image.tobytes()==photo


def test_learned_uncertainty_is_not_zeroed_by_binary_semantic_distance():
    image,mask,portrait,_=fixture();original=raster_mask(mask,image.size);box=(240,200,720,650)
    learned=Learned()
    original_predict=learned.predict
    def predict(image,points,**kwargs):
        value,detail=original_predict(image,points,**kwargs)
        value[:,240:]=128
        return value,detail
    learned.predict=predict
    context=HairContext(image,original,portrait=portrait,parser=Parser(),learned=learned)
    previous=np.asarray(original.crop(box));band=np.full(previous.shape,40,np.uint8);band[:200]=24
    pixels,_=context.solve(image,previous,box,semantic=previous>127,semantic_radius=40,semantic_band=band,engine=Matte())
    assert 0<pixels[160,265]<255 and 0<pixels[230,265]<255
    assert pixels[230,160]==255
    assert pixels[230,360]==0  # Explicit nonhair protection still applies.
    assert pixels[230,330]==0  # Nonhair boundary is protected too.


@pytest.mark.parametrize('invalid',['shape','dtype','class'])
def test_invalid_learned_region_fails_without_mutating_source(invalid):
    image,mask,portrait,_=fixture();snapshot=deepcopy(mask);photo=image.tobytes()
    original=raster_mask(mask,image.size);box=(240,200,720,650)
    context=HairContext(image,original,portrait=portrait,parser=Parser(),learned=Learned(invalid))
    with pytest.raises(ValueError,match='区域无效'):
        context.solve(image,np.asarray(original.crop(box)),box,semantic=np.asarray(original.crop(box))>127,engine=Matte())
    assert image.tobytes()==photo and mask==snapshot


def test_learned_model_digest_required_even_when_size_matches(tmp_path,monkeypatch):
    name='bad.onnx';(tmp_path/name).write_bytes(b'changed')
    monkeypatch.setitem(learned_models.FILES,'fixture',(name,7,sha256(b'correct').hexdigest()))
    with pytest.raises(ValueError,match='校验失败'):
        learned_models.verified_path(tmp_path,'fixture')


def test_nearby_opaque_reference_does_not_fill_protected_nonhair():
    image,mask,portrait,_=fixture();original=raster_mask(mask,image.size)
    from PIL import Image
    extended=np.array(original);extended[400:470,480:570]=255;original=Image.fromarray(extended)
    box=(240,200,720,650)
    context=HairContext(image,original,portrait=portrait,parser=Parser(),learned=Learned())
    points=[[556/999,430/899,1],[690/999,430/899,0]]
    pixels,_=context.solve(image,np.asarray(original.crop(box)),box,semantic=np.asarray(original.crop(box))>127,
                           points=points,engine=Matte())
    assert pixels[230,316]==255  # Reliable opaque hair reference.
    assert pixels[230,323]==0    # Neighbouring clothing inside its 8px disk.


def test_new_prediction_phase_is_visible_and_resets_old_tile_counts():
    owner=SimpleNamespace(_status='',changed=SimpleNamespace(emit=lambda:None))
    active={'method':'correction','hair':True,'detail_tile':8,'detail_tiles':8}
    _progress(owner,active,{'phase':'hair_uncertainty'})
    assert '透明区域' in owner._status and '可取消' in owner._status
    assert 'detail_tile' not in active


def test_shared_learned_protocol_preserves_unknown_embedding_and_class_probability_validation():
    _,labels=prompts([[20,20,2]],(64,64))
    assert labels.tolist()==[[2,3,4]]
    with pytest.raises(ValueError):
        native_trimap(np.zeros((1,3,256,256),np.float32),(64,64))
