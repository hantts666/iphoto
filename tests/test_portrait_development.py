from copy import deepcopy

import numpy as np
import pytest
from PIL import Image,ImageDraw

from iphoto.ai_protocol import parse_auto
from iphoto.document import empty_mask,raster_mask,new_layer
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.photo_strategy import parse_review,validate_portrait_development,soften_effect_mask,mouth_review_box,render_candidate
from test_photo_strategy import develop,response


@pytest.mark.parametrize('field',['exposure','shadows','warmth','hsl_orange_lightness','red_channel','curve_rgb'])
def test_broad_portrait_skin_light_cannot_pass_planning_or_review(field):
    recipe=Recipe().to_dict();recipe[field]=[[0,0],[255,230]] if field=='curve_rgb' else .4
    with pytest.raises(ValueError,match='完整五官'):validate_portrait_development(recipe,'face_skin')
    context={'revision':0,'natural_development':True,'candidates':[{'layer_id':'skin','whole_image':False,'mask_target':'face_skin'}]}
    with pytest.raises(ValueError,match='完整五官'):
        parse_review(response({'status':'revise','summary':'brighten','edits':[{'layer_id':'skin','recipe':recipe}]}),context)


@pytest.mark.parametrize('field',['skin_smoothing','softness'])
def test_complete_face_light_does_not_blur_eyes_or_lips(field):
    recipe=Recipe().to_dict();recipe[field]=15
    with pytest.raises(ValueError,match='五官'):validate_portrait_development(recipe,'face')
    for target in ('face_skin','body_skin'):
        with pytest.raises(ValueError,match='保留皮肤纹理'):validate_portrait_development(recipe,target)


def test_explicit_local_smoothing_remains_available_but_natural_review_cannot_add_it():
    import json
    from iphoto.ai_tasks import parse_regions
    from test_ai_auto_layers import auto_response
    region=json.loads(auto_response()['choices'][0]['message']['content'])['regions'][0]
    region.update(mask_target='face_skin',recipe=Recipe(skin_smoothing=25).to_dict())
    assert parse_regions(response({'status':'planned','summary':'明确磨皮','regions':[region]}))['regions'][0]['recipe']['skin_smoothing']==25
    natural=develop();natural['regions']=[region]
    with pytest.raises(ValueError,match='保留皮肤纹理'):parse_auto(response(natural),Recipe().to_dict(),[])
    context={'natural_development':True,'candidates':[{'layer_id':'skin','whole_image':False,'mask_target':'face_skin'}]}
    review=response({'status':'revise','summary':'smooth','edits':[{'layer_id':'skin','recipe':region['recipe']}]})
    with pytest.raises(ValueError,match='保留皮肤纹理'):parse_review(review,context)
    context['natural_development']=False
    assert parse_review(review,context)['edits'][0]['recipe']['skin_smoothing']==25


def test_real_develop_protocol_rejects_old_skin_only_brightness_and_accepts_separate_face_light():
    from test_ai_auto_layers import auto_response
    import json
    plan=develop();region=json.loads(auto_response()['choices'][0]['message']['content'])['regions'][0]
    region.update(mask_target='face_skin',recipe=Recipe(exposure=.4,skin_smoothing=15).to_dict())
    plan['regions']=[region]
    with pytest.raises(ValueError,match='完整五官'):parse_auto(response(plan),Recipe().to_dict(),[])
    region.update(mask_target='face',recipe=Recipe(exposure=.2,shadows=8).to_dict())
    assert parse_auto(response(plan),Recipe().to_dict(),[])['regions'][0]['mask_target']=='face'


def test_whole_face_effect_keeps_mouth_illumination_continuous_without_growing_outer_range():
    alpha=Image.new('L',(320,300));ImageDraw.Draw(alpha).ellipse((25,15,295,285),fill=255)
    mask={**empty_mask(),'semantic_target':'face','bitmap':encode_bitmap(alpha,sampling='alpha')}
    before=deepcopy(mask);actual=np.asarray(raster_mask(soften_effect_mask(mask,alpha.size),alpha.size))
    assert mask==before and np.all(actual<=np.asarray(alpha))
    assert actual[200,140]==actual[200,160]==actual[200,180]==255
    assert 0<actual[150,30]<255 and not actual[150,20]


@pytest.mark.parametrize('points',[[],[[.1,.2]],[[float('nan'),.2],[.3,.4]],[[0,0],[.001,.001]]])
def test_missing_or_unusable_mouth_landmarks_do_not_invent_a_crop(points):
    assert mouth_review_box([{'face_features':{'mouth':points}}],(1000,800)) is None


def test_native_mouth_evidence_is_paired_lossless_and_includes_surrounding_skin(tmp_path):
    face={'face_features':{'mouth':[[.4,.6],[.6,.6]]},'mask':empty_mask(True)}
    box=mouth_review_box([face],(600,500))
    assert box[0]<240<360<box[2] and box[1]<300<box[3]
    image=Image.new('RGB',(600,500),(120,90,80));ImageDraw.Draw(image).rectangle((240,290,360,315),fill=(60,30,35))
    result=render_candidate(image,[new_layer('original',True)],[new_layer('candidate',True)],[face],tmp_path,'test')
    last=result['images'][-2:]
    assert all(asset['lossless'] and '嘴周' in asset['label'] for asset in last)
    with Image.open(last[0]['path']) as original,Image.open(last[1]['path']) as candidate:
        assert original.tobytes()==candidate.tobytes()==image.crop(box).tobytes()
