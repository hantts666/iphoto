"""Semantic confirmation of whole-face deficits; no geometry-only filling."""
from copy import deepcopy
from types import SimpleNamespace
import numpy as np
from PIL import Image
import pytest

from iphoto.segmentation import face_interior as interior,face_precision,face_skin
from iphoto.document import empty_mask,raster_mask


def scene():
    alpha=np.zeros((48,64),np.uint8);alpha[4:44,4:60]=255
    alpha[14:26,18:34]=128;alpha[16:24,20:32]=0
    context=np.full(alpha.shape,255,np.uint8)
    expected=np.zeros(alpha.shape,bool);expected[14:26,18:34]=True
    return alpha,context,expected


def test_confirmed_deficit_includes_its_faded_rim_and_never_changes_the_exterior():
    alpha,context,expected=scene();before=alpha.copy()
    assert np.array_equal(interior.candidates(alpha,context),expected)
    result,quality=interior.restore(alpha,context,expected)
    assert (result[expected]==255).all() and np.array_equal(result[~expected],before[~expected])
    assert np.array_equal(alpha,before) and quality=={'candidate_regions':1,'restored_regions':1,
        'restored_pixels':192,'retained_regions':0}


@pytest.mark.parametrize('case',['no-confirmation','one-uncertain-pixel','zero-hole-only','excluded-context','exterior-link'])
def test_unconfirmed_or_spatially_open_regions_keep_every_original_byte(case):
    alpha,context,expected=scene();confirmed=expected.copy()
    if case=='no-confirmation':confirmed[:]=False
    elif case=='one-uncertain-pixel':confirmed[15,20]=False
    elif case=='zero-hole-only':confirmed=alpha==0
    elif case=='excluded-context':context[15,20]=0
    else:alpha[:17,20]=128
    result,quality=interior.restore(alpha,context,confirmed)
    assert np.array_equal(result,alpha) and quality['restored_pixels']==0


def test_two_regions_are_independent_and_nested_foreground_is_preserved():
    alpha=np.full((64,80),255,np.uint8);alpha[:2]=0
    alpha[10:50,10:60]=0;alpha[20:40,20:50]=255;alpha[25:30,25:30]=0
    context=np.full(alpha.shape,255,np.uint8);confirmed=np.ones(alpha.shape,bool)
    confirmed[26,26]=False
    result,quality=interior.restore(alpha,context,confirmed)
    assert result[12,12]==255 and result[27,27]==0 and result[22,22]==255
    assert np.array_equal(result[:2],alpha[:2]) and quality['candidate_regions']==2 and quality['restored_regions']==1


def test_large_frame_uses_a_small_deficit_budget(monkeypatch):
    monkeypatch.setattr(interior,'MAX_PIXELS',64)
    alpha=np.full((120,140),255,np.uint8);alpha[50:54,70:74]=0
    context=np.full(alpha.shape,255,np.uint8)
    result,quality=interior.restore(alpha,context,np.ones(alpha.shape,bool))
    assert result.min()==255 and quality['restored_pixels']==16
    alpha[30:50,20:40]=0
    with pytest.raises(ValueError,match='预算'):interior.candidates(alpha,context)


@pytest.mark.parametrize('case',['float-alpha','wrong-context','empty','float-confirmation','wrong-confirmation'])
def test_invalid_inputs_cannot_produce_a_modified_mask(case):
    alpha,context,confirmed=scene()
    if case=='float-alpha':alpha=alpha.astype(float)
    elif case=='wrong-context':context=context[:-1]
    elif case=='empty':alpha=np.zeros((0,0),np.uint8);context=alpha.copy();confirmed=np.zeros((0,0),bool)
    elif case=='float-confirmation':confirmed=confirmed.astype(float)
    else:confirmed=confirmed[:-1]
    with pytest.raises(ValueError):interior.restore(alpha,context,confirmed)


@pytest.mark.parametrize('opinion',['face','background','hair','uncertain','wrong-label'])
def test_native_semantic_support_uses_face_classes_and_confidence_only_on_candidates(opinion):
    labels=np.ones((8,12),np.uint8);candidate=np.zeros(labels.shape,bool);candidate[2:5,3:8]=True
    scores=np.zeros((1,11,512,512),np.float32)
    if opinion!='uncertain':scores[:,1 if opinion in ('face','wrong-label') else 0 if opinion=='background' else 10]=12
    if opinion=='wrong-label':labels[:]=17
    result=face_precision.native_face_support(scores,np.eye(3,dtype=np.float32),labels,candidate)
    assert np.array_equal(result,candidate if opinion=='face' else np.zeros(labels.shape,bool))


