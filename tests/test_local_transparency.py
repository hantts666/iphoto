"""Local AI editing contracts; controlled engines do not prove image quality."""

from copy import deepcopy

import numpy as np
from PIL import Image
import pytest
from scipy.ndimage import distance_transform_edt

from iphoto.controllers import matting
from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.matting import local
from test_ai import wait_for
from test_editor import settled
from test_selection_controller import editor as shared_editor

editor = shared_editor


class Engine:
    provider = "fixture"
    fallback = ""

    def __init__(self, value=.4):
        self.value = value
        self.calls = []

    def predict(self, pixels):
        self.calls.append(pixels.copy())
        return np.full(pixels.shape[2:], self.value, np.float32)


def scene():
    image = Image.new("RGB",(320,240),(80,100,130))
    alpha = Image.new("L",image.size,0)
    alpha.paste(255,(40,35,230,220))
    alpha.paste(117,(135,55,150,75))
    mask = empty_mask()
    mask.update(bitmap=encode_bitmap(alpha,sampling="alpha",preserve_resolution=True),label="original")
    stroke = {"points":[[.125,.4],[.125,.6]],"radius":.035}
    return image,mask,stroke


@pytest.mark.parametrize("extra", [{}, {"feather":.01}, {"edge_shift":1},
                                 {"color_recovery":True},
                                 {"inverted":True,"color_recovery":True}])
def test_every_unpainted_effective_alpha_byte_survives(extra):
    image,mask,stroke=scene();mask.update(extra)
    original=deepcopy(mask);engine=Engine()
    before=np.asarray(raster_mask(mask,image.size));scope=np.asarray(local.stroke_mask(stroke,image.size))>0
    result,q=local.refine(image,mask,stroke,engine=engine)
    after=np.asarray(raster_mask(result,image.size))
    assert np.array_equal(after[~scope],before[~scope])
    assert q["changed_pixels"]==np.count_nonzero(after!=before)>0 and engine.calls
    assert q["local_refinement"] and q["mask_size"]==list(image.size)
    assert result["bitmap"]["width"]==image.width
    assert mask==original and result["feather"]==0 and result["ops"]==[]
    for key in ("color_recovery",):
        if key in mask:assert result[key]==mask[key]


def test_painted_opaque_interior_is_reopened_even_away_from_the_old_contour():
    image,mask,_=scene();stroke={"points":[[.23,.5]],"radius":.025};engine=Engine()
    result,q=local.refine(image,mask,stroke,engine=engine)
    assert raster_mask(mask,image.size).getpixel((74,120))==255
    assert raster_mask(result,image.size).getpixel((74,120))==102
    assert np.any(engine.calls[0][0,3]==128/255) and q["changed_pixels"]>0


def test_recovered_hair_joins_inside_the_stroke_without_a_hard_patch_edge():
    image,mask,stroke=scene()
    scope=np.asarray(local.stroke_mask(stroke,image.size))>0
    before=np.asarray(raster_mask(mask,image.size))
    result,q=local.refine(image,mask,stroke,engine=Engine(1))
    after=np.asarray(raster_mask(result,image.size))
    distance=distance_transform_edt(scope)
    outer=(distance==1)&(before==0)
    inner=(distance>=q["join_width"])&(before==0)
    assert outer.any() and inner.any()
    assert 0<after[outer].max()<255 and np.all(after[inner]==255)
    assert np.array_equal(after[~scope],before[~scope])


@pytest.mark.parametrize("target", ["face", "face_skin", "body_skin"])
def test_semantic_protected_zeros_stay_zero_and_bindings_survive(target):
    image,mask,stroke=scene();mask["semantic_target"]=target
    if target!="body_skin":mask["face_binding"]={"face_id":"local-face-1","source_sha256":"a"*64}
    result,_=local.refine(image,mask,stroke,engine=Engine())
    before=np.asarray(raster_mask(mask,image.size));after=np.asarray(raster_mask(result,image.size))
    assert not np.any(after[before==0]) and result["semantic_target"]==target
    if target!="body_skin":assert result["face_binding"]==mask["face_binding"]


