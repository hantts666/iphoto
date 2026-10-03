"""Neural score projection and source-pixel protection, not accuracy fixtures."""
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image
import pytest

from iphoto.document import raster_mask
from iphoto.segmentation import face_skin
from test_face_skin import mask


def scores():
    values = np.full((19,512,512), -100, np.float32)
    values[1,:,:256] = 1; values[18,:,:256] = 0
    values[1,:,256:] = 0; values[18,:,256:] = 9
    return values


def test_projecting_scores_before_classification_keeps_neural_boundary_information():
    value = scores(); size = (1300,137)
    x = (np.arange(size[0],dtype=np.float32)+.5)*512/size[0]-.5
    y = (np.arange(size[1],dtype=np.float32)+.5)*512/size[1]-.5
    xx,yy = np.meshgrid(x,y)
    expected = cv2.remap(np.ascontiguousarray(value.transpose(1,2,0)),xx,yy,
                        cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE).argmax(2).astype(np.uint8)
    actual = face_skin.native_labels(value,size)
    assert np.array_equal(actual,expected)
    enlarged_hard_labels = np.asarray(Image.fromarray(value.argmax(0).astype(np.uint8)).resize(size,Image.Resampling.NEAREST))
    assert np.any(actual != enlarged_hard_labels)


def test_native_projection_is_bounded_in_both_dimensions_and_handles_maximum_width(monkeypatch):
    calls = []; original = cv2.remap
    def remap(source,x,y,*args,**kwargs):
        calls.append(x.shape)
        assert source.shape == (512,512,19) and x.shape[0] <= 64 and x.shape[1] <= 1024
        return original(source,x,y,*args,**kwargs)
    monkeypatch.setattr(cv2,"remap",remap)
    result = face_skin.native_labels(scores(),(32768,65))
    assert result.shape == (65,32768) and len(calls) == 64
    assert result[0,0] == 1 and result[-1,-1] == 18


@pytest.mark.parametrize("bad", ["shape", "dtype", "nan", "size", "pixels"])
def test_invalid_scores_or_oversized_native_geometry_do_not_produce_labels(bad):
    value=scores();size=(800,900)
    if bad == "shape":value=value[:18]
    elif bad == "dtype":value=value.astype(np.float64)
    elif bad == "nan":value[0,0,0]=np.nan
    elif bad == "size":size=(32769,2)
    else:size=(8000,8000)
    with pytest.raises(ValueError):face_skin.native_labels(value,size)


def test_native_parser_uses_validated_model_scores_and_the_original_crop_dimensions():
    value=scores();calls=[]
    parser=face_skin.FaceParser.__new__(face_skin.FaceParser)
    parser.session=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(name="pixels")],
        run=lambda outputs,inputs:calls.append(inputs["pixels"].shape) or [value[None]])
    result=parser.predict_native(Image.new("RGB",(1300,137)))
    assert calls == [(1,3,512,512)]
    expected=face_skin.native_labels(value,(1300,137))
    # The parser omits distant non-face crop content, while preserving the
    # full score projection at the actual facial boundary.
    assert np.array_equal(result == 1, expected == 1)
    assert result[0,-1] == 0


def test_native_skin_protection_is_in_source_pixels_and_keeps_a_narrow_nose():
    image=Image.new("RGB",(1200,900));labels=np.zeros((900,1200),np.uint8)
    labels[100:700,100:700]=1
    labels[300:310,700:708]=10  # Narrow visible nose, only eight original pixels wide.
    labels[300:310,708:720]=18
    labels[400:425,400:430]=4
    labels[500:525,400:430]=12
    engine=SimpleNamespace(predict_native=lambda patch:labels)
    result,quality=face_skin.segment(image,mask(),[[.3,.3,1]],crop=[0,0,1,1],engine=engine)
    alpha=raster_mask(result,image.size)
    assert alpha.getpixel((704,305)) == 255 and alpha.getpixel((708,305)) == 0
    assert alpha.getpixel((415,410)) == 0 and alpha.getpixel((415,510)) == 0
    assert quality["boundary_grid"] == "source" and quality["boundary_size"] == [1200,900]


def test_native_landmark_guards_use_image_geometry_on_non_square_crops():
    labels=np.zeros((900,1200),np.uint8);labels[100:800,250:1050]=1
    points={"eyes":[[450,350],[650,350]],"mouth":[[470,550],[630,550]]}
    guard=face_skin.feature_guard_pixels(points,labels)
    assert guard.shape == labels.shape and guard[350,650] and guard[550,550]
    assert not guard[450,550]


def test_collapsed_occluded_landmarks_do_not_carve_a_false_mouth_in_plain_cheek_skin():
    labels=np.zeros((900,1200),np.uint8);labels[100:800,250:1050]=1
    labels[500:520,400:500]=12
    labels[540:570,725:735]=13  # A nearby lip boundary is not evidence at the guessed cheek centre.
    guessed={"eyes":[[420,340],[440,340]],"mouth":[[750,550],[780,560]]}
    assert not face_skin.feature_guard_pixels(guessed,labels).any()
    guessed["mouth"]=[[440,510],[460,510]]
    guard=face_skin.feature_guard_pixels(guessed,labels)
    assert guard[510,450] and not guard[550,765]
