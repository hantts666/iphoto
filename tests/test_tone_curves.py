"""Curve truth checks, arbitrary shapes, strict protocols and source boundaries."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest
from scipy.interpolate import PchipInterpolator

from iphoto.ai_protocol import parse_plan, RECIPE_SCHEMA
from iphoto.ai_layer_edits import validate_layer_edits
from iphoto.document import new_layer, validate_layers, render_layers, validate_project
from iphoto.engine import Recipe, render, render_reference, RECIPE_FIELDS, CHANNEL_FIELDS
from iphoto.tone_curves import validate, evaluate, FIELDS

BLUE = [[0,0],[64,85],[128,128],[192,192],[255,255]]


@pytest.mark.parametrize('points', [BLUE, [[0,30],[120,80],[121,220],[255,235]],
    [[0,255],[20,200],[180,30],[255,0]], [[0,30],[40,180],[80,10],[130,120],[200,120],[255,255]],
    [[0,100],[255,100]], [[0,0],[255,255]]])
def test_shape_matches_independent_scipy_at_knots_and_between(points):
    x = np.linspace(0,255,4097)
    expected = PchipInterpolator(*np.array(points).T)(x)/255
    result = evaluate(x/255, validate(points))
    np.testing.assert_allclose(result, expected, atol=6e-8, rtol=0)
    assert np.min(result)>=0 and np.max(result)<=1
    for (left,y0),(right,y1) in zip(points,points[1:]):
        actual = evaluate(np.linspace(left,right,71)/255, validate(points))*255
        assert actual.min() >= min(y0,y1)-.0001 and actual.max() <= max(y0,y1)+.0001


@pytest.mark.parametrize('points', [None,{},[0,0],[[0,0]],[[0,False],[255,255]],
    [[0,0],[128,128],[128,150],[255,255]],[[1,0],[255,255]],[[0,0],[254,255]],
    [[0,0],[128.5,150],[255,255]],[[0,0],[128,float('nan')],[255,255]],
    [[0,-1],[255,255]],[[0,0,1],[255,255]],[[0,0]]*17])
def test_invalid_points_rejected_without_sorting_clamping_or_duplicate_guessing(points):
    with pytest.raises(ValueError): Recipe.from_dict({'curve_blue':points})


def test_serialization_immutable_defaults_and_neutral_anchor_points():
    recipe=Recipe(curve_blue=BLUE)
    data=recipe.to_dict(); data['curve_blue'][1][1]=0
    assert recipe.curve_blue[1]==(64,85) and hash(recipe)
    assert not any(Recipe().to_dict().values())
    assert Recipe(curve_rgb=[[0,0],[255,255]])==Recipe()
    # Neutral anchors are meaningful editing data even before a later drag.
    assert Recipe(curve_rgb=[[0,0],[64,64],[255,255]]).to_dict()['curve_rgb'][1]==[64,64]
    assert Recipe.from_dict({'exposure':.2}).to_dict()['curve_blue']==[]
    assert set(RECIPE_SCHEMA['properties']['recipe']['required'])==set(RECIPE_FIELDS)
    assert set(FIELDS)<=CHANNEL_FIELDS


def grid(alpha=False):
    y,x=np.mgrid[:256,:256]
    channels=[x,y,(17*x+63*y)%256]
    if alpha: channels.append((x*3+y)%256)
    return Image.fromarray(np.stack(channels,-1).astype('uint8'))


@pytest.mark.parametrize('recipe',[Recipe(curve_blue=BLUE), Recipe(curve_rgb=[[0,20],[80,65],[180,205],[255,240]],curve_red=BLUE),
    Recipe(curve_blue=BLUE,exposure=.27,warmth=17), Recipe(curve_blue=BLUE,hsl_red_hue=-42,hsl_orange_saturation=13),
    Recipe(curve_green=BLUE,hsl_blue_lightness=12,exposure=-.2),
    Recipe(curve_blue=BLUE,contrast=41,shadows=25,vibrance=19), Recipe(curve_rgb=[[0,30],[120,80],[121,220],[255,235]],contrast=20),
    Recipe(curve_blue=BLUE,sharpness=35),Recipe(curve_blue=BLUE,skin_smoothing=25)])
@pytest.mark.parametrize('alpha',[False,True])
def test_all_render_paths_match_reference_and_preserve_source_alpha(recipe,alpha):
    source=grid(alpha); before=source.tobytes()
    assert render(source,recipe).tobytes()==render_reference(source,recipe).tobytes()
    assert source.tobytes()==before
    if alpha: assert render(source,recipe).getchannel('A').tobytes()==source.getchannel('A').tobytes()


@pytest.mark.parametrize('field,channel',[('curve_red',0),('curve_green',1),('curve_blue',2)])
def test_only_chosen_channel_low_values_change_and_high_values_remain(field,channel):
    image=grid();actual=np.asarray(render(image,Recipe.from_dict({field:BLUE})));before=np.asarray(image)
    others=[index for index in range(3) if index!=channel]
    assert np.array_equal(actual[...,others],before[...,others])
    bright=before[...,channel]>=192
    assert np.array_equal(actual[...,channel][bright],before[...,channel][bright])
    assert np.any(actual[...,channel][~bright]>before[...,channel][~bright])


def response(recipe):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'status':'applied','recipe':recipe,'summary':'仅按要求调整曲线'})}}]}


def test_ai_curve_lock_and_invalid_points_still_use_strict_validation():
    current=Recipe(curve_red=BLUE).to_dict(); proposal=Recipe(curve_blue=BLUE).to_dict()
    result=parse_plan(response(proposal),current,['curve_red'])
    assert result['recipe']['curve_red']==current['curve_red'] and result['recipe']['curve_blue']==BLUE
    layer=new_layer('已有层',True);layer['recipe']=current;layer['locked']=['curve_red']
    edit=validate_layer_edits([{'layer_id':layer['id'],'recipe':proposal}], [layer])[0]
    assert edit['recipe']==result['recipe'] and edit['preserved_locked']==['curve_red']
    proposal['curve_blue']=[[0,0],[64,300],[255,255]]
    with pytest.raises(ValueError): parse_plan(response(proposal),current,[])


def test_curves_obey_spatial_mask_and_old_project_migration():
    source=grid();layer=new_layer('区域')
    layer['mask']['ops']=[{'kind':'polygon','mode':'add','points':[[.2,.2],[.6,.2],[.6,.6],[.2,.6]]}]
    layer['recipe']=Recipe(curve_blue=BLUE).to_dict();layer['locked']=['curve_blue']
    from iphoto.document import raster_mask
    alpha=np.asarray(raster_mask(layer['mask'],source.size));result=np.asarray(render_layers(source,[layer]))
    assert np.any(result!=np.asarray(source)) and np.array_equal(result[alpha==0],np.asarray(source)[alpha==0])
    assert validate_layers([layer])==[layer]
    old=deepcopy(layer);old['locked']=[]
    for key in FIELDS:old['recipe'].pop(key)
    saved=validate_project({'schema_version':'1.9','source':'x.png','source_sha256':'a'*64,'layers':[old],'active_layer':old['id']})
    assert saved['schema_version']=='1.10' and saved['layers'][0]['recipe']['curve_blue']==[]
