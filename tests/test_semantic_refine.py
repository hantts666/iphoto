"""Semantic exclusions, person identity and bounded neural point corrections."""

from copy import deepcopy
from collections import deque
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.controllers import pixel_selections, worker_bridge
from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.segmentation import service


def fixture(target="face_skin"):
    alpha = Image.new("L", (680, 520))
    draw = ImageDraw.Draw(alpha)
    draw.rectangle((100, 100, 500, 420), fill=255)
    draw.rectangle((310, 240, 345, 265), fill=0)  # protected mouth/eye
    hint = {**empty_mask(), "label": "existing semantic range", "semantic_target": target, 'color_recovery':True,
            "bitmap": encode_bitmap(alpha, sampling="alpha", preserve_resolution=True)}
    if target != "body_skin":
        hint["face_binding"] = {"face_id": "stable-person", "source_sha256": "a"*64}
    return Image.new("RGB", alpha.size, (130, 100, 80)), alpha, hint


class Predictor:
    def __init__(self):
        self.calls = []

    def predict(self, image, coords, labels):
        self.calls.append((image.size, coords.copy(), labels.copy()))
        hard = np.zeros((image.height, image.width), bool)
        hard[5:-5, 5:-5] = True  # deliberately overselect excluded features
        for (x, y), label in zip(coords, labels):
            if label == 0:
                x, y = round(float(x)), round(float(y))
                hard[max(0, y-12):y+13, max(0, x-12):x+13] = False
        return np.where(hard[None], 5., -5.), np.array([.96]), {"encoding_ms": 1}


@pytest.mark.parametrize("target", ["face", "face_skin", "body_skin"])
@pytest.mark.parametrize("interactive", [True, False])
def test_neural_refinement_preserves_class_zeros_identity_and_native_pixels(target, interactive):
    image, prior, hint = fixture(target)
    before = deepcopy(hint), image.tobytes()
    engine = Predictor()
    phases = []
    points = [[320/679, 200/519, 1]] if interactive else []
    result, quality = service.segment(image, hint, points, engine=engine,
                                      progress=lambda *phase: phases.append(phase))
    actual, old = np.asarray(raster_mask(result, image.size)), np.asarray(prior)
    assert actual[250, 325] == 0 and np.all(actual[old == 0] == 0)
    assert result["semantic_target"] == target
    assert result['color_recovery'] is True
    assert result.get("face_binding") == hint.get("face_binding")
    assert quality["mask_size"] == list(image.size) and quality["resolution"] == "source"
    assert "语义范围保护" in quality["model"] and phases == [("semantic_points",)]
    assert (hint, image.tobytes()) == before
    if interactive:
        left, top, right, bottom = quality["crop_box"]
        outside = np.ones(old.shape, bool); outside[top:bottom, left:right] = False
        assert np.array_equal(actual[outside], old[outside])
        assert engine.calls[0][0] == (193, 193) and actual[200, 320] > 127


def test_first_exclusion_uses_an_inferred_keep_point_without_changing_user_prompts():
    image, prior, hint = fixture()
    points = [[320/679, 200/519, 0]]
    engine = Predictor()
    result, quality = service.segment(image, hint, points, engine=engine)
    actual = raster_mask(result, image.size)
    assert actual.getpixel((320, 200)) == 0 and actual.getpixel((150, 350)) == 255
    assert points == [[320/679, 200/519, 0]] and hint["face_binding"] == result["face_binding"]
    assert list(engine.calls[0][2])[:2] == [1, 0] and len(engine.calls[0][2]) <= 6


def test_keep_point_in_protected_class_is_rejected_before_encoding():
    image, prior, hint = fixture()
    engine = Predictor()
    with pytest.raises(ValueError, match="补选"):
        service.segment(image, hint, [[325/679, 250/519, 1]], engine=engine)
    assert not engine.calls and raster_mask(hint, image.size).tobytes() == prior.tobytes()