def test_large_native_labels_do_not_require_a_large_candidate_tensor(monkeypatch):
    monkeypatch.setattr(interior,'MAX_PIXELS',4)
    labels=np.ones((80,90),np.uint8);candidate=np.zeros(labels.shape,bool);candidate[40,40]=True
    scores=np.zeros((1,11,512,512),np.float32);scores[:,1]=12
    assert face_precision.native_face_support(scores,np.eye(3,dtype=np.float32),labels,candidate).sum()==1
    candidate[:]=True
    with pytest.raises(ValueError,match='预算'):
        face_precision.native_face_support(scores,np.eye(3,dtype=np.float32),labels,candidate)


def test_sparse_confirmation_never_decodes_native_labels_and_parts_reuse_its_scores(monkeypatch):
    parser=face_precision.FaceParser.__new__(face_precision.FaceParser);parser._cached=None;inferences=[];decodes=[]
    scores=np.zeros((1,11,512,512),np.float32);scores[:,1]=12
    parser.session=SimpleNamespace(run=lambda *args:inferences.append(True) or [scores])
    image=Image.new('RGB',(32,32));points=np.array([[8,10],[24,10],[16,18],[10,26],[22,26]],np.float32)
    pending=np.zeros((32,32),bool);pending[16:20,14:18]=True
    monkeypatch.setattr(face_precision,'native_labels',lambda *args:pytest.fail('Sparse check must not decode a native class map'))
    assert np.array_equal(parser.confirm_face(image,points,pending),pending)
    assert np.array_equal(parser.confirm_face(image,points,pending),pending) and len(inferences)==1
    assert parser._cached[1] is None and parser._cached[2].nbytes<=face_precision.MAX_CACHE_BYTES
    def decode(scores,matrix,size):decodes.append(True);return np.ones((size[1],size[0]),np.uint8)
    monkeypatch.setattr(face_precision,'native_labels',decode)
    labels=parser.predict_native(image,points)
    assert len(decodes)==1 and len(inferences)==1 and not labels.flags.writeable
    parser.predict_part(image,points,'nose')
    assert len(decodes)==1 and len(inferences)==1


@pytest.mark.parametrize('outcome',['confirm','reject','failure','no-hole','region','skin'])
def test_real_face_pipeline_preserves_other_scopes_and_falls_back_atomically(monkeypatch,outcome):
    width,height=320,240;image=Image.new('RGB',(width,height),(130,90,70))
    labels=np.zeros((height,width),np.uint8);labels[20:220,30:290]=1;labels[115:126,155:166]=10
    if outcome!='no-hole':labels[60:85,80:105]=17
    class Basic:
        def predict_native(self,picture,**kwargs):return labels.copy()
    calls=[]
    class Fine:
        def confirm_face(self,picture,points,pending,**kwargs):
            calls.append(pending.copy())
            if outcome=='failure':raise ValueError('unavailable fine model')
            return pending if outcome=='confirm' else np.zeros(pending.shape,bool)
    basic=Basic();monkeypatch.setattr(face_skin,'backend',lambda **kwargs:basic)
    monkeypatch.setattr(face_precision,'available',lambda:True)
    def load_fine(**kwargs):
        if outcome in ('no-hole','region'):pytest.fail('This face needs no interior model')
        return Fine()
    monkeypatch.setattr(face_precision,'backend',load_fine)
    features={'eyes':[[.35,.3],[.65,.3]],'mouth':[[.4,.7],[.6,.7]]}
    options={'crop':[0,0,1,1],'features':features,'target':'face_skin' if outcome=='skin' else 'face',
             'scope':'region' if outcome=='region' else 'full'}
    hint=empty_mask(True);points=[[.5,.5,1]]
    before,_=face_skin.segment(image,hint,points,engine=basic,**options)
    original=deepcopy(hint)
    after,quality=face_skin.segment(image,hint,points,**options)
    base=np.asarray(raster_mask(before,image.size));actual=np.asarray(raster_mask(after,image.size))
    assert hint==original
    if outcome=='confirm':
        assert calls and (actual[calls[0]]==255).all() and np.array_equal(actual[~calls[0]],base[~calls[0]])
        assert quality['face_interior']['restored_pixels']>0 and not quality['continuous_boundary']
    else:
        assert np.array_equal(actual,base)
        assert bool(calls)==(outcome in ('reject','failure'))
        if outcome=='reject':assert quality['face_interior']['restored_pixels']==0
        if outcome=='failure':assert '未完成' in quality['warnings'][0] and 'face_interior' not in quality
