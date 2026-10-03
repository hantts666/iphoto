"""Author decoder geometry and cascade boundaries, without accuracy claims."""
from types import SimpleNamespace
import math

import numpy as np
from PIL import Image
import pytest

from iphoto.segmentation import face_detection,retinaface


def outputs(size=(80,64)):
    count=len(retinaface.priors(size))
    return [np.zeros((1,count,n),np.float32) for n in (4,2,10)]


@pytest.mark.parametrize('size',[(80,64),(853,1280),(13,17)])
def test_anchor_layout_matches_author_row_column_and_size_order(size):
    width,height=size
    expected=[]
    for step,sizes in zip((8,16,32),((16,32),(64,128),(256,512))):
        for row in range(math.ceil(height/step)):
            for column in range(math.ceil(width/step)):
                for minimum in sizes:
                    expected.append([(column+.5)*step/width,(row+.5)*step/height,minimum/width,minimum/height])
    priors=retinaface.priors(size)
    assert np.array_equal(priors,np.array(expected,np.float32))
    assert not priors.flags.writeable


@pytest.mark.parametrize('size',[(0,20),(1281,20),(20,-1),(True,20),(20.,20),(20,)])
def test_invalid_or_unbounded_input_rejected(size):
    with pytest.raises(ValueError):retinaface.priors(size)


def test_decode_maps_offsets_and_five_landmarks_into_common_detector_protocol():
    size=(80,64);values=outputs(size)
    values[1][0,0]=[.05,.95]
    values[0][0,0]=[1,2,0,0]
    values[2][0,0]=np.tile([2,3],5)
    row=retinaface.decode(values,size)[0]
    assert row[:4]==pytest.approx([-2.4,-.8,16,16],abs=1e-6)
    assert row[4:14]==pytest.approx(np.tile([7.2,8.8],5),abs=1e-6)
    assert row[-1]==pytest.approx(.95)
    assert np.array_equal(values[0][0,0],[1,2,0,0])
    assert retinaface.decode(outputs(size),size).shape==(0,15)


def test_nms_removes_overlapping_duplicate_and_preserves_best_score():
    values=outputs()
    values[1][0,:2,1]=[.9,.99]
    values[0][0,1,2:]=math.log(.5)/.2
    result=retinaface.decode(values,(80,64))
    assert result.shape==(1,15) and result[0,-1]==pytest.approx(.99)
    assert result[0,2:4]==pytest.approx([16,16])


@pytest.mark.parametrize('kind',('shape','dtype','nan','probability','extent_overflow','point_overflow','extent_zero'))
def test_corrupt_or_overflowed_neural_outputs_are_rejected(kind):
    values=outputs();values[1][0,0,1]=.95
    if kind=='shape':values[0]=values[0][:,:-1]
    elif kind=='dtype':values[2]=values[2].astype(np.float64)
    elif kind=='nan':values[0][0,0,0]=np.nan
    elif kind=='probability':values[1][0,1,0]=1.1
    elif kind=='extent_overflow':values[0][0,0,2]=1e5
    elif kind=='point_overflow':values[2][0,0,0]=np.finfo(np.float32).max
    elif kind=='extent_zero':values[0][0,0,2]=-1e5
    with pytest.raises(ValueError):retinaface.decode(values,(80,64))


def test_digest_is_checked_before_model_loading(tmp_path,monkeypatch):
    monkeypatch.setattr(retinaface,'MODEL_DIR',tmp_path)
    (tmp_path/retinaface.NAME).write_bytes(b'0'*retinaface.SIZE)
    assert retinaface.available()
    with pytest.raises(ValueError,match='校验失败'):retinaface.verified_path()


def engine(rows,calls):
    return SimpleNamespace(setInputSize=lambda size:None,detect=lambda pixels:(calls.append(pixels.shape) or True,rows))


def face_row():
    return np.array([[100,100,200,200,150,165,250,165,200,200,175,250,225,250,.95]],np.float32)


def test_fallback_only_after_native_and_both_rotated_misses(monkeypatch):
    first,second=[],[]
    monkeypatch.setattr(face_detection,'_backend',engine(None,first))
    monkeypatch.setattr(retinaface,'available',lambda:True)
    monkeypatch.setattr(retinaface,'backend',lambda:engine(face_row(),second))
    result=face_detection.detect(Image.new('RGB',(1000,1000)))
    assert len(first)==3 and len(second)==1
    assert result[0]['detection_model']=='RetinaFace MobileNet0.25'
    assert result[0]['id']=='local-face-1' and result[0]['anchor']==[.2,.2]


@pytest.mark.parametrize('mode',('native_success','explicit_engine','rotation_disabled','unconfigured'))
def test_fallback_does_not_change_existing_success_or_explicit_engine(monkeypatch,mode):
    calls=[]
    primary=engine(face_row() if mode=='native_success' else None,calls)
    monkeypatch.setattr(face_detection,'_backend',primary)
    monkeypatch.setattr(retinaface,'available',lambda:mode!='unconfigured')
    def unexpected():raise AssertionError('Supplement should not run')
    monkeypatch.setattr(retinaface,'backend',unexpected)
    result=face_detection.detect(Image.new('RGB',(1000,1000)),engine=primary if mode=='explicit_engine' else None,retry_rotated=mode!='rotation_disabled')
    assert len(result)==(1 if mode=='native_success' else 0)
    if result:assert 'detection_model' not in result[0]


def test_rotated_success_keeps_rotation_context_without_supplement(monkeypatch):
    calls=[]
    primary=SimpleNamespace(setInputSize=lambda size:calls.append(size),detect=lambda pixels:(True,None if len(calls)==1 else face_row()))
    monkeypatch.setattr(face_detection,'_backend',primary)
    def unexpected():raise AssertionError('Rotation already found the face')
    monkeypatch.setattr(retinaface,'available',unexpected)
    result=face_detection.detect(Image.new('RGB',(1000,1000)))
    assert len(calls)==2 and result[0]['detection_rotation']==-30