def test_failed_neural_prompt_never_publishes_an_unsatisfied_correction():
    image, prior, hint = fixture()
    class Wrong:
        def predict(self, patch, coords, labels):
            hard = np.zeros((patch.height, patch.width), bool)
            hard[30:60, 30:60] = True
            return np.where(hard[None], 5., -5.), np.array([.99]), {}
    with pytest.raises(ValueError, match="可靠目标"):
        service.segment(image, hint, [[320/679, 200/519, 1]], engine=Wrong())
    assert raster_mask(hint, image.size).tobytes() == prior.tobytes()


def test_point_controller_allows_first_semantic_exclusion_and_skips_full_photo_warm(monkeypatch):
    image, prior, hint = fixture()
    calls = []
    owner = SimpleNamespace(busy=False, hasRegionDraft=False, hasImage=True,
                            _pixel_points=[], _pixel_hint=hint, _candidate=hint,
                            _notify=lambda *args: pytest.fail(str(args)),
                            _start_warm=lambda: pytest.fail("Whole photo encoding for semantic correction"))
    monkeypatch.setattr(pixel_selections, "start", lambda *args: calls.append(args) or True)
    assert pixel_selections.point(owner, [.4, .4], False)
    assert calls[0][1][0]["points"] == [[.4, .4, 0]]
    assert calls[0][1][0]["hint"]["face_binding"] == hint["face_binding"]
    pixel_selections.warm(owner)


def test_job_dispatch_keeps_semantic_quality_and_avoids_object_matting(monkeypatch):
    image, prior, hint = fixture()
    monkeypatch.setattr("iphoto.segmentation.efficient_sam.backend", lambda: Predictor())
    monkeypatch.setattr("iphoto.matting.neural.refine", lambda *args, **kwargs: pytest.fail("Generic matting"))
    result = service.segment_jobs(image.resize((340, 260)), [{"id": "target", "hint": hint,
                                  "points": [[320/679, 200/519, 1]]}], source=image)
    item = result["items"][0]
    assert item["mask"]["face_binding"] == hint["face_binding"]
    assert "语义范围保护" in item["quality"]["model"]
    assert item["quality"]["mask_size"] == [680, 520]


@pytest.mark.parametrize("semantic", [True, False])
def test_local_embedding_is_not_reported_as_a_prepared_whole_photo(monkeypatch, semantic):
    image, prior, hint = fixture()
    if not semantic:
        hint.pop("semantic_target"); hint.pop("face_binding")
    done = []
    monkeypatch.setattr(pixel_selections, "complete", lambda *args: done.append(True))
    response = {"id": 7, "generation": 9, "ok": True, "result": {"items": []}}
    owner = SimpleNamespace(_pixel_buffer=b"", _pixel_active={"id": 7, "generation": 9,
                            "op": "segment", "context": {"purpose": "points"},
                            "jobs": [{"id": "target", "hint": hint}]},
                            _generation=9, _sha="source", _warm_ready_sha="",
                            _scene=SimpleNamespace(revision=1), _pixel_queue=deque(),
                            changed=SimpleNamespace(emit=lambda: None),
                            _pixel_process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(response)+"\n").encode()),
                            _pump_pixel=lambda: None)
    worker_bridge._pixel_read(owner)
    assert done == [True] and owner._pixel_active is None
    assert owner._warm_ready_sha == ("" if semantic else "source")


def test_six_exclusion_points_report_prompt_budget_before_encoding():
    image, prior, hint = fixture()
    engine = Predictor()
    with pytest.raises(ValueError, match="5 个排除点"):
        service.segment(image, hint, [[.4, .4, 0]]*6, engine=engine)
    assert not engine.calls


def test_source_image_border_is_not_an_artificial_blend_boundary():
    image = Image.new("RGB", (200, 200), "gray")
    prior = Image.new("L", image.size, 255)
    hint = {**empty_mask(), "semantic_target": "face_skin", "label": "border",
            "bitmap": encode_bitmap(prior, sampling="alpha", preserve_resolution=True)}
    engine = Predictor()
    result, quality = service.segment(image, hint, [[0, .5, 0]], engine=engine)
    assert raster_mask(result, image.size).getpixel((0, 100)) == 0
    assert quality["crop_box"][0] == 0
