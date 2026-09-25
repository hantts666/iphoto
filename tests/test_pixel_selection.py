"""Contracts and failure safety. Real-model visual QA is a separate script."""

import json
import numpy as np
import pytest
from PIL import Image, ImageDraw

from iphoto.ai_tasks import parse_selection
from iphoto.document import empty_mask, raster_mask
from iphoto.scene import parse_scene, validate_catalog, SceneIndex
from iphoto.segmentation.edges import guided_edge
from iphoto.segmentation.prompts import from_hint, from_points, validate_points
from iphoto.segmentation.service import choose_candidate, segment
from iphoto.controllers import pixel_selections


def response(data):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]
    }


def ring():
    mask = Image.new("L", (400, 300), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((50, 40, 350, 260), fill=255)
    draw.ellipse((160, 100, 240, 170), fill=0)
    photo = Image.composite(
        Image.new("RGB", mask.size, (230, 160, 60)),
        Image.new("RGB", mask.size, (20, 65, 100)),
        mask,
    )
    hint = empty_mask()
    hint["ops"] = [
        {"kind": "rect", "mode": "add", "points": [[0.10, 0.10], [0.90, 0.90]]}
    ]
    return photo, mask, hint


def test_grounding_box_and_interior_anchor_are_not_a_claimed_final_mask():
    data = {
        "status": "selected",
        "summary": "定位目标",
        "box": [100, 100, 900, 900],
        "point": [300, 400],
    }
    result = parse_selection(response(data))
    assert result["anchor"] == pytest.approx([300 / 999, 400 / 999])
    assert "bitmap" not in result["mask"]
    obj = {
        "name": "环形物",
        "category": "物体",
        "box": data["box"],
        "point": data["point"],
    }
    scene = parse_scene(
        response({"status": "analyzed", "summary": "定位", "objects": [obj]})
    )
    assert validate_catalog(scene)["objects"][0]["anchor"] == result["anchor"]
    for box, point in (
        ([900, 100, 100, 900], [300, 400]),
        ([0, 0, 800, 800], [999, 10]),
        ([0, 0, 999, 999], [True, 300]),
    ):
        with pytest.raises(ValueError):
            parse_selection(response({**data, "box": box, "point": point}))
    assert (
        parse_selection(
            response(
                {"status": "unsupported", "summary": "没有目标", "box": [], "point": []}
            )
        )["status"]
        == "unsupported"
    )


@pytest.mark.parametrize(
    "points",
    [
        [[0.1, 0.2, 2]],
        [[float("nan"), 0.2, 1]],
        [[True, 0.2, 1]],
        [[0.2, 1.1, 0]],
        [[0.2, 0.2, 1]] * 7,
    ],
)
def test_invalid_or_excessive_points_rejected(points):
    with pytest.raises(ValueError):
        validate_points(points)


def test_prompt_budget_and_negative_only_rejection():
    photo, mask, hint = ring()
    points = [
        [0.2, 0.2, 1],
        [0.5, 0.5, 0],
        [0.6, 0.6, 1],
        [0.7, 0.7, 1],
        [0.8, 0.8, 1],
        [0.3, 0.3, 0],
    ]
    coords, labels = from_hint(mask, points)
    assert len(coords) == len(labels) == 6
    assert list(labels) == [1, 0, 1, 1, 1, 0]
    with pytest.raises(ValueError):
        from_points([[0.5, 0.5, 0]], photo.size)
    with pytest.raises(ValueError):
        from_hint(Image.new("L", photo.size, 0))


def test_pipeline_preserves_holes_and_curves_instead_of_clipping_to_hint():
    photo, mask, hint = ring()
    hard = np.asarray(mask) > 0

    class Predictor:
        def predict(self, image, coords, labels):
            assert image.size == photo.size
            return (
                np.stack([np.where(hard, 5.0, -5.0), np.ones(hard.shape)]),
                np.array([0.96, 0.99]),
                {"encoding_ms": 1, "decoding_ms": 1},
            )

    result, quality = segment(photo, hint, [[0.25, 0.5, 1]], engine=Predictor())
    out = np.asarray(raster_mask(result, photo.size))
    assert "bitmap" in result and not result["ops"]
    assert out[130, 200] == 0 and out[150, 100] == 255 and out[45, 55] == 0
    iou = ((out > 127) & hard).sum() / ((out > 127) | hard).sum()
    assert iou > 0.98 and quality["candidate"] == 0


def test_contradictory_or_empty_prediction_is_rejected():
    shape = (60, 80)
    coords = np.array([[40, 30]], np.float32)
    labels = np.array([1], np.float32)
    with pytest.raises(ValueError):
        choose_candidate(np.full((3, *shape), -5.0), np.ones(3), coords, labels)
    bad = np.full((1, *shape), -5.0)
    bad[0, :5, :5] = 5
    with pytest.raises(ValueError):
        choose_candidate(bad, np.ones(1), coords, labels)


def test_edge_processing_protects_definite_background_and_foreground():
    photo, mask, _ = ring()
    alpha = np.asarray(guided_edge(photo, np.asarray(mask) > 0, 4))
    assert alpha[130, 200] == 0 and alpha[150, 100] == 255
    assert np.all(alpha[:20] == 0) and np.all(alpha[:, :20] == 0)


def test_precise_object_hit_and_hover_use_bitmap_but_catalog_stays_portable():
    photo, mask, hint = ring()
    from iphoto.segmentation.classical import bitmap_mask

    catalog = {
        "summary": "rough",
        "objects": [{"id": "ring", "name": "环", "category": "物体", "mask": hint}],
    }
    hint["ops"] = [
        {
            "kind": "polygon",
            "mode": "add",
            "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        }
    ]
    scene = SceneIndex()
    scene.set(catalog)
    scene.set_precise("ring", bitmap_mask(mask, "ring"), {"predicted_iou": 0.95})
    assert scene.hit(0.5, 130 / 300) == "" and scene.hit(0.25, 0.5) == "ring"
    assert scene.rows()[0]["pixelReady"] and scene.rows()[0]["maskPreview"].startswith(
        "data:image/png"
    )
    assert validate_catalog(scene.catalog) == catalog
    scene.set(catalog)
    assert not scene.precise and not scene.rows()[0]["pixelReady"]


def test_missing_model_never_silently_uses_coarse_polygon(monkeypatch):
    class Editor:
        notes = []

        def _notify(self, *args):
            self.notes.append(args)

        def _request(self, *args, **kwargs):
            raise AssertionError("must not request")

    e = Editor()
    monkeypatch.setattr(pixel_selections, "available", lambda: False)
    assert not pixel_selections.start(e, [], {})
    assert "未用粗多边形" in e.notes[-1][0]


def test_small_negative_hole_correction_protects_positive_and_large_regions():
    from iphoto.segmentation.corrections import correct_negatives

    photo, truth, _ = ring()
    filled = Image.new("L", photo.size)
    ImageDraw.Draw(filled).ellipse((50, 40, 350, 260), fill=255)
    hard = np.asarray(filled) > 0
    points = np.array([[100, 150], [200, 130]], np.float32)
    labels = np.array([1, 0])
    corrected, count = correct_negatives(photo, hard, points, labels)
    assert count > 0 and not corrected[130, 200] and corrected[150, 100]
    assert np.array_equal(corrected, np.asarray(truth) > 0)
    # Same-colored foreground, a conflicting positive, and a large subtraction
    # all preserve the neural support instead of forcing an arbitrary cut.
    uniform = Image.new("RGB", photo.size, (120, 120, 120))
    corrected, count = correct_negatives(uniform, hard, points, labels)
    assert count == 0 and np.array_equal(corrected, hard)
    corrected, count = correct_negatives(
        photo, hard, np.array([[200, 130], [200, 130]]), labels
    )
    assert count == 0 and np.array_equal(corrected, hard)


def test_satisfying_candidate_wins_over_higher_score_violating_prompt():
    logits = np.full((2, 40, 40), -5.0)
    logits[0, 2:15, 2:15] = 5
    logits[1, 15:30, 15:30] = 5
    hard, q = choose_candidate(
        logits, np.array([0.99, 0.65]), np.array([[20, 20]]), np.array([1])
    )
    assert q["candidate"] == 1 and hard[20, 20] and q["warnings"]


def test_regions_use_same_strict_box_and_anchor_contract():
    from iphoto.ai_tasks import parse_regions
    from iphoto.engine import Recipe

    data = {
        "status": "planned",
        "summary": "局部调整",
        "regions": [
            {
                "name": "物体",
                "reason": "提亮",
                "box": [100, 100, 900, 900],
                "point": [300, 400],
                "recipe": Recipe().to_dict(),
            }
        ],
    }
    region = parse_regions(response(data))["regions"][0]
    assert region["anchor"] == pytest.approx([300 / 999, 400 / 999])
    assert "bitmap" not in region["mask"]


def test_edge_touching_hint_anchor_is_inside_instead_of_image_corner():
    hint = Image.new("L", (400, 300))
    ImageDraw.Draw(hint).rectangle((0, 0, 399, 120), fill=255)
    coords, labels = from_hint(hint)
    assert labels[0] == 1 and coords[0, 0] > 20 and coords[0, 1] > 20


def test_positive_correction_can_extend_the_prior_box():
    hint = Image.new("L", (400, 300))
    ImageDraw.Draw(hint).rectangle((100, 100, 200, 200), fill=255)
    coords, labels = from_hint(hint, [[.9, .5, 1], [.02, .5, 0]])
    assert labels[-2:].tolist() == [2, 3]
    assert coords[-1, 0] >= .9 * 399
    assert coords[-2, 0] > .02 * 399  # a negative point doesn't expand it
