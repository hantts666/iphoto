"""Local adjustments retain the old full-image result, including crop edges."""
from copy import deepcopy

import numpy as np
from PIL import Image
import pytest

from iphoto import document, engine
from iphoto.document import new_layer, raster_mask, render_detail_tile, render_layers
from iphoto.engine import Recipe, render, render_masked
from iphoto.masks import encode_bitmap


@pytest.fixture(scope="module")
def texture():
    pixels = np.random.default_rng(77).integers(0, 256, (811, 2253, 3), dtype=np.uint8)
    return Image.fromarray(pixels)


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
@pytest.mark.parametrize("recipe", [
    Recipe(exposure=.2, warmth=15),
    Recipe(vibrance=24, saturation=17, tint=-19, contrast=12),
    Recipe(skin_smoothing=25),
    Recipe(skin_smoothing=85, softness=100, sharpness=100, exposure=.1),
    Recipe(softness=100, sharpness=100),
])
@pytest.mark.parametrize("edge", [False, True])
def test_local_pixels_match_full_image_reference(texture, mode, recipe, edge):
    image = texture.copy()
    if mode == "RGBA":
        image.putalpha(Image.fromarray(np.random.default_rng(7).integers(0, 256, (image.height, image.width), dtype=np.uint8)))
    source_bytes = image.tobytes()
    alpha = Image.new("L", image.size)
    small = Image.fromarray(np.random.default_rng(9).integers(0, 256, (119, 177), dtype=np.uint8))
    alpha.paste(small, (image.width-177, image.height-119) if edge else (913, 311))
    mask_bytes = alpha.tobytes()
    expected = Image.composite(render(image, recipe), image, alpha)
    actual = render_masked(image, recipe, alpha)
    assert actual.tobytes() == expected.tobytes()
    assert image.tobytes() == source_bytes and alpha.tobytes() == mask_bytes


@pytest.mark.parametrize("step_size", [(2253, 811), (4351, 1753)])
def test_feathered_bitmap_nested_group_opacity_and_panning_match_old_renderer(texture, step_size, monkeypatch):
    image = texture.resize(step_size)
    group = new_layer("portrait", True, kind="group")
    group["opacity"] = .67
    layer = new_layer("face", parent_id=group["id"])
    layer["opacity"] = .72
    layer["recipe"] = Recipe(skin_smoothing=80, softness=70, sharpness=90, vibrance=24).to_dict()
    alpha = Image.new("L", (513, 197))
    alpha.paste(180, (111, 69, 158, 101))
    layer["mask"].update(bitmap=encode_bitmap(alpha, preserve_resolution=True), feather=.008)
    layers = [new_layer("base", True), group, layer]
    original = image.tobytes()
    with monkeypatch.context() as patch:
        patch.setattr(document, "render_masked", lambda source, recipe, mask, **kw:
                      Image.composite(render(source, recipe, detail_size=kw["detail_size"]), source, mask))
        expected = render_layers(image, deepcopy(layers))
    actual = render_layers(image, deepcopy(layers))
    assert actual.tobytes() == expected.tobytes()
    for box in ((373, 217, 1037, 611), (401, 229, 1153, 637), (0, 0, 513, 411)):
        assert render_detail_tile(image, layers, box).tobytes() == expected.crop(box).tobytes()
    assert image.tobytes() == original
    region = np.asarray(raster_mask(layer["mask"], image.size))
    assert np.array_equal(np.asarray(actual)[region == 0], np.asarray(image)[region == 0])


@pytest.mark.parametrize("coverage", ["empty", "small", "large"])
def test_filter_uses_local_pixels_or_full_fallback_without_touching_source(texture, coverage, monkeypatch):
    alpha = Image.new("L", texture.size)
    if coverage == "small":
        alpha.paste(255, (931, 303, 1017, 417))
    elif coverage == "large":
        alpha.paste(255, (0, 0, texture.width, texture.height))
    calls = []
    original = engine.render
    def observe(image, recipe, **kwargs):
        calls.append(image.size)
        return original(image, recipe, **kwargs)
    monkeypatch.setattr(engine, "render", observe)
    source_bytes = texture.tobytes()
    result = render_masked(texture, Recipe(skin_smoothing=60), alpha)
    assert result is not texture and texture.tobytes() == source_bytes
    if coverage == "empty":
        assert calls == [] and result.tobytes() == source_bytes
    elif coverage == "small":
        assert len(calls) == 1 and calls[0][0]*calls[0][1] < texture.width*texture.height*.1
    else:
        assert calls == [texture.size]
