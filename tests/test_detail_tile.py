"""Source-resolution detail crops match the full-resolution composition."""

import numpy as np
import pytest
from PIL import Image

from iphoto.document import new_layer, render_detail_tile, render_layers
from iphoto.engine import Recipe


def test_detail_tile_matches_full_render_with_local_mask_and_filters():
    y, x = np.mgrid[:360, :480]
    image = Image.fromarray(np.stack(((x * 3 + y) % 256,
                                     (x + y * 2) % 256,
                                     (x * 2 + y * 3) % 256), axis=-1).astype("uint8"))
    global_layer = new_layer("global", True)
    global_layer["recipe"] = Recipe(exposure=.2, warmth=8).to_dict()
    local_layer = new_layer("local")
    local_layer["recipe"] = Recipe(sharpness=35, softness=15, vibrance=20).to_dict()
    local_layer["mask"].update(
        feather=.02,
        ops=[{"kind": "ellipse", "mode": "add", "points": [[.15, .1], [.85, .9]]}],
    )
    layers = [global_layer, local_layer]
    box = (90, 65, 390, 285)
    expected = np.asarray(render_layers(image, layers).crop(box))
    actual = np.asarray(render_detail_tile(image, layers, box))
    assert np.percentile(np.abs(expected.astype("int16") - actual.astype("int16")), 99.9) <= 1


def test_detail_tile_preserves_unedited_source_pixels_and_bounds():
    image = Image.new("RGB", (600, 400), (40, 90, 140))
    image.putpixel((250, 200), (255, 0, 0))
    box = (150, 100, 450, 300)
    tile = render_detail_tile(image, [new_layer("global", True)], box)
    assert tile.size == (300, 200)
    assert tile.getpixel((100, 100)) == (255, 0, 0)
    with pytest.raises(ValueError, match="超出照片"):
        render_detail_tile(image, [], (0, 0, 601, 400))
