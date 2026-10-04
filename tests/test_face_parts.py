"""Facial part isolation and transactions; fixtures do not measure accuracy."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_tasks import parse_regions, parse_selection
from iphoto.controllers import pixel_selections
from iphoto.document import empty_mask, raster_mask
from iphoto.engine import Recipe
from iphoto.segmentation.face_skin import segment
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_canvas_ui import canvas  # noqa: F401
from test_face_detection import completion
from test_face_inventory import face
from test_scene_object_actions_ui import reveal


def rect(left=0, top=0, right=1, bottom=1):
    return {**empty_mask(), 'label': '面部', 'ops': [{'kind': 'rect', 'mode': 'add',
            'points': [[left, top], [right, bottom]]}]}


def labels():
    result = np.zeros((120, 240), np.uint8)
    for left in (10, 130):
        result[10:110, left:left+100] = 1
        result[40:55, left+45:left+55] = 10
        result[62:73, left+25:left+75] = 12
        result[73:84, left+25:left+75] = 13
        result[68:76, left+35:left+65] = 11
        result[25:35, left+20:left+35] = 4
    return result


def lip_region(**changes):
    return {'name': '唇色', 'reason': '仅调整嘴唇', 'mask_target': 'face',
            'face_scope': 'region', 'face_part': 'lips', 'parts': [],
            'box': [230, 250, 350, 350], 'point': [300, 300],
            'recipe': Recipe(hsl_red_saturation=18).to_dict(), **changes}


def test_lips_exclude_skin_mouth_eyes_and_the_other_face_and_keep_source():
    image = Image.new('RGB', (240, 120), (170, 110, 80)); original = image.tobytes()
    classes = labels()
    mask, quality = segment(image, rect(), [[55/240, 47/120, 1]], crop=[0, 0, 1, 1],
                            target='face', scope='region', part='lips',
                            engine=SimpleNamespace(predict_native=lambda _: classes))
    alpha = np.asarray(raster_mask(mask, image.size))
    assert alpha[65, 40] > 0 and alpha[80, 40] > 0
    assert not np.any(alpha[~np.isin(classes, (12, 13))]) and not np.any(alpha[:, 120:])
    assert image.tobytes() == original and mask['semantic_target'] == 'face'
    assert mask['label'].endswith('嘴唇') and quality['face_part'] == 'lips'
    assert '嘴内' in quality['protected_features'] and 'face_binding' not in mask


def test_lip_box_limits_edits_even_with_the_nose_anchor_outside_it():
    classes = labels(); hint = rect(.12, .55, .23, .73)
    mask, _ = segment(Image.new('RGB', (240, 120)), hint, [[55/240, 47/120, 1]],
                      context_hint=rect(0, 0, .5, 1), crop=[0, 0, 1, 1], target='face',
                      scope='region', part='lips', engine=SimpleNamespace(predict_native=lambda _: classes))
    alpha = np.asarray(raster_mask(mask, (240, 120))); extent = np.asarray(raster_mask(hint, (240, 120)))
    assert np.count_nonzero(alpha) > 0 and not np.any(alpha[extent == 0])


def test_occluded_or_out_of_box_lips_fail_instead_of_returning_a_whole_face():
    classes = labels(); classes[classes == 12] = 1; classes[classes == 13] = 1
    with pytest.raises(ValueError, match='未识别到嘴唇'):
        segment(Image.new('RGB', (240, 120)), rect(), [[55/240, 47/120, 1]],
                crop=[0, 0, 1, 1], target='face', scope='region', part='lips',
                engine=SimpleNamespace(predict_native=lambda _: classes))


def test_touching_face_components_cannot_extend_parts_outside_the_parent_identity():
    classes = labels();classes[30:60,100:140] = 1
    mask, _ = segment(Image.new('RGB',(240,120)), rect(), [[55/240,47/120,1]],
                      crop=[0,0,1,1], target='face', scope='region', part='lips',
                      context_hint=rect(0,0,.5,1), engine=SimpleNamespace(predict_native=lambda _:classes))
    alpha=np.asarray(raster_mask(mask,(240,120)))
    assert np.count_nonzero(alpha[:,:120])>0 and not np.any(alpha[:,120:])


@pytest.mark.parametrize('target,scope', [('face_skin', 'region'), ('object', 'region'), ('face', 'full')])
def test_lip_parts_reject_wrong_targets_in_both_cloud_protocol_and_worker(target, scope):
    value = {'status': 'selected', 'summary': '嘴唇', 'mask_target': target,
             'face_scope': scope, 'face_part': 'lips', 'box': [100,100,300,300], 'point': [200,200]}
    with pytest.raises(ValueError):
        parse_selection(completion(value))
    with pytest.raises(ValueError):
        segment(Image.new('RGB', (240,120)), rect(), [[.2,.4,1]], target=target,
                scope=scope, part='lips', engine=SimpleNamespace(predict_native=lambda _: labels()))


@pytest.mark.parametrize('change', [{'face_part':'all'}, {'face_part':'nose'},
    {'face_scope':'full'}, {'recipe':Recipe(skin_smoothing=20).to_dict()}, {'parts':[{'box':[230,250,350,350],'point':[300,300]}]}])
def test_face_color_plans_cannot_smooth_lips_or_smuggle_whole_faces(change):
    with pytest.raises(ValueError):
        parse_regions(completion({'status':'planned','summary':'唇色','regions':[lip_region(**change)]}))


def test_lip_plan_keeps_its_part_and_spatial_limit():
    value = lip_region(); result = parse_regions(completion({'status':'planned','summary':'唇色','regions':[value]}))['regions'][0]
    assert result['mask_target']=='face' and result['face_scope']=='region' and result['face_part']=='lips'
    assert result['anchor']==[300/999,300/999] and result['recipe']['hsl_red_saturation']==18


@pytest.mark.parametrize('button,part,target', [('selectFaceLipsButton','lips','face'), ('selectFaceNoseButton','nose','face_skin')])
def test_local_part_buttons_use_parent_face_without_a_full_face_binding(canvas,monkeypatch,button,part,target):  # noqa: F811
    e=canvas.e;e._face_hints=[face('local-face-1')];e.changed.emit();captured=[]
    monkeypatch.setattr(pixel_selections,'select_hint',lambda *args,**kw:captured.append((args,kw)))
    reveal(canvas,button);canvas.click(button)
    assert len(captured)==1
    args,kw=captured[0]
    assert kw['face_part']==part and kw['face_scope']=='region' and kw['mask_target']==target
    assert kw['face_context']==e._face_hints[0]['mask'] and kw['anchor']==e._face_hints[0]['anchor']
    assert 'face_binding' not in kw and not e._scene.catalog


def test_chat_lip_plan_uses_parent_context_and_does_not_expand_its_box_or_publish_a_face(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;e._face_hints=[face('local-face-1')];captured=[];before=deepcopy(e._layers)
    monkeypatch.setattr(pixel_selections,'select_regions',lambda *args,**kw:captured.append((args,kw)) or True)
    with mock_api(auto_response(regions=[lip_region()])) as (url,requests):
        configure(e.ai,url);assert e.sendMessage('只给嘴唇加点红色','auto')
        wait_for(lambda:not e.ai.busy and e._pending_request is None)
    assert len(captured)==1 and len(requests)==1
    region=captured[0][0][1][0]
    expected=parse_regions(completion({'status':'planned','summary':'唇色','regions':[lip_region()]}))['regions'][0]
    assert region['mask']==expected['mask'] and region['face_context']==e._face_hints[0]['mask']
    assert region['anchor']==e._face_hints[0]['anchor'] and region['recover_face_anchor'] is True
    assert region['face_part']=='lips' and 'face_binding' not in region
    assert e._layers==before and not e._scene.catalog
