"""Generated framing is measured from protected content, never guessed by size."""
from copy import deepcopy

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.generated_alignment import align_generated


def reference():
    noise = np.random.default_rng(151).integers(0, 256, (512, 640, 3), dtype=np.uint8)
    picture = Image.fromarray(cv2.GaussianBlur(noise, (5, 5), .9))
    draw = ImageDraw.Draw(picture)
    for y in range(25, 512, 55):
        for x in range(25, 640, 65):
            draw.text((x, y), str(x+y), fill=(x % 255, y % 255, 240))
    return picture


def selection():
    mask = Image.new('L', (640, 512))
    ImageDraw.Draw(mask).ellipse((200, 150, 430, 380), fill=255)
    return mask


def displaced(picture, scale=.91, angle=2, shift=(22, 8)):
    transform = cv2.getRotationMatrix2D((320, 256), angle, scale)
    transform[:, 2] += shift
    return Image.fromarray(cv2.warpAffine(np.asarray(picture), transform, picture.size,
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT))


@pytest.mark.parametrize('output_size', [(640, 512), (960, 768)])
def test_same_canvas_can_hide_content_drift_and_alignment_preserves_the_actual_edit(output_size):
    original, allowed = reference(), selection()
    changed = np.array(original)
    yy, xx = np.ogrid[:512, :640]
    edited = (xx-315)**2+(yy-265)**2 < 45**2
    changed[edited, 0] = np.minimum(changed[edited, 0].astype(int)+55, 255)
    desired = Image.fromarray(changed)
    generated = displaced(desired).resize(output_size, Image.Resampling.LANCZOS)
    snapshots = [v.tobytes() for v in (original, generated, allowed)]
    output, quality = align_generated(original, generated, allowed)
    assert output.size == output_size and quality['status'] == 'aligned'
    assert quality['inliers'] >= 24 and quality['scale'] == pytest.approx(1/.91, abs=.01)
    assert [v.tobytes() for v in (original, generated, allowed)] == snapshots
    # Independent known content transform and deliberate edit are the oracle.
    target = np.asarray(desired.resize(output_size, Image.Resampling.LANCZOS)).astype(float)
    permit = np.asarray(allowed.resize(output_size, Image.Resampling.NEAREST)) > 0
    before = np.abs(np.asarray(generated).astype(float)-target)[permit].mean()
    after = np.abs(np.asarray(output).astype(float)-target)[permit].mean()
    assert after < 7 and after < before*.4
    center = output.resize(original.size, Image.Resampling.LANCZOS).getpixel((315, 265))
    assert center[0] > original.getpixel((315, 265))[0]+35


def test_uniform_paint_is_explicitly_not_a_measured_registration():
    output, quality = align_generated(reference(), Image.new('RGB', (800, 640), 'purple'), selection())
    assert output.getextrema() == ((128, 128), (0, 0), (128, 128))
    assert quality == {'status': 'uniform_pixels', 'inliers': 0}


def test_authorized_whole_frame_replacement_has_no_local_compositing_boundary():
    generated = displaced(reference())
    output, quality = align_generated(reference(), generated, Image.new('L', generated.size, 255))
    assert output.tobytes() == generated.tobytes()
    assert quality == {'status': 'whole_frame_replacement', 'inliers': 0}


@pytest.mark.parametrize('case', ['no_context', 'featureless_reference', 'new_content', 'localized_references',
                                 'large_scale', 'missing_coverage', 'wrong_aspect', 'rgba'])
def test_unproven_or_uncovered_geometry_is_rejected(case):
    original, allowed = reference(), selection()
    generated = displaced(original)
    if case == 'no_context':
        allowed = Image.new('L', original.size, 255)
        ImageDraw.Draw(allowed).rectangle((0, 0, 6, 511), fill=0)
    elif case == 'featureless_reference':
        original = Image.new('RGB', original.size, 'gray')
    elif case == 'new_content':
        generated = Image.fromarray(np.random.default_rng(42).integers(0, 256, (512, 640, 3), dtype=np.uint8))
    elif case == 'localized_references':
        restricted = Image.new('RGB', original.size, 'gray')
        restricted.paste(original.crop((0, 0, 110, 100)), (0, 0))
        original = restricted
        generated = displaced(original)
    elif case == 'large_scale':
        generated = displaced(original, scale=.65, angle=0, shift=(0, 0))
    elif case == 'missing_coverage':
        generated = displaced(original, scale=1, angle=0, shift=(40, 0))
        allowed = Image.new('L', original.size)
        ImageDraw.Draw(allowed).rectangle((600, 150, 639, 380), fill=255)
    elif case == 'wrong_aspect':
        generated = generated.resize((640, 490))
    elif case == 'rgba':
        generated = generated.convert('RGBA')
    before = deepcopy([original.tobytes(), generated.tobytes(), allowed.tobytes()])
    with pytest.raises(ValueError, match='照片未改变'):
        align_generated(original, generated, allowed)
    assert [original.tobytes(), generated.tobytes(), allowed.tobytes()] == before
