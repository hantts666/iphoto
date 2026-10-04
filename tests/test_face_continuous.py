"""Class-score transitions, scope and anatomy; not a model accuracy metric."""
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from iphoto.document import raster_mask
from iphoto.segmentation import face_precision as precision, face_skin
from test_face_parts import labels,rect


def test_ambiguous_skin_can_form_an_edge_but_other_anatomy_cannot():
    scores=np.zeros((1,11,512,512),np.float32)
    scores[:,7]=np.log(5);scores[:,9]=np.log(4);scores[:,1]=np.log(10)
    field=np.ones((20,30),np.uint8);field[:,10:20]=12;field[5:10,12:18]=11
    alpha=precision.native_part_alpha(scores,np.eye(3,dtype=np.float32),field,'lips')
    # Combined lips = 9/(9+10+8), giving a weak continuous transition on skin.
    assert alpha[2,2]==pytest.approx(42.5,abs=.5) and alpha[2,12]==pytest.approx(42.5,abs=.5)
    assert not np.any(alpha[field==11])
    field[:]=1
    assert not np.any(precision.native_part_alpha(scores,np.eye(3,dtype=np.float32),field,'lips'))


@pytest.mark.parametrize('part,index,semantic',[('lips',7,12),('nose',6,10)])
def test_confident_parts_have_solid_interiors_and_protected_features(part,index,semantic):
    scores=np.zeros((1,11,512,512),np.float32);scores[:,index]=30
    field=np.full((18,25),semantic,np.uint8);field[4:8,4:8]=11;field[:,0]=4
    alpha=precision.native_part_alpha(scores,np.eye(3,dtype=np.float32),field,part)
    assert alpha[10,10]==255 and not np.any(alpha[field==11]) and not np.any(alpha[field==4])


@pytest.mark.parametrize('invalid',['part','scores','matrix','labels'])
def test_invalid_continuous_inputs_do_not_produce_a_mask(invalid):
    scores=np.zeros((1,11,512,512),np.float32);matrix=np.eye(3,dtype=np.float32)
    field=np.full((10,10),12,np.uint8);part='lips'
    if invalid=='part':part='cheek'
    elif invalid=='scores':scores[0,1,1,1]=np.nan
    elif invalid=='matrix':matrix[0,0]=np.nan
    else:field=field.astype(np.float32)
    with pytest.raises(ValueError):precision.native_part_alpha(scores,matrix,field,part)


@pytest.mark.parametrize('limited',[False,True])
def test_initial_lip_selection_keeps_soft_edges_mouth_holes_person_and_spatial_limits(monkeypatch,limited):
    image=Image.new('RGB',(240,120),(170,110,80));source=image.tobytes();classes=labels()
    score_alpha=np.zeros(classes.shape,np.uint8);score_alpha[60:87,35:85]=170
    score_alpha[25:35,30:45]=255;score_alpha[:,130:]=255
    monkeypatch.setattr(precision,'available',lambda:True)
    monkeypatch.setattr(precision,'backend',lambda:SimpleNamespace(predict_part=lambda *args,**kwargs:(classes,score_alpha)))
    features={'eyes':[[.1,.2],[.4,.2]],'mouth':[[.15,.6],[.3,.6]]}
    hint=rect(.16,.5,.3,.71) if limited else rect()
    mask,quality=face_skin.segment(image,hint,[[55/240,47/120,1]],crop=[0,0,1,1],
        features=features,target='face',scope='region',context_hint=rect(0,0,.5,1),part='lips')
    alpha=np.asarray(raster_mask(mask,image.size));extent=np.asarray(raster_mask(hint,image.size))
    assert quality['continuous_boundary'] and mask['face_part']=='lips'
    assert not np.any(alpha[classes==11]) and not np.any(alpha[classes==4]) and not np.any(alpha[:,120:])
    assert alpha[61,50]>0 and classes[61,50]==1  # the old binary erosion lost this edge
    assert not np.any(alpha[extent==0]) and image.tobytes()==source


def test_native_and_part_predictions_share_scores_with_a_byte_budget(monkeypatch):
    parser=precision.FaceParser.__new__(precision.FaceParser);parser._cached=None;calls=[]
    scores=np.zeros((1,11,512,512),np.float32);scores[:,7]=30
    parser.session=SimpleNamespace(run=lambda *args:calls.append(True) or [scores.copy()])
    image=Image.new('RGB',(12,12),(100,90,80));points=np.array([[2,3],[8,3],[5,6],[3,9],[7,9]],np.float32)
    first=parser.predict_native(image,points)
    classes,alpha=parser.predict_part(image,points,'lips')
    assert classes is first and len(calls)==1 and alpha.max()==255
    assert sum(v.nbytes for v in parser._cached[1:])<=precision.MAX_CACHE_BYTES
    assert all(not v.flags.writeable for v in parser._cached[1:])
    monkeypatch.setattr(precision,'MAX_CACHE_BYTES',scores.nbytes)
    parser.predict_part(image,points+.1,'lips')
    assert parser._cached is None and len(calls)==2


def test_large_native_maps_reuse_exact_scores_with_lossless_packing(monkeypatch):
    parser=precision.FaceParser.__new__(precision.FaceParser);parser._cached=None;calls=[]
    scores=np.zeros((1,11,512,512),np.float32);scores[:,7]=30
    parser.session=SimpleNamespace(run=lambda *args:calls.append(True) or [scores.copy()])
    monkeypatch.setattr(precision,'MAX_CACHE_BYTES',scores.nbytes+64)
    image=Image.new('RGB',(12,12));points=np.array([[2,3],[8,3],[5,6],[3,9],[7,9]],np.float32)
    first,alpha=parser.predict_part(image,points,'lips')
    packed=parser._cached[1]
    assert isinstance(packed,tuple) and len(packed[1])+parser._cached[2].nbytes<=precision.MAX_CACHE_BYTES
    second,next_alpha=parser.predict_part(image.copy(),points.copy(),'lips')
    assert np.array_equal(first,second) and np.array_equal(alpha,next_alpha) and not second.flags.writeable
    assert len(calls)==1


@pytest.mark.parametrize('case',['shape','empty'])
def test_invalid_part_field_does_not_publish(monkeypatch,case):
    classes=labels()
    field=np.zeros((2,2) if case=='shape' else classes.shape,np.uint8)
    monkeypatch.setattr(precision,'available',lambda:True)
    monkeypatch.setattr(precision,'backend',lambda:SimpleNamespace(predict_part=lambda *args,**kw:(classes,field)))
    with pytest.raises(ValueError,match='连续五官边缘无效' if case=='shape' else '五官类别分数不足'):
        face_skin.segment(Image.new('RGB',(240,120)),rect(),[[55/240,47/120,1]],crop=[0,0,1,1],
            features={'eyes':[[.1,.2],[.4,.2]],'mouth':[[.15,.6],[.3,.6]]},target='face',scope='region',part='lips')
