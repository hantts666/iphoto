"""Coordinate and fallback contracts; synthetic data is not an accuracy score."""
from types import SimpleNamespace
import numpy as np
from PIL import Image
import pytest

from iphoto.segmentation import face_precision as precision, face_skin
from iphoto.document import raster_mask
from test_face_parts import labels,rect


def test_alignment_maps_author_five_points_to_the_fixed_448_template():
    points=np.array([[196,226],[316,226],[256,286],[220,360.4],[292,360.4]],np.float32)
    source=(points+np.array([30,80],np.float32))*2
    matrix=precision.alignment(source)
    actual=source@matrix[:2,:2].T+matrix[:2,2]
    assert np.allclose(actual,points*447/512,atol=.0001)


@pytest.mark.parametrize('points',[np.zeros((5,2)),np.full((5,2),np.nan),np.zeros((4,2))])
def test_invalid_alignment_is_rejected(points):
    with pytest.raises(ValueError):precision.alignment(points)


def test_warp_round_trip_and_linear_center_preserve_subpixel_coordinates():
    coords=np.array([[100,200],[220,220],[320,400]],np.float32)
    assert np.allclose(precision.warp(precision.warp(coords),inverse=True),coords,atol=.0001)
    assert np.array_equal(precision.warp(coords[1:2]),coords[1:2])


def test_sampler_uses_zero_padding_and_continuous_bilinear_weights():
    pixels=np.array([[[1],[2]],[[3],[4]]],np.float32)
    coords=np.array([[[.3,.4],[-.5,0],[2,0]]],np.float32)
    assert precision.sample_bilinear(pixels,coords).ravel()==pytest.approx([2.1,.5,0])


def test_native_classification_warps_logits_before_argmax_and_maps_author_lip_ids():
    scores=np.zeros((1,11,512,512),np.float32);scores[:,7]=4
    result=precision.native_labels(scores,np.eye(3,dtype=np.float32),(19,13))
    assert result.shape==(13,19) and result.dtype==np.uint8 and np.all(result==12)
    scores[:,9]=5
    assert np.all(precision.native_labels(scores,np.eye(3,dtype=np.float32),(19,13))==13)


@pytest.mark.parametrize('invalid',('nan','shape','dtype','matrix','size'))
def test_corrupt_scores_and_unbounded_coordinates_are_rejected(invalid):
    scores=np.zeros((1,11,512,512),np.float32);matrix=np.eye(3,dtype=np.float32);size=(20,20)
    if invalid=='nan':scores[0,0,0,0]=np.nan
    elif invalid=='shape':scores=scores[:,:,:10,:]
    elif invalid=='dtype':scores=scores.astype(np.float64)
    elif invalid=='matrix':matrix[0,0]=np.nan
    elif invalid=='size':size=(True,20)
    with pytest.raises(ValueError):precision.native_labels(scores,matrix,size)


def test_model_digest_is_checked_before_loading(tmp_path,monkeypatch):
    monkeypatch.setattr(precision,'MODEL_DIR',tmp_path);monkeypatch.setattr(precision,'SIZE',3)
    (tmp_path/precision.NAME).write_bytes(b'bad')
    assert precision.available()
    with pytest.raises(ValueError,match='校验失败'):precision.verified_path()


@pytest.mark.parametrize('mode',('strong','unconfigured','failure','explicit','whole'))
def test_only_known_facial_parts_use_precision_and_failures_keep_base_parser(monkeypatch,mode):
    image=Image.new('RGB',(240,120));classes=labels();calls=[];basic=[]
    features={'eyes':[[.1,.2],[.4,.2]],'mouth':[[.15,.6],[.3,.6]]}
    monkeypatch.setattr(precision,'available',lambda:mode!='unconfigured')
    def predict(patch,points):
        calls.append(points)
        if mode=='failure':raise ValueError('model failure')
        return classes
    monkeypatch.setattr(precision,'backend',lambda:SimpleNamespace(predict_native=predict))
    base=SimpleNamespace(predict_native=lambda patch:basic.append(True) or classes)
    monkeypatch.setattr(face_skin,'backend',lambda:base)
    mask,quality=face_skin.segment(image,rect(),[[55/240,47/120,1]],crop=[0,0,1,1],
        target='face_skin' if mode=='whole' else 'face',scope='full' if mode=='whole' else 'region',
        part='all' if mode=='whole' else 'lips',features=features,engine=base if mode=='explicit' else None)
    assert bool(calls)==(mode in ('strong','failure'))
    assert bool(basic)==(mode!='strong')
    assert quality['model'].startswith('FaRL' if mode=='strong' else 'BiSeNet')
    assert any('基础面部' in warning for warning in quality['warnings'])==(mode=='failure')
    if mode!='whole':assert not np.any(np.asarray(raster_mask(mask,image.size))[~np.isin(classes,(12,13))])


def test_one_face_cache_reuses_semantics_but_new_pixels_transform_or_size_invalidate(monkeypatch):
    parser=precision.FaceParser.__new__(precision.FaceParser);parser._cached=None;calls=[]
    scores=np.zeros((1,11,512,512),np.float32);scores[:,1]=1
    parser.session=SimpleNamespace(run=lambda *args:calls.append(True) or [scores])
    points=np.array([[2,3],[8,3],[5,6],[3,9],[7,9]],np.float32)
    image=Image.new('RGB',(12,12),(100,90,80))
    first=parser.predict_native(image,points)
    assert parser.predict_native(image.copy(),points.copy()) is first and len(calls)==1
    assert not first.flags.writeable
    image.putpixel((1,1),(0,0,0));parser.predict_native(image,points);assert len(calls)==2
    parser.predict_native(image,points+.25);assert len(calls)==3
    parser.predict_native(image.resize((13,12)),points+.25);assert len(calls)==4
    parser.predict_native(Image.new('RGB',(12,12),(100,90,80)),points);assert len(calls)==5
    assert parser._cached[1].nbytes+parser._cached[2].nbytes<=precision.MAX_CACHE_BYTES
    monkeypatch.setattr(precision,'MAX_CACHE_BYTES',1)
    parser.predict_native(image,points)
    assert parser._cached is None