def test_original_face_part_scope_caps_painted_alpha():
    image,mask,stroke=scene()
    ceiling=empty_mask();ceiling["bitmap"]=encode_bitmap(Image.new("L",image.size,80),preserve_resolution=True)
    mask.update(semantic_target="face_skin",face_part="nose",face_part_scope=ceiling)
    result,_=local.refine(image,mask,stroke,engine=Engine(1))
    scope=np.asarray(local.stroke_mask(stroke,image.size))>0
    assert np.max(np.asarray(raster_mask(result,image.size))[scope])<=80
    assert result["face_part_scope"]==ceiling


def test_local_and_distant_prompts_keep_their_constraints():
    image,mask,stroke=scene()
    result,_=local.refine(image,mask,stroke,points=[[.126,.5,1],[.95,.95,0]],engine=Engine())
    assert raster_mask(result,image.size).getpixel((round(.126*319),round(.5*239)))==255
    with pytest.raises(ValueError,match="重叠"):
        local.refine(image,mask,stroke,points=[[.126,.5,1],[.126,.5,0]],engine=Engine())


@pytest.mark.parametrize("stroke", [{}, {"points":[],"radius":.01}, {"points":[[True,.3]],"radius":.01},
                                   {"points":[[.2,.3]],"radius":float("nan")}, {"points":[[.2,.3]],"radius":0},
                                   {"points":[[.2,.3]],"radius":.01,"unknown":True}])
def test_invalid_strokes_fail_before_loading_the_model(stroke,monkeypatch):
    image,mask,_=scene()
    monkeypatch.setattr(local.neural,"backend",lambda:pytest.fail("invalid stroke must not initialize model"))
    with pytest.raises(ValueError):local.refine(image,mask,stroke)


@pytest.mark.parametrize("budget", ["roi","unknown"])
def test_local_work_budgets_fail_before_model_allocation(budget,monkeypatch):
    image,mask,stroke=scene()
    if budget=="roi":monkeypatch.setattr(local,"MAX_ROI",100)
    else:monkeypatch.setattr(local.neural,"MAX_UNKNOWN",10)
    monkeypatch.setattr(local.neural,"backend",lambda:pytest.fail("over budget must not initialize model"))
    with pytest.raises(ValueError):local.refine(image,mask,stroke)


def test_unchanged_prediction_retains_original_representation_and_draft_history(editor):
    image,mask,_=scene();stroke={"points":[[.23,.5]],"radius":.01}
    result,q=local.refine(image,mask,stroke,engine=Engine(1))
    assert result==mask and q["changed_pixels"]==0
    editor._set_candidate(mask);wait_for(lambda:settled(editor))
    before=deepcopy((editor._candidate,editor._draft_history,editor._generation,editor._layers,editor._pixel_points))
    matting.complete(editor,{"mask":result,"quality":q},points=[])
    assert before==(editor._candidate,editor._draft_history,editor._generation,editor._layers,editor._pixel_points)


@pytest.mark.parametrize("change",["layer","photo","generation","tool"])
def test_stale_paint_gesture_never_dispatches_worker(editor,monkeypatch,change):
    editor.beginSelection("empty")
    editor.selection._tool="transparency"
    layer,photo,generation=editor.activeLayerId,editor.originalUrl,editor._generation
    if change=="layer":layer="missing"
    elif change=="photo":photo="old"
    elif change=="generation":generation-=1
    else:editor.selection._tool="brush"
    monkeypatch.setattr(editor,"_request",lambda *_a,**_k:pytest.fail("stale stroke must not start work"))
    assert not editor.selection.paintTransparency(layer,photo,generation,[[.5,.5]],.01)


def test_no_reference_classes_rejects_without_guessing_alpha(monkeypatch):
    image,mask,_=scene();mask=empty_mask(True)
    monkeypatch.setattr(local.neural,"backend",lambda:pytest.fail("no reference must not initialize model"))
    with pytest.raises(ValueError,match="目标或背景"):
        local.refine(image,mask,{"points":[[.5,.5]],"radius":.03})


def test_progress_and_output_use_a_real_neural_solver_contract():
    image,mask,stroke=scene();progress=[]
    _,q=local.refine(image,mask,stroke,engine=Engine(),progress=lambda i,n:progress.append((i,n)))
    assert progress==[(1,1)] and q["tiles"]==1
