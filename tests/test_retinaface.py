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


@pytest.mark.parametrize('mode',('explicit_engine','unconfigured'))
def test_supplement_does_not_run_with_explicit_engine_or_unconfigured_model(monkeypatch,mode):
    calls=[]
    primary=engine(None,calls)
    monkeypatch.setattr(face_detection,'_backend',primary)
    monkeypatch.setattr(retinaface,'available',lambda:mode!='unconfigured')
    supplement_calls=[]
    def unexpected():supplement_calls.append(True);raise AssertionError('Supplement should not run')
    monkeypatch.setattr(retinaface,'backend',unexpected)
    result=face_detection.detect(Image.new('RGB',(1000,1000)),engine=primary if mode=='explicit_engine' else None)
    assert result==[] and supplement_calls==[]


def test_rotated_success_keeps_rotation_context_while_supplement_checks_for_more(monkeypatch):
    calls=[]
    primary=SimpleNamespace(setInputSize=lambda size:calls.append(size),detect=lambda pixels:(True,None if len(calls)==1 else face_row()))
    monkeypatch.setattr(face_detection,'_backend',primary)
    supplementary=[]
    monkeypatch.setattr(retinaface,'available',lambda:True)
    monkeypatch.setattr(retinaface,'backend',lambda:engine(None,supplementary))
    result=face_detection.detect(Image.new('RGB',(1000,1000)))
    assert len(calls)==2 and len(supplementary)==1 and result[0]['detection_rotation']==-30


def test_supplement_keeps_existing_geometry_and_id_when_new_face_is_to_its_left(monkeypatch):
    primary=face_row();primary[0,[0,4,6,8,10,12]]+=400
    existing=face_detection.detect(Image.new('RGB',(1000,1000)),engine=engine(primary,[]))
    repeated=primary.copy();repeated[0,0]-=10;repeated[0,2]+=20;repeated[0,-1]=.999
    calls=[]
    monkeypatch.setattr(face_detection,'_backend',engine(primary,[]))
    monkeypatch.setattr(retinaface,'available',lambda:True)
    monkeypatch.setattr(retinaface,'backend',lambda:engine(np.concatenate([repeated,face_row()]),calls))
    result=face_detection.detect(Image.new('RGB',(1000,1000)))
    assert len(result)==2 and len(calls)==1 and result[0]==existing[0]
    assert result[1]['id']=='local-face-2' and result[1]['anchor']==[.2,.2]


@pytest.mark.parametrize('has_primary',(False,True))
def test_supplement_failure_keeps_existing_faces_and_reports_it(monkeypatch,has_primary):
    primary=face_row() if has_primary else None
    expected=face_detection.detect(Image.new('RGB',(1000,1000)),engine=engine(primary,[]))
    monkeypatch.setattr(face_detection,'_backend',engine(primary,[]))
    monkeypatch.setattr(retinaface,'available',lambda:True)
    def failure():raise ValueError('corrupt supplemental model')
    monkeypatch.setattr(retinaface,'backend',failure)
    warnings=[]
    assert face_detection.detect(Image.new('RGB',(1000,1000)),warnings=warnings)==expected
    assert len(warnings)==1 and '未完成' in warnings[0]


def test_overlapping_boxes_with_separate_noses_are_not_the_same_identity():
    image=Image.new('RGB',(1000,1000));first=face_row();second=first.copy()
    first[0,8]=125;second[0,0]+=40;second[0,8]=330
    faces=[face_detection.detect(image,engine=engine(rows,[]))[0] for rows in (first,second)]
    assert not face_detection._same_face(*faces)


def test_inner_face_box_with_nearby_nose_is_deduplicated():
    image=Image.new('RGB',(1000,1000));first=face_row();second=first.copy()
    second[0,:4]=[150,150,100,100]
    faces=[face_detection.detect(image,engine=engine(rows,[]))[0] for rows in (first,second)]
    assert face_detection._same_face(*faces)


def test_primary_face_limit_skips_an_unnecessary_supplement(monkeypatch):
    rows=np.tile(face_row(),(16,1));rows[:,0]=np.arange(16)*40
    rows[:,4:14:2]+=np.arange(16)[:,None]*40-100
    monkeypatch.setattr(face_detection,'_backend',engine(rows,[]))
    calls=[]
    monkeypatch.setattr(retinaface,'available',lambda:calls.append(True) or True)
    result=face_detection.detect(Image.new('RGB',(1000,1000)))
    assert len(result)==16 and calls==[]


