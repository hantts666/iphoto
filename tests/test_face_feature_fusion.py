"""Two neural domains refine internal skin without losing identity/protection."""
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask, raster_mask
from iphoto.segmentation import face_precision, face_skin
from iphoto.segmentation.face_feature_fusion import refine, OUTER_PROTECTED


def rect(left=0, top=0, right=1, bottom=1):
    result = empty_mask()
    result['ops'] = [{'kind':'polygon', 'mode':'add',
                      'points':[[left,top],[right,top],[right,bottom],[left,bottom]]}]
    return result


def fixture():
    base = np.zeros((120, 200), np.uint8)
    base[10:110, 10:95] = 1
    base[50:64, 45:58] = 10
    fine = base.copy()
    fine[26:34, 24:40] = 4
    fine[26:34, 64:80] = 5
    fine[76:82, 34:64] = 12
    fine[82:87, 34:64] = 13
    skin = np.where(np.isin(fine, (1,10)), 255, 0).astype(np.uint8)
    original = np.where(np.isin(base, (1,10)), 255, 0).astype(np.uint8)
    original[35:49, 62:80] = 0  # An old circular guard removed real skin.
    selected = base > 0
    context = np.zeros(base.shape, np.uint8);context[10:110,10:95] = 255
    return original, base, fine, skin, selected, context


def test_restore_skin_at_old_guard_and_remove_neural_features_without_mutating_inputs():
    values = fixture();before = tuple(value.copy() for value in values)
    result = refine(*values)
    assert result[41,70] == 255 and values[0][41,70] == 0
    for y, x in [(29,30),(29,70),(78,45),(84,45)]:
        assert result[y,x] == 0 and values[0][y,x] == 255
    assert result[56,50] == 255
    assert all(np.array_equal(actual,expected) for actual,expected in zip(values,before))


@pytest.mark.parametrize('category', OUTER_PROTECTED)
def test_outer_parser_glasses_jewelry_neck_cloth_hair_hat_remain_protected(category):
    original,base,fine,skin,selected,context = fixture()
    base[38:45,62:80] = category
    original[38:45,62:80] = 0
    actual = refine(original,base,fine,skin,selected,context)
    assert not np.any(actual[38:45,62:80])
    assert actual[37,70] == 0  # Retain the adjacent outer-class guard too.


def test_ears_and_another_face_stay_as_the_base_selection():
    original,base,fine,skin,selected,context = fixture()
    base[55:68,80:94] = 7;original[55:68,80:94] = 171
    base[10:110,120:190] = 1;base[50:64,150:165] = 10
    fine[10:110,120:190] = 1;fine[50:64,150:165] = 10
    skin[10:110,120:190] = 255;original[10:110,120:190] = 37
    result = refine(original,base,fine,skin,selected,context)
    assert np.array_equal(result[55:68,80:94],original[55:68,80:94])
    assert np.array_equal(result[:,120:],original[:,120:])
    assert np.array_equal(result[context==0],original[context==0])


@pytest.mark.parametrize('reason', ['missing_nose','two_faces','empty_skin'])
def test_identity_ambiguity_or_empty_skin_keeps_the_original_unchanged(reason):
    original,base,fine,skin,selected,context = fixture()
    before = original.copy()
    if reason == 'missing_nose':fine[fine==10] = 1
    elif reason == 'empty_skin':skin[:] = 0
    else:
        base[10:110,120:190] = 1;base[50:64,150:165] = 10
        fine[10:110,120:190] = 1;fine[50:64,150:165] = 10
        selected[10:110,120:190] = True;context[10:110,120:190] = 255
    with pytest.raises(ValueError):refine(original,base,fine,skin,selected,context)
    assert np.array_equal(original,before)


@pytest.mark.parametrize('invalid', ['shape','dtype','labels','feature_alpha','selected','budget'])
def test_invalid_fields_are_not_guessed_or_published(monkeypatch,invalid):
    values = list(fixture());before = values[0].copy()
    if invalid == 'shape':values[3] = values[3][:-1]
    elif invalid == 'dtype':values[2] = values[2].astype(np.float32)
    elif invalid == 'labels':values[1][20,20] = 19
    elif invalid == 'feature_alpha':values[3][29,30] = 255
    elif invalid == 'selected':values[4] = values[4].astype(np.uint8)
    else:
        from iphoto.segmentation import face_feature_fusion
        monkeypatch.setattr(face_feature_fusion,'MAX_PIXELS',100)
    with pytest.raises(ValueError):refine(*values)
    assert np.array_equal(values[0],before)


@pytest.mark.parametrize('limited', [False,True])
def test_real_segment_routes_to_fusion_and_keeps_person_spatial_and_source_limits(monkeypatch,limited):
    _,base,fine,skin,_,_ = fixture()
    image = Image.new('RGB',(200,120),(150,100,90));before = image.tobytes()
    calls=[]
    monkeypatch.setattr(face_skin,'backend',lambda:SimpleNamespace(predict_native=lambda _:base))
    monkeypatch.setattr(face_precision,'available',lambda:True)
    monkeypatch.setattr(face_precision,'backend',lambda:SimpleNamespace(predict_skin=lambda *args,**kwargs:calls.append(True) or (fine,skin)))
    features={'eyes':[[.16,.25],[.36,.25]],'mouth':[[.17,.68],[.32,.68]]}
    hint=rect(.12,.29,.37,.58) if limited else rect()
    mask,quality=face_skin.segment(image,hint,[[.26,.47,1]],crop=[0,0,1,1],
        features=features,context_hint=rect(.05,.08,.475,.92),scope='region' if limited else 'full')
    actual=np.asarray(raster_mask(mask,image.size));extent=np.asarray(raster_mask(hint,image.size))
    assert calls==[True] and quality['skin_feature_refinement'] and quality['continuous_boundary']
    assert quality['model'].startswith('BiSeNet + FaRL') and mask['semantic_target']=='face_skin'
    assert quality['coverage'] == round(np.count_nonzero(actual) / (image.width * image.height) * 100, 2)
    assert actual[41,70]>0 and not np.any(actual[extent==0])
    assert not np.any(actual[fine==12]) and not np.any(actual[fine==4])
    assert image.tobytes()==before


