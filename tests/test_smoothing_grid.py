"""A source pixel must keep the same smoothing result as the viewport moves."""

from copy import deepcopy

import numpy as np
from PIL import Image
import pytest

from iphoto.document import new_layer, raster_mask, render_detail_tile, render_layers
from iphoto.engine import Recipe


@pytest.fixture(scope="module")
def portrait_texture():
    y, x = np.mgrid[:1751, :3239]
    rng = np.random.default_rng(41)
    pixels = np.stack(((x*3+y) % 192+32, (x+y*2) % 176+40, (x*2+y*3) % 160+48), axis=-1)
    pixels = np.clip(pixels+rng.integers(-12, 13, pixels.shape), 0, 255).astype("uint8")
    return Image.fromarray(pixels)


@pytest.mark.parametrize("strength", [35, 80])
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_smoothing_tiles_and_pan_overlap_match_full_composition(portrait_texture, strength, mode):
    image = portrait_texture.copy()
    if mode == "RGBA":
        y, x = np.mgrid[:image.height, :image.width]
        image.putalpha(Image.fromarray(((x+y) % 256).astype("uint8")))
    original = image.tobytes()
    layer = new_layer("whole", True)
    layer["recipe"] = Recipe(skin_smoothing=strength, sharpness=25, softness=12).to_dict()
    layers = [layer]
    full = render_layers(image, layers)
    boxes = [(379, 271, 1204, 922), (396, 284, 1221, 935),
             (379, 271, 1283, 981), (0, 0, 401, 331),
             (image.width-407, image.height-339, image.width, image.height)]
    for box in boxes:
        tile = render_detail_tile(image, layers, box)
        assert tile.tobytes() == full.crop(box).tobytes(), (strength, mode, box)
    assert image.tobytes() == original


def test_local_smoothing_nested_group_opacity_and_unaffected_pixels(portrait_texture):
    image = portrait_texture
    group = new_layer("portrait group", True)
    group.update(kind="group", opacity=.73)
    layer = new_layer("skin")
    layer.update(parent=group["id"], opacity=.68)
    layer["recipe"] = Recipe(exposure=.15, skin_smoothing=45, sharpness=20).to_dict()
    layer["mask"].update(ops=[{"kind": "ellipse", "mode": "add", "points": [[.12, .13], [.51, .63]]}])
    layers = [new_layer("whole", True), group, layer]
    full = render_layers(image, deepcopy(layers))
    box = (331, 179, 1475, 1195)
    assert render_detail_tile(image, deepcopy(layers), box).tobytes() == full.crop(box).tobytes()
    mask = np.asarray(raster_mask(layer["mask"], image.size))
    assert np.array_equal(np.asarray(full)[mask == 0], np.asarray(image)[mask == 0])


def test_three_pixel_grid_partial_photo_edges_and_different_tile_sizes(portrait_texture):
    image = portrait_texture.resize((4351, 1753), Image.Resampling.NEAREST)
    layer = new_layer("whole", True)
    layer["recipe"] = Recipe(skin_smoothing=55, sharpness=10).to_dict()
    full = render_layers(image, [layer])
    for box in ((383, 217, 1227, 994), (409, 231, 1291, 1051),
                (0, 0, 393, 333), (3979, 1391, 4351, 1753)):
        assert render_detail_tile(image, [layer], box).tobytes() == full.crop(box).tobytes()
