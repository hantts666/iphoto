"""Learned constraints, bounded inference, atomic output and AI hint routing."""
from copy import deepcopy

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.segmentation import confidence, service
from iphoto.matting import neural, models


def scene():
    size = (600, 360)
    seed = np.full(size[::-1], 255, np.uint8)
    seed[25:135, 25:175] = 0
    seed[180:230, 25:95] = 0
    # Distant large background must remain definite, including in a solid image.
    seed[250:, 200:] = 0
    logits = np.where(seed > 127, 8., -4.).astype(np.float32)
    logits[25:135, 25:175] -= np.linspace(0, 6, 150, dtype=np.float32)
    logits[180:230, 25:95] -= np.linspace(0, 5, 70, dtype=np.float32)
    mask = {**empty_mask(), "label": "天空", "edge_protection": True,
            "bitmap": encode_bitmap(Image.fromarray(seed), sampling="alpha", preserve_resolution=True)}
    return Image.new("RGB", size, (180, 180, 180)), seed, logits, mask


class MatteEngine:
    provider = "test-AI"
    fallback = ""
    def __init__(self): self.calls = 0
    def predict(self, pixels):
        self.calls += 1
        # Deliberately learned-looking partial alpha, independent of RGB. Real
        # model quality is checked separately on the original natural photos.
        return np.full((neural.INPUT, neural.INPUT), .25, np.float32)


def test_sparse_constraints_are_model_rankings_not_color_classification():
    image, seed, logits, _ = scene()
    before = logits.copy()
    plans = confidence._plans(seed >= 128, logits, [])
    assert len(plans) == 2
    for _, box, support, guide, selected, threshold in plans:
        assert not selected and threshold >= 3
        assert (guide[support] == 128).mean() > .85
        assert (guide[support] == 255).any()
        assert guide.shape == support.shape == (box[3]-box[1], box[2]-box[0])
    assert np.array_equal(logits, before)
    # The planner does not even receive RGB: same-color textures cannot become
    # hard semantic labels through an accidental color threshold.
    assert image.getpixel((40, 40)) == image.getpixel((400, 40))


def test_all_regions_share_monotonic_progress_and_native_source_is_unchanged():
    image, seed, logits, mask = scene()
    before_image = image.tobytes(); before_mask = deepcopy(mask)
    phases = []; model = MatteEngine()
    result, q = confidence.recover(image, mask, logits, [], engine=model,
                                   progress=lambda *p: phases.append(p))
    pixels = np.asarray(raster_mask(result, image.size))
    assert q["guidance"] == "learned-confidence" and len(q["regions"]) == 2
    assert phases == [(i, model.calls) for i in range(1, model.calls+1)]
    assert q["tiles"] == model.calls
    assert q["reopened_pixels"] > 1000 and q["partial_pixels"] > 1000
    assert np.array_equal(pixels[280:, 300:], seed[280:, 300:])
    assert result["label"] == mask["label"] and result["edge_protection"]
    assert result["bitmap"]["sampling"] == "alpha"
    assert mask == before_mask and image.tobytes() == before_image


def test_selected_fine_structure_is_supported_as_well_as_inverse_sky():
    image, seed, logits, mask = scene()
    mask["bitmap"] = encode_bitmap(Image.fromarray(255-seed), sampling="alpha", preserve_resolution=True)
    result, q = confidence.recover(image, mask, -logits, [], engine=MatteEngine())
    assert all(r["class"] == "selected" for r in q["regions"])
    assert q["removed_pixels"] > 1000 and result["bitmap"]["width"] == image.width


def test_actual_points_are_constraints_and_are_not_mutated():
    _, seed, logits, _ = scene()
    points = [[30/599, 30/359, 0], [185/599, 35/359, 1]]
    before = deepcopy(points)
    plans = confidence._plans(seed >= 128, logits, points)
    _, box, _, guide, _, _ = plans[0]
    assert guide[30-box[1], 30-box[0]] == 255
    assert guide[35-box[1], 185-box[0]] == 0
    assert points == before


@pytest.mark.parametrize("case", ["flat", "weak", "large_roi", "unknown_budget", "tile_budget"])
def test_ambiguous_or_oversized_plans_decline_before_loading_model(case, monkeypatch):
    image, seed, logits, mask = scene()
    if case == "flat": logits = np.where(seed > 127, 8., -8.).astype(np.float32)
    if case == "weak": logits /= 10
    if case == "large_roi": monkeypatch.setattr(confidence, "MAX_ROI", 100)
    if case == "unknown_budget": monkeypatch.setattr(neural, "MAX_UNKNOWN", 1)
    if case == "tile_budget": monkeypatch.setattr(neural, "MAX_TILES", 1)
    monkeypatch.setattr(neural, "solve", lambda *a, **k: pytest.fail("Unbounded plan reached inference"))
    assert confidence.recover(image, mask, logits, []) is None


@pytest.mark.parametrize("bad", [np.full((10, 10), np.nan), np.zeros((10,)), np.zeros((1, 10))])
def test_invalid_semantic_fields_are_rejected(bad):
    image, _, _, mask = scene()
    with pytest.raises(ValueError, match="模型输出无效"):
        confidence.recover(image, mask, bad, [])


