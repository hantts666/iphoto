"""Detector geometry, typed routing, and mask semantics; not accuracy fixtures."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_tasks import parse_selection
from iphoto.document import raster_mask, validate_mask
from iphoto.scene import parse_scene, with_local_faces
from iphoto.segmentation import face_detection, face_skin
from test_face_skin import labels, mask


def detector(rows):
    return SimpleNamespace(setInputSize=lambda size:None, detect=lambda pixels:(True,rows))


def row(x=100,y=100,score=.95):
    return [x,y,200,200,x+50,y+65,x+150,y+65,x+100,y+100,x+75,y+150,x+125,y+150,score]


def detected():
    return face_detection.detect(Image.new('RGB',(1000,1000)),engine=detector(np.array([row()])))


def test_detector_input_is_bounded_and_geometry_maps_to_source_without_mutating_it():
    image=Image.new('RGB',(4000,6000),'gray')
    before=image.tobytes()
    sizes=[]
    engine=SimpleNamespace(setInputSize=sizes.append,detect=lambda pixels:(True,np.array([row(5,5),row(400,700)])))
    result=face_detection.detect(image,engine=engine)
    assert sizes==[(853,1280)] and len(result)==2
    assert result[0]['anchor']==pytest.approx([105/853,105/1280])
    assert result[0]['skin_crop'][:2]==[0,0]
    assert result[0]['mask_target']=='face'
    assert [f['id'] for f in result]==['local-face-1','local-face-2']
    assert image.tobytes()==before


@pytest.mark.parametrize('rows',[np.zeros((2,14)),np.full((1,15),np.nan),np.zeros(15)])
def test_invalid_detector_output_is_rejected(rows):
    with pytest.raises(ValueError):face_detection.detect(Image.new('RGB',(500,500)),engine=detector(rows))


def test_detector_rejects_small_low_confidence_and_invalid_anchor():
    invalid=row();invalid[8]=900
    small=row();small[2]=4
    assert face_detection.detect(Image.new('RGB',(1000,1000)),engine=detector(np.array([row(score=.79),invalid,small])))==[]
    assert face_detection.detect(Image.new('RGB',(1000,1000)),engine=detector(None))==[]


def test_partially_visible_face_keeps_clipped_landmarks_in_valid_catalog():
    output = row(x=-100,y=800)
    output[4] = -40
    output[13] = 1040
    faces = face_detection.detect(Image.new('RGB',(1000,1000)),engine=detector(np.array([output])))
    assert len(faces) == 1
    features = faces[0]['face_features']
    assert features['eyes'][0][0] == 0 and features['mouth'][1][1] == 1
    assert face_detection.validate_features(features) == features
    assert with_local_faces({'summary':'边缘人物','objects':[]},faces)['objects'][0]['id']=='local-face-1'


def test_model_hash_is_checked_before_loading(tmp_path,monkeypatch):
    monkeypatch.setattr(face_detection,'MODEL_DIR',tmp_path)
    (tmp_path/face_detection.NAME).write_bytes(b'0'*face_detection.SIZE)
    assert face_detection.available()
    with pytest.raises(ValueError,match='校验失败'):face_detection.verified_path()


def completion(value):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}


@pytest.mark.parametrize('target',['object','face','face_skin'])
def test_scene_and_direct_selection_keep_semantic_target_and_legacy_support(target):
    value={'status':'selected','summary':'目标','box':[100,100,300,300],'point':[200,200],'mask_target':target}
    selection=parse_selection(completion(value))
    scene=parse_scene(completion({'status':'analyzed','summary':'目标','objects':[
        {'name':'目标','category':'人脸','box':value['box'],'point':value['point'],'mask_target':target}]}))
    assert selection.get('mask_target','object')==target
    assert scene['objects'][0].get('mask_target','object')==target
    value.pop('mask_target')
    assert parse_selection(completion(value)).get('mask_target','object')=='object'


def test_local_inventory_is_idempotent_and_only_replaces_matching_cloud_faces():
    faces=detected()
    other=deepcopy(faces[0]);other.update(id='cloud-other',name='另一个人脸')
    other['mask']['ops'][0]['points']=[[.6,.6],[.8,.6],[.8,.8],[.6,.8]]
    matching=deepcopy(faces[0]);matching['id']='cloud-face'
    person=deepcopy(faces[0]);person.update(id='person',mask_target='object',name='人物')
    catalog={'summary':'人物','objects':[person,matching,other]}
    before=deepcopy(catalog)
    result=with_local_faces(catalog,faces)
    assert [o['id'] for o in result['objects']]==['person','cloud-other','local-face-1']
    assert with_local_faces(result,faces)==result and catalog==before
    assert result['objects'][-1]['skin_crop']==faces[0]['skin_crop']
    stale=deepcopy(faces[0]);stale['mask']=deepcopy(other['mask'])
    restored=with_local_faces({'summary':'旧定位','objects':[stale]},faces)
    assert [o['id'] for o in restored['objects']]==['local-face-1']
    assert restored['objects'][0]['mask']==faces[0]['mask']


def test_face_includes_eyes_and_lips_while_skin_and_both_targets_protect_neck():
    semantic=labels();semantic[310:350,210:280]=4;semantic[350:365,210:280]=12;semantic[370:400,210:280]=14
    image=Image.new('RGB',(512,512))
    masks={target:face_skin.segment(image,mask(),[[.3,.3,1]],crop=[0,0,1,1],
                                   engine=SimpleNamespace(predict=lambda _:semantic),target=target)[0]
           for target in ('face','face_skin')}
    for target,result in masks.items():
        assert validate_mask(result)['semantic_target']==target
        alpha=raster_mask(result,image.size)
        assert alpha.getpixel((240,385))==0
        assert alpha.getpixel((240,330))==(255 if target=='face' else 0)
        assert alpha.getpixel((240,358))==(255 if target=='face' else 0)


def test_occluded_detector_anchor_recovers_only_a_visible_nose_inside_its_hint():
    semantic=labels();semantic[140:180,140:180]=18
    image=Image.new('RGB',(512,512))
    result,quality=face_skin.segment(image,mask(),[[.3,.3,1]],crop=[0,0,1,1],recover_anchor=True,
                                     engine=SimpleNamespace(predict=lambda _:semantic))
    assert quality['anchor_recovered'] and raster_mask(result,image.size).getpixel((210,215))==255
    semantic[semantic==10]=1
    with pytest.raises(ValueError,match='定位点'):
        face_skin.segment(image,mask(),[[.3,.3,1]],crop=[0,0,1,1],recover_anchor=True,
                          engine=SimpleNamespace(predict=lambda _:semantic))


def test_face_association_keeps_ambiguous_and_nonoverlapping_hints_for_cloud_grounding():
    faces=detected()
    assert face_detection.match_hint(faces[0]['mask'],faces)==faces[0]
    second=deepcopy(faces[0]);second['id']='local-face-2'
    assert face_detection.match_hint(faces[0]['mask'],faces+[second]) is None
    away=deepcopy(faces[0]['mask']);away['ops'][0]['points']=[[.7,.7],[.9,.7],[.9,.9],[.7,.9]]
    assert face_detection.match_hint(away,faces) is None


def test_rotated_retry_maps_detector_points_back_to_original_pixels(monkeypatch):
    import cv2
    width,height=500,700;angle=-30
    transform=cv2.getRotationMatrix2D((width/2,height/2),angle,1)
    cosine,sine=abs(transform[0,0]),abs(transform[0,1])
    size=(int(np.ceil(height*sine+width*cosine)),int(np.ceil(height*cosine+width*sine)))
    transform[:,2]+=np.array(size)/2-np.array((width,height))/2
    expected=np.array([240,350])
    rotated=transform[:,:2]@expected+transform[:,2]
    result=row(x=float(rotated[0]-100),y=float(rotated[1]-100))
    calls=[]
    engine=SimpleNamespace(setInputSize=lambda size:calls.append(size),
                           detect=lambda pixels:(True,None if len(calls)==1 else np.array([result])))
    monkeypatch.setattr(face_detection,'_backend',engine)
    faces=face_detection.detect(Image.new('RGB',(width,height)))
    assert len(calls)==2 and len(faces)==1
    assert np.array(faces[0]['anchor'])*(width,height)==pytest.approx(expected,abs=1e-6)
    assert faces[0]['detection_rotation']==-30


def test_ai_landmarks_protect_facial_features_misclassified_as_skin_but_whole_face_includes_them():
    features={'eyes':[[.26,.30],[.52,.30]],'mouth':[[.34,.6],[.5,.6]]}
    image=Image.new('RGB',(512,512))
    semantic=labels()
    for target in ('face_skin','face'):
        result,quality=face_skin.segment(image,mask(),[[.3,.3,1]],crop=[0,0,1,1],target=target,
                                        features=features,engine=SimpleNamespace(predict=lambda _:semantic))
        alpha=raster_mask(result,image.size)
        for point in ((133,154),(266,154),(215,307)):
            assert alpha.getpixel(point)==(0 if target=='face_skin' else 255)
        assert quality['landmark_protection']==(target=='face_skin')


@pytest.mark.parametrize('bad',[None,{}, {'eyes':[[.2,.3],[.4,.3]],'mouth':[]},
                                {'eyes':[[True,.3],[.4,.3]],'mouth':[[.3,.5],[.4,.5]]},
                                {'eyes':[[float('nan'),.3],[.4,.3]],'mouth':[[.3,.5],[.4,.5]]}])
def test_invalid_landmarks_are_rejected_before_mask_publication(bad):
    with pytest.raises(ValueError):face_detection.validate_features(bad)
