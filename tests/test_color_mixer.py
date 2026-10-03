"""Independent colour references and persistent, masked colour edits."""
import colorsys
from copy import deepcopy

import numpy as np
from PIL import Image
import pytest

from iphoto.color_mixer import COLORS, FIELDS, rgb_to_hsl, hsl_to_rgb
from iphoto.document import new_layer, render_layers
from iphoto.engine import Recipe, RANGES, render, render_reference


def test_hsl_conversion_matches_independent_colorsys_on_greys_and_random_colours():
    pixels = np.concatenate((np.random.default_rng(51).random((2500, 3)),
                             np.repeat(np.linspace(0, 1, 256)[:, None], 3, axis=1))).astype(np.float32)
    hue, sat, light = rgb_to_hsl(pixels)
    expected = np.array([colorsys.rgb_to_hls(*pixel) for pixel in pixels])
    assert np.allclose(light, expected[:, 1], atol=2e-6)
    assert np.allclose(sat, expected[:, 2], atol=2e-6)
    chromatic = sat > .0001
    distance = (hue[chromatic] / 360 - expected[chromatic, 0] + .5) % 1 - .5
    assert np.abs(distance).max() < 2e-6
    assert np.allclose(hsl_to_rgb(hue, sat, light), pixels, atol=2e-6)


@pytest.mark.parametrize("key", ["red_channel", "green_channel", "blue_channel"])
def test_rgb_gain_changes_only_requested_channel_with_exact_reference(key):
    pixels = np.random.default_rng(99).integers(0, 256, (63, 256, 4), dtype=np.uint8)
    image = Image.fromarray(pixels)
    index = ["red_channel", "green_channel", "blue_channel"].index(key)
    for amount in (-100, -37, 54, 100):
        result = np.array(render(image, Recipe.from_dict({key: amount})))
        assert np.array_equal(result[..., [i for i in range(4) if i != index]],
                              pixels[..., [i for i in range(4) if i != index]])
        assert np.array_equal(result, np.asarray(render_reference(image, Recipe.from_dict({key: amount}))))
        if amount == -100:
            assert not result[..., index].any()
    assert image.tobytes() == pixels.tobytes()


@pytest.mark.parametrize("name,label,center", COLORS)
def test_each_colour_band_rotates_its_center_and_leaves_opposite_and_neutrals(name, label, center):
    rgb = np.array([colorsys.hls_to_rgb(center/360, .5, 1),
                    colorsys.hls_to_rgb(((center+180)%360)/360, .5, 1), (.5,.5,.5), (1,1,1), (0,0,0)])
    image = Image.fromarray(np.rint(rgb[None]*255).astype(np.uint8))
    result = render(image, Recipe.from_dict({f"hsl_{name}_hue": 120}))
    expected = np.rint(np.array(colorsys.hls_to_rgb(((center+120)%360)/360,.5,1))*255).astype(np.uint8)
    assert np.abs(np.array(result.getpixel((0,0)), dtype=int)-expected).max() <= 1
    assert np.array_equal(np.asarray(result)[0,1:],np.asarray(image)[0,1:])


def test_combined_hsl_is_strip_independent_alpha_safe_and_exact_in_mask():
    data=np.random.default_rng(19).integers(0,256,(103,89,4),dtype=np.uint8)
    image=Image.fromarray(data)
    recipe=Recipe(exposure=.14,red_channel=8,hsl_red_hue=179,hsl_green_saturation=-100,
                  hsl_blue_lightness=65,hsl_orange_saturation=17)
    actual=render(image,recipe,strip_height=17)
    assert actual.tobytes()==render_reference(image,recipe,strip_height=31).tobytes()
    layer=new_layer("分色")
    layer['mask']['ops']=[{'kind':'rect','mode':'add','points':[[.25,.25],[.75,.75]]}]
    layer['recipe']=recipe.to_dict()
    original=deepcopy(layer)
    output=np.asarray(render_layers(image,[layer]))
    assert np.array_equal(output[:20],data[:20])
    assert np.array_equal(output[...,3],data[...,3])
    assert layer==original


@pytest.mark.parametrize("key", FIELDS)
def test_all_selective_fields_are_validated_and_saved_as_recipe_fields(key):
    assert key in RANGES
    lo,hi=RANGES[key]
    assert Recipe.from_dict({key:lo}).to_dict()[key]==lo
    with pytest.raises(ValueError):Recipe.from_dict({key:hi+1})
    with pytest.raises(ValueError):Recipe.from_dict({key:True})