def test_late_model_failure_keeps_input_mask_and_never_returns_partial_result():
    image, _, logits, mask = scene(); before = deepcopy(mask)
    class Failing(MatteEngine):
        def predict(self, pixels):
            if self.calls >= 2: raise ValueError("model failed in local ROI")
            return super().predict(pixels)
    with pytest.raises(ValueError, match="local ROI"):
        confidence.recover(image, mask, logits, [], engine=Failing())
    assert mask == before


def test_anchor_violation_and_total_time_limit_never_publish_a_result(monkeypatch):
    image, _, logits, mask = scene()
    with pytest.raises(ValueError, match="提示点"):
        confidence.recover(image, mask, logits, [[300/599, 249/359, 1]], engine=MatteEngine())
    monkeypatch.setattr(confidence, "MAX_SECONDS", -1)
    with pytest.raises(ValueError, match="超时"):
        confidence.recover(image, mask, logits, [], engine=MatteEngine())


def test_hint_only_AI_job_receives_internal_anchor_without_visible_point_changes(monkeypatch):
    image, seed, logits, mask = scene(); captured = []
    class SegmentEngine:
        def predict(self, *args): return logits[None], np.array([.99]), {}
    monkeypatch.setattr(models, "available", lambda: True)
    monkeypatch.setattr(service, "backend", lambda: SegmentEngine())
    monkeypatch.setattr(service, "guided_edge", lambda *a, **k: Image.fromarray(seed))
    def recover(image, result, semantic, anchors, **kwargs):
        captured.append(deepcopy(anchors)); return result, {"guidance": "learned-confidence", "warnings": []}
    monkeypatch.setattr(confidence, "recover", recover)
    before = deepcopy(mask)
    result, q = service.segment(image, hint=mask)
    assert captured and len(captured[0]) == 1 and captured[0][0][2] == 1
    assert mask == before and result["label"] == "天空"
    assert q["detail_recovery"]["guidance"] == "learned-confidence"


@pytest.mark.parametrize("selected", [False, True])
def test_larger_regions_need_a_semantic_core_and_retain_uncertain_detail(monkeypatch, selected):
    _, seed, logits, _ = scene()
    logits[25:135,25:175] -= 1  # Enough strong interior to pass the core gate.
    monkeypatch.setattr(confidence, "SPARSE_ROI", 20_000)
    hard = seed >= 128
    if selected:
        hard, logits = ~hard, -logits
    plans = confidence._plans(hard, logits, [])
    assert plans
    _, box, support, guide, class_, _ = plans[0]
    component = hard[box[1]:box[3], box[0]:box[2]] == selected
    margin = confidence._local_logits(logits, box, (600, 360)) * (1 if selected else -1)
    assert class_ == selected and guide.size > confidence.SPARSE_ROI
    assert (guide[component] == 255).mean() >= .5
    assert np.any((guide == 128) & component & (margin < confidence.CORE_MARGIN))
    assert np.any((guide == 128) & support & ~component)
    # Geometry never invents solid foreground from a weak neural field.
    weak = np.where(hard, 4., -4.).astype(np.float32)
    weak[25:135, 25:175] += np.linspace(0, 1.5, 150, dtype=np.float32) * (1 if selected else -1)
    assert confidence._plans(hard, weak, []) == []


def test_weak_regions_are_declined_before_native_float_allocations(monkeypatch):
    _, seed, _, _ = scene()
    monkeypatch.setattr(confidence, "_local_logits", lambda *a: pytest.fail("Weak field allocated a native ROI"))
    weak = np.where(seed > 127, 8., -2.).astype(np.float32)
    assert confidence._plans(seed >= 128, weak, []) == []


def test_interpolation_bounds_never_reject_a_field_that_passes_seed_gates():
    rng = np.random.default_rng(120)
    for size, box in [((6016,4016),(0,0,190,140)), ((500,300),(277,114,490,296)),
                      ((25,17),(1,1,24,16))]:
        for selected in (False, True):
            for scale in (.00001,.1,.499,.501,1,8):
                logits = rng.uniform(2.9-scale, 2.9+scale, (37,53)).astype(np.float32)
                if not selected:
                    logits = -logits
                local = confidence._local_logits(logits, box, size) * (1 if selected else -1)
                threshold = float(np.percentile(local,95))
                if threshold >= 3 and threshold-float(np.median(local)) >= .5:
                    assert confidence._could_supply_seeds(logits, box, size, selected)


def test_planning_deadline_covers_work_before_the_first_matte_tile(monkeypatch):
    _, seed, logits, _ = scene()
    monkeypatch.setattr(confidence, "perf_counter", lambda: 11.)
    monkeypatch.setattr(confidence, "MAX_SECONDS", 1.)
    monkeypatch.setattr(confidence, "_local_logits", lambda *a: pytest.fail("Expired planning sampled an ROI"))
    with pytest.raises(ValueError, match="规划超时"):
        confidence._plans(seed >= 128, logits, [], started=9.)