def test_region_that_becomes_only_anatomy_is_rejected_instead_of_publishing_empty_skin(monkeypatch):
    _,base,fine,skin,_,_ = fixture()
    image = Image.new('RGB',(200,120),(150,100,90));before = image.tobytes()
    calls = []
    monkeypatch.setattr(face_skin,'backend',lambda:SimpleNamespace(predict_native=lambda _:base))
    monkeypatch.setattr(face_precision,'available',lambda:True)
    monkeypatch.setattr(face_precision,'backend',lambda:SimpleNamespace(predict_skin=lambda *args,**kwargs:calls.append(True) or (fine,skin)))
    with pytest.raises(ValueError,match='五官类别分数不足'):
        face_skin.segment(image,rect(.125,.225,.185,.26),[[.26,.47,1]],crop=[0,0,1,1],
            features={'eyes':[[.07,.11],[.10,.11]],'mouth':[[.17,.68],[.32,.68]]},
            context_hint=rect(.05,.08,.475,.92),scope='region')
    assert calls == [True] and image.tobytes()==before


@pytest.mark.parametrize('reason', ['error','shape','identity'])
def test_optional_failure_retains_exact_old_base_mask_and_reports_fallback(monkeypatch,reason):
    _,base,fine,skin,_,_ = fixture()
    image=Image.new('RGB',(200,120));features={'eyes':[[.16,.25],[.36,.25]],'mouth':[[.17,.68],[.32,.68]]}
    engine=SimpleNamespace(predict_native=lambda _,**kwargs:base)
    arguments=dict(crop=[0,0,1,1],features=features,context_hint=rect(.05,.08,.475,.92))
    baseline,_=face_skin.segment(image,rect(),[[.26,.47,1]],engine=engine,**arguments)
    def predict(*args,**kwargs):
        if reason=='error':raise RuntimeError('Optional model unavailable')
        if reason=='shape':return fine,skin[:-1]
        changed=fine.copy();changed[changed==10]=1
        return changed,skin
    monkeypatch.setattr(face_skin,'backend',lambda **kwargs:engine)
    monkeypatch.setattr(face_precision,'available',lambda:True)
    monkeypatch.setattr(face_precision,'backend',lambda **kwargs:SimpleNamespace(predict_skin=predict))
    phases=[]
    mask,quality=face_skin.segment(image,rect(),[[.26,.47,1]],progress=phases.append,**arguments)
    assert mask==baseline and not quality['skin_feature_refinement']
    assert 'face_fallback' in phases and any('基础面部分区' in text for text in quality['warnings'])


def test_skin_scores_protect_other_anatomy_and_reuse_part_inference(monkeypatch):
    parser=face_precision.FaceParser.__new__(face_precision.FaceParser);parser._cached=None;calls=[]
    scores=np.zeros((1,11,512,512),np.float32);scores[:,1]=30
    parser.session=SimpleNamespace(run=lambda *args:calls.append(True) or [scores.copy()])
    image=Image.new('RGB',(12,12),(100,90,80));points=np.array([[2,3],[8,3],[5,6],[3,9],[7,9]],np.float32)
    labels=parser.predict_native(image,points);phases=[]
    fine,alpha=parser.predict_skin(image,points,progress=phases.append)
    assert fine is labels and len(calls)==1 and alpha.max()==255
    assert phases==['face_cached','face_skin_features']
    labels=labels.copy();labels[:2]=12;labels[2:4]=4;labels[4:6]=17;labels[6:8]=6
    alpha=face_precision.native_part_alpha(scores,face_precision.alignment(points),labels,'skin')
    assert not np.any(alpha[:8])


def test_over_budget_skips_optional_model_before_inference(monkeypatch):
    _,base,_,_,_,_ = fixture()
    from iphoto.segmentation import face_feature_fusion
    monkeypatch.setattr(face_feature_fusion,'MAX_PIXELS',100)
    monkeypatch.setattr(face_skin,'backend',lambda:SimpleNamespace(predict_native=lambda _:base))
    monkeypatch.setattr(face_precision,'available',lambda:True)
    monkeypatch.setattr(face_precision,'backend',lambda **kwargs:pytest.fail('Do not load over-budget precision'))
    _,quality=face_skin.segment(Image.new('RGB',(200,120)),rect(),[[.26,.47,1]],crop=[0,0,1,1],
        features={'eyes':[[.16,.25],[.36,.25]],'mouth':[[.17,.68],[.32,.68]]})
    assert not quality['skin_feature_refinement'] and any('基础面部分区' in warning for warning in quality['warnings'])