def test_supplement_is_capped_without_discarding_primary_identity(monkeypatch):
    primary=face_row();primary[0,[0,4,6,8,10,12]]+=600
    expected=face_detection.detect(Image.new('RGB',(1000,1000)),engine=engine(primary,[]))[0]
    rows=[]
    for index in range(16):
        x=40+(index%4)*160;y=40+(index//4)*220
        rows.append([x,y,20,20,x+5,y+5,x+15,y+5,x+10,y+10,x+7,y+15,x+13,y+15,.8+index*.01])
    candidates=np.array(rows,np.float32)
    monkeypatch.setattr(face_detection,'_backend',engine(primary,[]))
    monkeypatch.setattr(retinaface,'available',lambda:True)
    monkeypatch.setattr(retinaface,'backend',lambda:engine(candidates,[]))
    result=face_detection.detect(Image.new('RGB',(1000,1000)))
    assert len(result)==16 and result[0]==expected
    assert [f['id'] for f in result]==[f'local-face-{i}' for i in range(1,17)]
    assert all(f['anchor']!=[.05,.05] for f in result[1:])


def landmark_pair():
    image = Image.new('RGB',(1000,700))
    primary = face_detection.detect(image,engine=engine(face_row(),[]))[0]
    primary.update(detection_rotation=-30,detection_score=.82,
                   face_features={'eyes':[[.19,.23],[.20,.23]],'mouth':[[.19,.36],[.20,.36]]})
    candidate = face_detection.detect(image,engine=engine(face_row(),[]))[0]
    candidate['detection_score'] = .93
    return primary,candidate,image.size


@pytest.mark.parametrize('case',('valid','ordinary','low_score','healthy_pair','collapsed_candidate','ambiguous_profile'))
def test_landmark_replacement_requires_collapsed_rotated_and_clearer_neural_geometry(case):
    first,second,size = landmark_pair()
    if case=='ordinary':first.pop('detection_rotation')
    elif case=='low_score':second['detection_score']=.85
    elif case=='healthy_pair':first['face_features']['eyes']=second['face_features']['eyes']
    elif case=='collapsed_candidate':second['face_features']['mouth']=first['face_features']['mouth']
    elif case=='ambiguous_profile':second['face_features']['eyes']=[[.19,.23],[.212,.23]]
    assert face_detection._clearer_landmarks(first,second,np.array(size)) == (case=='valid')


def test_rotated_matching_landmarks_are_replaced_without_new_identity_or_boundary(monkeypatch):
    from copy import deepcopy
    image=Image.new('RGB',(1000,1000));collapsed=face_row()
    collapsed[0,[0,4,6,8,10,12]] += 400
    collapsed[0,[1,5,7,9,11,13]] += 400
    collapsed[0,[4,6,10,12]]=[597,601,597,601];collapsed[0,-1]=.82
    calls=[]
    def primary():
        calls.clear()
        return SimpleNamespace(setInputSize=lambda size:calls.append(size),
            detect=lambda pixels:(True,None if len(calls)==1 else collapsed))
    monkeypatch.setattr(face_detection,'_backend',primary())
    monkeypatch.setattr(retinaface,'available',lambda:False)
    before=deepcopy(face_detection.detect(image)[0])
    bounds=np.asarray(before['mask']['ops'][0]['points']);left,top=bounds.min(0);right,bottom=bounds.max(0)
    width=right-left;height=bottom-top;anchor=np.array(before['anchor'])+[.003,.003]
    points=np.array([[left+width*.25,top+height*.3],[left+width*.75,top+height*.3],anchor,
                     [left+width*.3,top+height*.75],[left+width*.7,top+height*.75]])*1000
    candidate=np.array([[left*1000,top*1000,width*1000,height*1000,*points.ravel(),.99]],np.float32)
    monkeypatch.setattr(face_detection,'_backend',primary())
    monkeypatch.setattr(retinaface,'available',lambda:True)
    monkeypatch.setattr(retinaface,'backend',lambda:engine(candidate,[]))
    after=face_detection.detect(image)
    assert len(after)==1 and after[0]['landmark_model']=='RetinaFace MobileNet0.25'
    expected=face_detection.detect(image,engine=engine(candidate,[]))[0]
    assert after[0]['anchor']==expected['anchor'] and after[0]['face_features']==expected['face_features']
    for key in before.keys()-{'anchor','face_features'}:assert after[0][key]==before[key]


def test_ambiguous_supplement_landmarks_keep_primary_estimate(monkeypatch):
    image=Image.new('RGB',(1000,1000));rows=face_row();rows[0,:14:2]+=400;rows[0,1:14:2]+=400
    rows[0,[4,6,10,12]]=[597,601,597,601];rows[0,-1]=.82
    def primary():
        calls=[]
        return SimpleNamespace(setInputSize=lambda size:calls.append(size),
            detect=lambda pixels:(True,None if len(calls)==1 else rows))
    monkeypatch.setattr(face_detection,'_backend',primary());monkeypatch.setattr(retinaface,'available',lambda:False)
    before=face_detection.detect(image)
    points=np.asarray(before[0]['mask']['ops'][0]['points']);left,top=points.min(0);right,bottom=points.max(0)
    anchor=np.array(before[0]['anchor'])*1000
    duplicate=np.array([[left*1000,top*1000,(right-left)*1000,(bottom-top)*1000,
        left*1000+50,top*1000+50,right*1000-50,top*1000+50,*anchor,
        left*1000+60,bottom*1000-50,right*1000-60,bottom*1000-50,.99]],np.float32)
    monkeypatch.setattr(face_detection,'_backend',primary());monkeypatch.setattr(retinaface,'available',lambda:True)
    monkeypatch.setattr(retinaface,'backend',lambda:engine(np.concatenate([duplicate,duplicate]),[]))
    assert face_detection.detect(image)==before
