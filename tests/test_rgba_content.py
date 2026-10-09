"""Independent optical coverage, persistence, geometry and real import transactions."""
import json
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.document import new_layer, render_layers, render_detail_tile, validate_layers, project_version
from iphoto.foreground_content import read_foreground
from iphoto.generated_alignment import align_generated
from iphoto.masks import encode_bitmap
from iphoto.pixel_patch import encode_patch, validate_patch, _decode
from iphoto.preview_cache import LayerPreviewCache
from iphoto.rgba_content import replace_content
from iphoto.paths import ROOT
from test_generated_alignment import reference, selection


def optical(new, old, weight):
    n, o = np.asarray(new.convert('RGBA'), dtype=float), np.asarray(old.convert('RGBA'), dtype=float)
    w = np.asarray(weight, dtype=float)[..., None] / 255
    alpha = (n[..., 3:] * w + o[..., 3:] * (1-w)) / 255
    rgb = (n[..., :3] * n[..., 3:] / 255 * w + o[..., :3] * o[..., 3:] / 255 * (1-w)) / np.maximum(alpha, 1e-12)
    expected = np.rint(np.clip(np.concatenate((rgb, alpha * 255), axis=2), 0, 255)).astype('uint8')
    values = np.asarray(weight)
    expected[values == 0] = o[values == 0]
    expected[values == 255] = n[values == 255]
    return expected


