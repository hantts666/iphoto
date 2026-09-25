"""Real matting regression fixtures and document/worker integration."""

from copy import deepcopy
import json
from types import SimpleNamespace
import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QUrl

from iphoto.document import empty_mask, raster_mask, render_layers, read_project
from iphoto.masks import encode_bitmap, decode_bitmap, validate_bitmap
from iphoto.matting.service import refine_alpha
from iphoto.matting.trimap import make_trimap
from iphoto.matting.solver import solve_alpha
from iphoto.segmentation.edges import guided_edge
from iphoto.workspace import Editor
from iphoto.controllers import worker_bridge
from test_ai import wait_for
from test_editor import settled


def soft_scene(width=480, height=320):
    y, x = np.mgrid[:height, :width]
    ridge = height * 0.48 + 22 * np.sin(x * 0.033) + 13 * np.sin(x * 0.13)
    alpha = np.clip((y - ridge) / 5 + 0.5, 0, 1)
    hole = np.clip((np.hypot(x - width * 0.48, y - height * 0.78) - 4) / 1.5, 0, 1)
    alpha *= hole
    foreground = np.zeros((height, width, 3), np.float64)
    foreground[:] = [0.025, 0.18, 0.065]
    foreground += (x / width)[..., None] * np.array([0.012, 0.02, 0.015])
    background = np.full_like(foreground, [0.72, 0.75, 0.79])
    background += (y / height)[..., None] * 0.025
    linear = alpha[..., None] * foreground + (1 - alpha[..., None]) * background
    encoded = np.where(
        linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055
    )
    image = Image.fromarray(np.rint(encoded * 255).astype(np.uint8))
    hard = Image.fromarray((alpha >= 0.5).astype(np.uint8) * 255)
    mask = empty_mask()
    mask["bitmap"] = encode_bitmap(hard)
    return image, alpha, mask


def test_partial_coverage_is_not_squared_by_feather():
    pixels = Image.new("L", (80, 60), 128)
    pixels.paste(0, (0, 0, 10, 60))
    mask = empty_mask()
    mask.update(bitmap=encode_bitmap(pixels), feather=0.02)
    result = np.asarray(raster_mask(mask, pixels.size))
    assert result[30, 40] == 128
    assert (result[:, :10] == 0).all()
    assert result.max() <= 128


def test_continuous_resampling_preserves_protected_zero_and_legacy_behavior():
    pixels = Image.fromarray(
        np.tile(np.array([0, 0, 32, 96, 160, 224, 255, 255], np.uint8), (8, 1))
    )
    old = encode_bitmap(pixels)
    new = encode_bitmap(pixels, sampling="alpha")
    old_result = np.asarray(decode_bitmap(old, (64, 64)))
    result = np.asarray(decode_bitmap(validate_bitmap(new), (64, 64)))
    assert (result[old_result == 0] == 0).all()
    assert np.unique(result).size > np.unique(old_result).size
    assert np.array_equal(old_result, pixels.resize((64, 64), Image.Resampling.NEAREST))
    with pytest.raises(ValueError):
        validate_bitmap({**new, "sampling": "execute"})


def test_trimap_locks_known_regions_and_preserves_small_hole_seed():
    _, truth, mask = soft_scene()
    trimap = make_trimap(raster_mask(mask, (480, 320)), 8)
    assert trimap[30, 30] == 0 and trimap[-15, -15] == 1
    assert (trimap[245:255, 225:236] == 0).any()
    assert set(np.unique(trimap)) == {0, 0.5, 1}
    for radius in (0, 65, True, 8.5):
        with pytest.raises(ValueError):
            make_trimap(truth, radius)
    for value in (0, 255):
        with pytest.raises(ValueError):
            make_trimap(np.full((32, 32), value, np.uint8), 8)


def test_real_closed_form_recovers_partial_alpha_and_tile_seams():
    image, truth, mask = soft_scene()
    refined, quality = refine_alpha(image, mask, 8)
    result = np.asarray(raster_mask(refined, image.size)) / 255
    band = (truth > 0) & (truth < 1)
    old = (
        np.asarray(
            guided_edge(image, np.asarray(raster_mask(mask, image.size)) > 127, 8)
        )
        / 255
    )
    error = np.abs(result - truth)[band].mean()
    assert error < 0.04
    assert error < np.abs(old - truth)[band].mean() * 0.65
    trimap = make_trimap(raster_mask(mask, image.size), 8)
    assert np.all(result[trimap == 0] == 0) and np.all(result[trimap == 1] == 1)
    whole, _ = solve_alpha(image, trimap, tile_size=1024)
    assert np.abs(result - whole).max() < 0.025
    assert quality["partial_pixels"] > 100 and refined["feather"] == 0


@pytest.mark.parametrize("outcome", ["cancelled", "stale", "error"])
def test_matte_failure_cancel_and_stale_result_keep_old_selection(outcome):
    old = empty_mask(True)
    notes = []
    response = {
        "id": 4,
        "op": "matte",
        "generation": 8,
        "ok": outcome != "error",
        "error": "fixture failure",
        "result": {},
    }
    owner = SimpleNamespace(
        process=SimpleNamespace(
            readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()
        ),
        _buffer=b"",
        _active={"id": 4, "op": "matte", "cancelled": outcome == "cancelled"},
        _generation=9 if outcome == "stale" else 8,
        _candidate=old,
        _pending_project=None,
        changed=SimpleNamespace(emit=lambda: None),
        _pump=lambda: None,
        _status="working",
        _notify=lambda *a: notes.append(a),
        _message=lambda *a, **k: notes.append(a),
    )
    worker_bridge._read(owner)
    assert owner._candidate is old and "保留" in owner._status and notes


def test_real_worker_draft_preview_undo_save_and_export(qt_app, ai_store, tmp_path):
    image, truth, mask = soft_scene(240, 160)
    path = tmp_path / "soft.png"
    image.save(path)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor._layer()["mask"] = deepcopy(mask)
        editor.setParameter("exposure", -0.8)
        editor.finishGesture()
        wait_for(lambda: settled(editor))
        original = deepcopy(editor._layers)
        editor.beginSelection("current")
        wait_for(lambda: settled(editor))
        before = deepcopy(editor._candidate)
        editor.refineMatte(8)
        wait_for(lambda: settled(editor), seconds=150)
        assert editor._candidate["bitmap"]["sampling"] == "alpha"
        assert editor._layers == original
        refined = deepcopy(editor._candidate)
        editor.setMaskView("adjustment")
        wait_for(lambda: settled(editor))
        expected = deepcopy(original)
        expected[0]["mask"] = refined
        shown = Image.open(QUrl(editor.previewUrl).toLocalFile())
        assert np.array_equal(shown, render_layers(image, expected))
        assert editor._layers == original
        editor.undo()
        assert editor._candidate == before
        editor.redo()
        assert editor._candidate == refined
        wait_for(lambda: settled(editor))
        project = tmp_path / "matte.iphoto"
        editor.saveProject(str(project))
        assert read_project(project)["selection_draft"] == refined
        editor.acceptSelection()
        wait_for(lambda: settled(editor))
        editor.exportImage(str(tmp_path / "export.png"))
        wait_for(lambda: settled(editor))
        output = np.asarray(Image.open(tmp_path / "export.png"))
        alpha = np.asarray(raster_mask(refined, image.size))
        assert np.array_equal(output[alpha == 0], np.asarray(image)[alpha == 0])
    finally:
        editor.close()
