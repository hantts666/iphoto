"""Subpixel coverage must survive fit previews without altering native masks."""
from copy import deepcopy
import json
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask, overlay_mask, raster_mask
from iphoto.masks import decode_bitmap, encode_bitmap
from iphoto.workspace import ROOT


def strand(phase, alpha=128):
    pixels = np.zeros((128, 256), np.uint8)
    pixels[16:112, 112+phase] = alpha
    return Image.fromarray(pixels)


@pytest.mark.parametrize('phase', range(4))
@pytest.mark.parametrize('orientation', ['vertical', 'horizontal'])
def test_one_source_pixel_strand_survives_every_subpixel_phase(phase, orientation):
    native = strand(phase)
    if orientation == 'horizontal':
        native = native.transpose(Image.Transpose.TRANSPOSE)
    asset = encode_bitmap(native, sampling='alpha', preserve_resolution=True)
    size = (native.width//4, native.height//4)
    reduced = np.asarray(decode_bitmap(asset, size))
    # A 1px, 128/255 strand has 32/255 average coverage at 4x reduction,
    # across its full length. Its placement must not make it disappear.
    cross_section = reduced[size[1]//2] if orientation == 'vertical' else reduced[:, size[0]//2]
    assert cross_section.sum() == 32
    assert np.count_nonzero(cross_section) > 0
    assert reduced.max() <= 32
    assert not reduced[:2].any() and not reduced[-2:].any()
    assert decode_bitmap(asset, native.size).tobytes() == native.tobytes()


def test_reduction_preserves_gray_cloth_and_a_large_transparent_hole():
    pixels = np.full((256, 384), 96, np.uint8)
    pixels[64:192, 96:288] = 0
    image = Image.fromarray(pixels)
    asset = encode_bitmap(image, sampling='alpha', preserve_resolution=True)
    before = deepcopy(asset)
    actual = np.asarray(decode_bitmap(asset, (96, 64)))
    assert (actual[24:40, 32:64] == 0).all()
    assert (actual[:12] == 96).all()
    assert actual.min() == 0 and actual.max() == 96
    assert asset == before and decode_bitmap(asset, image.size).tobytes() == image.tobytes()


@pytest.mark.parametrize('size', [(64, 32), (73, 41), (256, 32)])
def test_reduction_does_not_allocate_or_consult_nearest_support(monkeypatch, size):
    image = strand(0)
    asset = encode_bitmap(image, sampling='alpha', preserve_resolution=True)
    original = Image.Image.resize
    def resize(self, target, resample=None, *args, **kwargs):
        assert resample != Image.Resampling.NEAREST, 'Reduction must not gate with nearest support'
        return original(self, target, resample, *args, **kwargs)
    monkeypatch.setattr(Image.Image, 'resize', resize)
    assert decode_bitmap(asset, size).getbbox()


def test_legacy_nearest_and_conservative_enlargement_keep_their_behavior():
    image = strand(0)
    legacy = encode_bitmap(image, preserve_resolution=True)
    assert decode_bitmap(legacy, (64, 32)).tobytes() == image.resize((64, 32), Image.Resampling.NEAREST).tobytes()
    asset = encode_bitmap(image, sampling='alpha', preserve_resolution=True)
    enlarged = np.asarray(decode_bitmap(asset, (512, 256)))
    nearest = np.asarray(image.resize((512, 256), Image.Resampling.NEAREST))
    assert not enlarged[nearest == 0].any()


def test_real_worker_gray_and_green_previews_retain_thin_coverage(tmp_path):
    path = tmp_path/'large.png'
    Image.new('RGB', (2400, 1600), (90, 110, 130)).save(path)
    pixels = Image.new('L', (2400, 1600))
    pixels.paste(128, (1200, 160, 1201, 1440))
    mask = {**empty_mask(), 'bitmap':encode_bitmap(pixels, sampling='alpha', preserve_resolution=True)}
    messages = [
        {'id':1, 'op':'open', 'path':str(path)},
        {'id':2, 'op':'render', 'recipe':{}, 'mask':mask, 'mask_view':'grayscale'},
        {'id':3, 'op':'render', 'recipe':{}, 'mask':mask, 'mask_view':'overlay'},
    ]
    child = subprocess.run([sys.executable, str(ROOT/'run.py'), '--worker', str(tmp_path/'cache')],
        input=''.join(json.dumps(item)+'\n' for item in messages), capture_output=True, text=True, encoding='utf8', timeout=45)
    assert child.returncode == 0, child.stderr
    replies = [json.loads(line) for line in child.stdout.splitlines() if line.startswith('{')]
    assert len(replies) == 3 and all(item['ok'] for item in replies), replies
    with Image.open(replies[1]['result']['mask']) as gray:
        size = gray.size
        assert gray.tobytes() == overlay_mask(mask, size, 'grayscale').tobytes()
        assert gray.getbbox()
    with Image.open(replies[2]['result']['mask']) as green:
        assert green.tobytes() == overlay_mask(mask, size).tobytes()
        assert green.getchannel('A').getbbox()
    assert raster_mask(mask, pixels.size).tobytes() == pixels.tobytes()
