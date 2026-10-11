"""Independent known subject transforms, confidence and current-patch binding."""
from copy import deepcopy

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.foreground_position import check_position, validate_position
from test_generated_alignment import reference


def subject(picture):
    rgba=picture.convert('RGBA')
    alpha=Image.new('L',picture.size)
    ImageDraw.Draw(alpha).rectangle((40,30,picture.width-30,picture.height-20),fill=255)
    rgba.putalpha(alpha)
    return rgba


@pytest.mark.parametrize('size',[(640,512),(960,768),(320,256)])
def test_matching_subject_at_different_output_resolutions_is_not_warped(size):
    original=reference();rgba=subject(original).resize(size,Image.Resampling.LANCZOS)
    before=original.tobytes(),rgba.tobytes()
    facts=check_position(original,rgba,'actual-patch-sha')
    assert facts['status']=='consistent' and facts['inliers']>=24
    assert facts['scale']==pytest.approx(1.,abs=.005)
    assert abs(facts['rotation_degrees'])<.2 and facts['displacement_p95']<2
    assert validate_position(facts,original.size,rgba.size,'actual-patch-sha')==facts
    assert (original.tobytes(),rgba.tobytes())==before


@pytest.mark.parametrize('change',['translation','scale','rotation'])
def test_measured_drift_cannot_be_approved_as_unchanged_framing(change):
    original=reference();rgba=subject(original)
    matrix=cv2.getRotationMatrix2D((320,256),2.5 if change=='rotation' else 0,1.08 if change=='scale' else 1)
    if change=='translation':matrix[:,2]+=(32,0)
    moved=Image.fromarray(cv2.warpAffine(np.asarray(rgba),matrix,rgba.size,
                                      flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT))
    before=moved.tobytes()
    with pytest.raises(ValueError,match='改变了人物位置或大小'):
        check_position(original,moved,'moved-patch')
    assert moved.tobytes()==before


@pytest.mark.parametrize('case',['textureless','unrelated','concentrated'])
def test_no_reliable_correspondence_is_not_reported_as_verified(case):
    original=reference();rgba=subject(original)
    if case=='textureless':rgba=Image.new('RGBA',rgba.size,(90,70,40,255))
    elif case=='unrelated':rgba=subject(Image.fromarray(np.random.default_rng(188).integers(0,256,(512,640,3),dtype='uint8')))
    else:
        alpha=Image.new('L',rgba.size);ImageDraw.Draw(alpha).rectangle((200,200,300,300),fill=255);rgba.putalpha(alpha)
    with pytest.raises(ValueError,match='无法可靠核对'):
        check_position(original,rgba,'uncertain-patch')


def test_hidden_reference_rgb_is_not_a_position_anchor():
    pixels=reference();original=pixels.convert('RGBA');original.putalpha(0)
    rgba=subject(pixels)
    before=original.tobytes()
    with pytest.raises(ValueError,match='无法可靠核对'):
        check_position(original,rgba,'opaque-hidden-copy')
    assert original.tobytes()==before


@pytest.mark.parametrize('changed',['original','generated','patch','missing','stats'])
def test_worker_position_proof_must_belong_to_current_pixels_and_canvas(changed):
    original=reference();rgba=subject(original)
    facts=deepcopy(check_position(original,rgba,'current-patch'))
    if changed=='original':facts['original_size']=[1280,1024]
    elif changed=='generated':facts['generated_size']=[320,256]
    elif changed=='patch':facts['pixel_sha256']='earlier-patch'
    elif changed=='missing':facts=None
    else:facts['fit_error_p95']=float('nan')
    with pytest.raises(ValueError):validate_position(facts,original.size,rgba.size,'current-patch')
