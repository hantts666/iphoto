from copy import deepcopy

import numpy as np
import pytest
from PIL import Image, ImageDraw

from iphoto.document import new_layer, validate_layers, render_layers, render_detail_tile, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.pixel_patch import encode_patch, validate_patch
from iphoto.preview_cache import LayerPreviewCache


def layer():
    item = new_layer('生成精修')
    alpha = Image.new('L', (320, 240))
    draw = ImageDraw.Draw(alpha)
    draw.rectangle((55, 65, 240, 190), fill=255)
    draw.ellipse((100, 95, 140, 125), fill=0)
    item['mask']['bitmap'] = encode_bitmap(alpha, sampling='alpha', preserve_resolution=True)
    item['pixel_patch'] = encode_patch(Image.new('RGB', (160, 120), (200, 130, 100)), (320, 240), (40, 40, 260, 220))
    return item


def test_generated_pixels_protect_outside_and_hole_at_native_and_preview_sizes():
    item = layer()
    for size in [(320, 240), (160, 120)]:
        source = Image.new('RGBA', size, (30, 70, 90, 211))
        output = render_layers(source, validate_layers([item]))
        mask = np.asarray(raster_mask(item['mask'], size))
        assert np.array_equal(np.asarray(output)[mask == 0], np.asarray(source)[mask == 0])
        assert np.all(np.asarray(output)[..., 3] == 211)
        assert np.any(np.asarray(output)[mask == 255, :3] != np.asarray(source)[mask == 255, :3])
    source = Image.new('RGBA', (320, 240), (30, 70, 90, 211))
    item['opacity'] = .5
    full = render_layers(source, [item])
    tile = render_detail_tile(source, [item], [50, 50, 270, 200])
    assert np.array_equal(np.asarray(tile), np.asarray(full.crop((50, 50, 270, 200))))


def test_cache_and_portable_patch_validation():
    item = layer()
    assert validate_layers([item])[0]['pixel_patch'] == item['pixel_patch']
    source = Image.new('RGB', (320, 240), (30, 70, 90))
    cache = LayerPreviewCache()
    a = cache.render(source, [item])
    changed = deepcopy(item)
    changed['pixel_patch'] = encode_patch(Image.new('RGB', (160, 120), (100, 190, 200)), (320, 240), (40, 40, 260, 220))
    b = cache.render(source, [changed])
    assert a.tobytes() != b.tobytes()
    damaged = {**item['pixel_patch'], 'sha256': '0'*64}
    with pytest.raises(ValueError, match='校验'):
        validate_patch(damaged)
    with pytest.raises(ValueError, match='坐标'):
        validate_patch({**item['pixel_patch'], 'box': [-1, 0, 400, 300]})
    item['inpaint'] = {'method': 'telea', 'radius': 3}
    with pytest.raises(ValueError, match='混用'):
        validate_layers([item])
