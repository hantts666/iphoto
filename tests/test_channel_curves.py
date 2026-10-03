"""Exact channel tone curves, alpha/detail behavior, and luma-dependent controls."""

import numpy as np
from PIL import Image
import pytest

from iphoto.engine import Recipe, render, render_reference


def color_grid(alpha=False):
    y, x = np.mgrid[:256, :256]
    channels = [x, y, (x*37+y*97) % 256]
    if alpha:
        channels.append((x+y*3) % 256)
    return Image.fromarray(np.stack(channels, axis=-1).astype(np.uint8))


@pytest.mark.parametrize("recipe", [
    Recipe(exposure=-2), Recipe(exposure=2), Recipe(warmth=-60),
    Recipe(tint=60, exposure=-1.17), Recipe(warmth=60, tint=-60, exposure=1.31),
])
@pytest.mark.parametrize("alpha", [False, True])
def test_channel_controls_match_exact_float_reference_at_every_input_byte(recipe, alpha):
    image = color_grid(alpha)
    original = image.tobytes()
    actual = render(image, recipe)
    assert actual.tobytes() == render_reference(image, recipe).tobytes()
    assert image.tobytes() == original
    if alpha:
        assert actual.getchannel("A").tobytes() == image.getchannel("A").tobytes()


@pytest.mark.parametrize("field", ["contrast", "highlights", "shadows", "saturation",
                                   "vibrance", "whites", "blacks"])
def test_luminance_and_cross_channel_controls_still_depend_on_other_channels(field):
    image = Image.new("RGB", (2, 1))
    image.putdata([(160, 60, 80), (160, 230, 150)])
    recipe = Recipe.from_dict({"exposure": .1, field: 35})
    expected = render_reference(image, recipe)
    actual = render(image, recipe)
    # Same red inputs, different surrounding channels: a separable curve
    # cannot represent these controls, even when exposure is also present.
    assert expected.getpixel((0, 0))[0] != expected.getpixel((1, 0))[0]
    assert actual.getpixel((0, 0))[0] != actual.getpixel((1, 0))[0]
    delta = np.abs(np.asarray(actual, dtype=np.int16)-np.asarray(expected, dtype=np.int16))
    assert delta.max() <= 2


@pytest.mark.parametrize("detail", [dict(sharpness=61), dict(softness=29), dict(skin_smoothing=35)])
def test_channel_curve_keeps_detail_effects_and_original_alpha(detail):
    image = color_grid(True)
    recipe = Recipe(exposure=.37, warmth=14, tint=-6, **detail)
    actual = render(image, recipe)
    assert actual.tobytes() == render_reference(image, recipe).tobytes()
    assert actual.getchannel("A").tobytes() == image.getchannel("A").tobytes()


def test_many_parameter_changes_and_return_to_previous_recipe_stay_exact():
    image = color_grid()
    first = render(image, Recipe(exposure=-1.75, warmth=19)).tobytes()
    for index in range(25):
        recipe = Recipe(exposure=-2+index/6, warmth=19)
        assert render(image, recipe).tobytes() == render_reference(image, recipe).tobytes()
    assert render(image, Recipe(exposure=-1.75, warmth=19)).tobytes() == first