def content(size=(300, 200)):
    image = Image.new('RGBA', size, (220, 20, 190, 0))
    d = ImageDraw.Draw(image)
    d.rectangle((size[0]//3, 20, size[0]-30, size[1]-20), fill=(70, 35, 20, 255))
    d.line((20, size[1]//2, size[0]-30, size[1]//2), fill=(70, 35, 20, 128), width=3)
    d.ellipse((size[0]//2, 40, size[0]//2+20, 60), fill=(220, 20, 190, 0))
    return image


def content_layer(pixels, canvas=None):
    canvas = canvas or pixels.size
    layer = new_layer('透明内容', True)
    layer['pixel_patch'] = encode_patch(pixels, canvas, (0, 0, *canvas), preserve_alpha=True)
    return layer


def test_optical_replacement_uses_both_intrinsic_coverages_and_preserves_exact_endpoints():
    rng = np.random.default_rng(163)
    old = Image.fromarray(rng.integers(0, 256, (9, 300, 4), dtype='uint8'))
    new = Image.fromarray(rng.integers(0, 256, (9, 300, 4), dtype='uint8'))
    weight = Image.fromarray(np.tile(np.array([0, 1, 64, 128, 254, 255], dtype='uint8'), (9, 50)))
    before = [v.tobytes() for v in (new, old, weight)]
    actual = replace_content(new, old, weight)
    assert np.abs(np.asarray(actual).astype(int)-optical(new, old, weight).astype(int)).max() <= 1
    assert [v.tobytes() for v in (new, old, weight)] == before
    assert replace_content(new, old, Image.new('L', old.size, 255)).tobytes() == new.tobytes()
    assert replace_content(new, old, Image.new('L', old.size)).tobytes() == old.tobytes()


@pytest.mark.parametrize('opacity', [1, .5])
def test_content_alpha_is_not_a_second_layer_mask_and_group_cache_matches_optics(opacity):
    pixels = content()
    original = Image.new('RGB', pixels.size, (180, 220, 240))
    layer = content_layer(pixels)
    group = new_layer('组', True, kind='group')
    group['opacity'] = opacity
    layer['parent_id'] = group['id']
    layers = validate_layers([group, layer])
    result = render_layers(original, layers)
    expected = optical(pixels, original, Image.new('L', pixels.size, round(opacity*255)))
    assert np.abs(np.asarray(result).astype(int)-expected.astype(int)).max() <= 1
    if opacity == 1:
        assert result.tobytes() == pixels.tobytes()
        assert result.getpixel((25, 100)) == (70, 35, 20, 128)
        black = Image.alpha_composite(Image.new('RGBA', pixels.size, 'black'), result)
        assert black.getpixel((25, 100))[:3] == (35, 18, 10)
    cache = LayerPreviewCache()
    assert cache.render(original, layers).tobytes() == result.tobytes()
    assert cache.render(original, layers).tobytes() == result.tobytes() and cache.reused
    assert render_detail_tile(original, layers, [15, 25, 250, 175]).tobytes() == result.crop((15, 25, 250, 175)).tobytes()


def test_scope_holes_opacity_and_scaled_native_tiles_preserve_source():
    original = Image.new('RGBA', (600, 400), (15, 65, 180, 211))
    layer = content_layer(content(), original.size)
    mask = Image.new('L', original.size)
    d = ImageDraw.Draw(mask)
    d.rectangle((60, 50, 530, 350), fill=160)
    d.ellipse((200, 100, 300, 200), fill=0)
    layer['mask']['bitmap'] = encode_bitmap(mask, sampling='alpha', preserve_resolution=True)
    layer['mask']['base'] = 'empty'
    layer['opacity'] = .6
    rendered = render_layers(original, [layer])
    assert np.array_equal(np.asarray(rendered)[np.asarray(mask) == 0], np.asarray(original)[np.asarray(mask) == 0])
    tile = render_detail_tile(original, [layer], [70, 60, 520, 330])
    assert tile.tobytes() == rendered.crop((70, 60, 520, 330)).tobytes()
    assert original.getextrema() == ((15, 15), (65, 65), (180, 180), (211, 211))


def test_content_protocol_keeps_legacy_rgb_explicit_and_negotiates_new_project_version():
    pixels = content()
    rgba = content_layer(pixels)
    assert _decode(rgba['pixel_patch']['png']).tobytes() == pixels.tobytes()
    assert project_version([rgba]) == '1.13'
    base = new_layer('旧调整', True)
    assert project_version([base], regions={'layers':[rgba]}) == '1.13'
    legacy = encode_patch(pixels, pixels.size, (0, 0, *pixels.size))
    assert 'compositing' not in legacy and _decode(legacy['png']).mode == 'RGB'
    base['pixel_patch'] = legacy
    assert project_version([base]) == '1.11'
    for bad in ({k:v for k,v in rgba['pixel_patch'].items() if k != 'compositing'},
                {**legacy, 'compositing':'replace_rgba'},
                {**rgba['pixel_patch'], 'compositing':'overlay'},
                {**rgba['pixel_patch'], 'sha256':'0'*64}):
        with pytest.raises(ValueError):
            validate_patch(bad)


@pytest.mark.parametrize('case', ['opaque', 'empty', 'no_alpha', 'aspect', 'wrong_format', 'oversize', 'invalid_icc'])
def test_import_rejects_invalid_foreground_without_reinterpreting_rgb_as_transparency(tmp_path, case):
    image = content()
    if case == 'opaque': image.putalpha(255)
    elif case == 'empty': image.putalpha(0)
    elif case in ('no_alpha', 'wrong_format'): image = image.convert('RGB')
    elif case == 'aspect': image = image.resize((200, 300))
    elif case == 'oversize': image = Image.new('RGBA', (2049, 2049), (20, 30, 50, 0))
    path = tmp_path/'invalid.png'
    image.save(path, format='JPEG' if case == 'wrong_format' else 'PNG',
               **({'icc_profile':b'not an ICC profile'} if case == 'invalid_icc' else {}))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        read_foreground(path, (300, 200))
    assert path.read_bytes() == before


def test_palette_transparency_is_preserved_and_import_content_is_portable(tmp_path):
    pixels = content().quantize(colors=16)
    path = tmp_path/'indexed.png'
    pixels.save(path)
    loaded = read_foreground(path, pixels.size)
    assert loaded['has_clear_background']
    assert _decode(loaded['patch']['png']).tobytes() == pixels.convert('RGBA').tobytes()
    path.unlink()
    assert _decode(loaded['patch']['png']).size == pixels.size


def test_whole_frame_rgba_alignment_keeps_alpha_and_does_not_claim_a_measured_fit():
    generated = content((640, 512))
    actual, metrics = align_generated(reference(), generated, Image.new('L', generated.size, 255))
    assert actual.tobytes() == generated.tobytes() and actual.mode == 'RGBA'
    assert metrics['status'] == 'whole_frame_replacement'


def test_rgba_geometry_uses_opaque_context_and_warps_rgb_alpha_together():
    original, permitted = reference(), selection()
    wanted = original.convert('RGBA')
    # A translucent edit inside protected, texture-rich context. Invisible
    # saturated RGB should never leak into a reconstructed fringe.
    a = np.full((512, 640), 255, dtype='uint8')
    yy, xx = np.ogrid[:512, :640]
    a[(xx-315)**2+(yy-265)**2 < 28**2] = 0
    a[((xx-315)**2+(yy-265)**2 >= 28**2) & ((xx-315)**2+(yy-265)**2 < 35**2)] = 128
    wanted.putalpha(Image.fromarray(a))
    p = np.asarray(wanted, dtype=np.float32).copy()
    p[..., :3] *= p[..., 3:] / 255
    matrix = cv2.getRotationMatrix2D((320, 256), 2, .91)
    matrix[:, 2] += (22, 8)
    moved = cv2.warpAffine(p, matrix, wanted.size, borderMode=cv2.BORDER_REFLECT)
    moved[..., :3] /= np.maximum(moved[..., 3:] / 255, 1e-8)
    moved = np.rint(np.clip(moved, 0, 255)).astype('uint8')
    moved[moved[..., 3] == 0, :3] = (255, 0, 255)
    generated = Image.fromarray(moved)
    result, quality = align_generated(original, generated, permitted)
    assert result.mode == 'RGBA' and quality['status'] == 'aligned' and quality['inliers'] >= 24
    assert result.getpixel((315, 265))[3] == 0
    # Compare actual composited coverage to independent desired composites.
    for color in ('white', 'black'):
        actual = np.asarray(Image.alpha_composite(Image.new('RGBA', wanted.size, color), result), dtype=float)
        target = np.asarray(Image.alpha_composite(Image.new('RGBA', wanted.size, color), wanted), dtype=float)
        assert np.abs(actual-target)[np.asarray(permitted)>0].mean() < 5


def test_real_worker_import_and_alignment_retain_rgba(tmp_path):
    source, foreground = tmp_path/'source.png', tmp_path/'foreground.png'
    reference().save(source)
    pixels = content((640, 512))
    pixels.save(foreground)
    from iphoto.engine import file_hash
    patch = encode_patch(pixels, pixels.size, (0, 0, *pixels.size), preserve_alpha=True)
    layer = new_layer('范围', True)
    jobs = [
        {'id':1,'op':'open','path':str(source),'generation':0},
        {'id':2,'op':'foreground_import','path':str(foreground),'expected_sha256':file_hash(source),'generation':0},
        {'id':3,'op':'generative_crop','before':[new_layer('原图',True)],'proposed':[layer],
         'soften':False,'expected_sha256':file_hash(source),'generation':0},
        {'id':4,'op':'generative_align','patch':patch,'reference_id':3,
         'expected_sha256':file_hash(source),'generation':0},
    ]
    child = subprocess.run([sys.executable,str(ROOT/'run.py'),'--worker',str(tmp_path/'cache')],
        input=''.join(json.dumps(job)+'\n' for job in jobs),text=True,encoding='utf8',capture_output=True,timeout=40)
    assert child.returncode == 0
    responses = [json.loads(line) for line in child.stdout.splitlines() if line.startswith('{')]
    assert len(responses) == 4 and all(reply['ok'] for reply in responses), child.stdout
    assert _decode(responses[1]['result']['patch']['png']).tobytes() == pixels.tobytes()
    result = responses[3]['result']
    assert result['patch']['compositing'] == 'replace_rgba'
    assert _decode(result['patch']['png']).tobytes() == pixels.tobytes()
    assert result['alignment']['status'] == 'whole_frame_replacement'
