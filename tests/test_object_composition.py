"""Source-pixel combination semantics, actual worker delivery and cancellation."""

from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageChops
import pytest
from PySide6.QtCore import QByteArray, QPointF, Qt
from PySide6.QtTest import QTest

from iphoto import masks
from iphoto.controllers import objects, pixel_selections, worker_bridge
from iphoto.document import empty_mask, raster_mask
from iphoto.scene import combine_masks
from iphoto.segmentation.object_composition import compose
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui  # noqa: F401
from test_v14 import catalog


def bitmap(values):
    return {**empty_mask(), "bitmap": masks.encode_bitmap(Image.fromarray(values, "L"),
                                                          sampling="alpha", preserve_resolution=True)}


def fixtures():
    first = np.zeros((43, 59), dtype=np.uint8)
    first[4:34, 5:27] = 210
    first[12:18, 10:17] = 0
    first[4:34, 25:32] = 32
    second = np.zeros_like(first)
    second[8:38, 20:49] = 145
    removed = np.zeros_like(first)
    removed[14:22, 22:29] = 96
    return {"a": bitmap(first), "b": bitmap(second), "x": bitmap(removed)}


@pytest.mark.parametrize("mode", ["replace", "add", "subtract", "intersect"])
@pytest.mark.parametrize("exclude", [[], ["x"]])
def test_composition_keeps_exact_source_alpha_holes_and_zero_support(mode, exclude):
    cached = fixtures()
    definition = {"size": [217, 331], "ids": ["a", "b"], "exclude": exclude,
                  "mode": mode, "base": cached["b"],
                  "cached": {lid: mask for lid, mask in cached.items() if lid != "b" and lid in ["a"]+exclude}}
    before = deepcopy(definition)
    actual = compose(definition, [{"id": "b", "mask": cached["b"]}])
    expected = combine_masks([cached["a"], cached["b"]], (217, 331))
    if exclude:
        expected = combine_masks([cached["x"]], (217, 331), expected, "subtract")
    if mode != "replace":
        expected = combine_masks([expected], (217, 331), cached["b"], mode)
    assert raster_mask(actual, (217, 331)).tobytes() == raster_mask(expected, (217, 331)).tobytes()
    assert actual["bitmap"]["width"] == 217 and actual["bitmap"]["height"] == 331
    assert definition == before


@pytest.mark.parametrize("size", [(13, 11), (127, 201), (59, 43), (1, 1), (73, 9)])
def test_small_alpha_sampling_is_identical_to_previous_full_support_order(size):
    values = np.random.default_rng(14).integers(0, 256, (43, 59), dtype=np.uint8)
    values[6:27, 8:29] = 0
    image = Image.fromarray(values, "L")
    asset = masks.encode_bitmap(image, sampling="alpha", preserve_resolution=True)
    support = image.point([0]+[255]*255).resize(size, Image.Resampling.NEAREST)
    expected = image.resize(size, Image.Resampling.BILINEAR)
    expected.paste(0, mask=ImageChops.invert(support))
    assert masks.decode_bitmap(asset, size).tobytes() == expected.tobytes()


@pytest.mark.parametrize("mutation", ["missing", "coarse", "duplicate", "oversize", "base", "mode"])
def test_invalid_combination_is_rejected_before_raster_work(mutation, monkeypatch):
    cached = fixtures()
    definition = {"size": [217, 331], "ids": ["a", "b"], "exclude": [],
                  "mode": "replace", "base": None, "cached": {"a": cached["a"], "b": cached["b"]}}
    items = []
    if mutation == "missing":
        del definition["cached"]["b"]
    elif mutation == "coarse":
        definition["cached"]["b"] = empty_mask()
    elif mutation == "duplicate":
        items = [{"id": "a", "mask": cached["a"]}]
    elif mutation == "oversize":
        definition["size"] = [32768, 32768]
    elif mutation == "base":
        definition["mode"] = "add"
    else:
        definition["mode"] = "unknown"
    monkeypatch.setattr("iphoto.segmentation.object_composition.combine_masks",
                        lambda *_args, **_kwargs: pytest.fail("Invalid results must not allocate a union"))
    with pytest.raises(ValueError):
        compose(definition, items)


def test_foreground_validation_retains_only_bounded_decode_and_rejects_bad_png(monkeypatch):
    masks.clear_decode_cache()
    values = np.zeros((43, 59), dtype=np.uint8)
    values[10:30, 15:45] = 128
    asset = bitmap(values)["bitmap"]
    masks._VALIDATED.clear()
    monkeypatch.setattr(masks, "MAX_DECODE_CACHE_BYTES", 6000)
    calls = []
    original = masks._decode_uncached

    def decode(*args):
        calls.append(args[1:])
        return original(*args)

    monkeypatch.setattr(masks, "_decode_uncached", decode)
    try:
        masks.validate_bitmap(asset, cache_decoded=True)
        masks.decode_bitmap(asset, (13, 11))
        assert calls == [(59, 43)]
        assert 0 < masks._DECODE_CACHE_BYTES <= 6000
        with pytest.raises(ValueError):
            masks.validate_bitmap({**asset, "png": "invalid"}, cache_decoded=True)
    finally:
        masks.clear_decode_cache()


def prepare(editor):
    editor._scene.set(catalog())
    cached = fixtures()
    for lid, mask in zip(("object-1", "object-2"), (cached["a"], cached["b"])):
        editor._scene.set_precise(lid, mask, {})
        editor.checkSceneObject(lid, True)
    return [cached["a"], cached["b"]]


def click(window, item):
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


@pytest.mark.parametrize("size", [(1440, 930), (1080, 700)])
def test_cached_combination_uses_real_worker_one_transaction_save_undo_and_redo(ui, size, monkeypatch):  # noqa: F811
    editor, window, find, warnings, tmp_path = ui
    window.resize(*size)
    originals = prepare(editor)
    QTest.qWait(100)
    before, cursor, generation = deepcopy(editor._layers), editor._cursor, editor._generation
    events = []
    editor.selection.draftBegan.connect(events.append)
    # Any fallback to a GUI combination fails, including cached object unions.
    monkeypatch.setattr(objects, "combine_masks", lambda *_args, **_kwargs: pytest.fail("GUI union"))
    monkeypatch.setattr(pixel_selections, "available", lambda: False)
    click(window, find("adjustCheckedObjectsButton"))
    assert editor.busy and editor.selection.taskKind == "pixel"
    assert find("aiRequestProgress").property("visible")
    assert "正在组合 2 个对象" in find("aiRequestProgressText").property("text")
    assert editor._layers == before and editor._cursor == cursor
    wait_for(lambda: settled(editor) and window.property("previewReady"))
    assert editor._cursor == cursor+1 and editor._generation == generation+1
    assert len(editor._layers) == len(before)+1 and editor._layers[:-1] == before
    assert not events and not editor.hasSelectionDraft
    assert not editor._warm_ready_sha, "Mask composition must not claim an embedding was prepared"
    expected = combine_masks(originals, (300, 200))
    actual = editor._layer()["mask"]
    assert raster_mask(actual, (300, 200)).tobytes() == raster_mask(expected, (300, 200)).tobytes()
    applied = deepcopy(editor._layers)
    editor.saveProject(str(tmp_path / "combined.iphoto"))
    wait_for(lambda: not editor.savingProject)
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before
    editor.redo()
    wait_for(lambda: settled(editor))
    assert editor._layers == applied
    editor.openProject(str(tmp_path / "combined.iphoto"))
    wait_for(lambda: settled(editor) and not editor.busy)
    assert editor._layers == applied and not warnings


def test_combination_cancellation_and_immediate_photo_switch_preserve_document(ui):  # noqa: F811
    editor, window, find, warnings, tmp_path = ui
    prepare(editor)
    QTest.qWait(100)
    before = deepcopy((editor._layers, editor._cursor, editor._generation, editor._candidate))
    click(window, find("adjustCheckedObjectsButton"))
    assert editor.busy
    click(window, find("cancelAiRequest"))
    assert not editor.busy
    assert before == (editor._layers, editor._cursor, editor._generation, editor._candidate)
    assert editor.checkedObjectCount == 2
    other = tmp_path / "other.png"
    Image.new("RGB", (380, 250), (30, 40, 180)).save(other)
    editor.openImage(str(other))
    wait_for(lambda: editor.imageName == other.name and settled(editor) and window.property("previewReady"))
    QTest.qWait(150)
    assert len(editor.layers) == 1 and not editor.hasSelectionDraft and not editor.sceneObjects
    assert editor.activeLayerName == "全图调整" and not warnings


@pytest.mark.parametrize("expired", ["photo", "catalog", "generation"])
def test_stale_composition_response_cannot_apply_after_identity_changes(ui, expired):  # noqa: F811
    editor, _, _, _, _ = ui
    prepare(editor)
    original_request, requests = editor._request, []
    editor._request = lambda op, **data: requests.append((op, data))
    try:
        editor.adjustCheckedObjects()
    finally:
        editor._request = original_request
    data = requests[0][1]
    result = {"items": [], "mask": compose(data["composition"], [])}
    response = {"id": 991, "generation": editor._generation, "ok": True, "result": result}
    if expired == "photo":
        data["context"]["source_sha"] = "outdated-photo"
    elif expired == "catalog":
        data["context"]["scene_revision"] -= 1
    else:
        response["generation"] -= 1
    before = deepcopy((editor._layers, editor._cursor, editor._candidate))
    editor._pixel_active = {"id": 991, "op": "segment", **data}
    actual_process = editor._pixel_process
    editor._pixel_process = SimpleNamespace(readAllStandardOutput=lambda: QByteArray((json.dumps(response)+"\n").encode()))
    try:
        worker_bridge._pixel_read(editor)
    finally:
        editor._pixel_process = actual_process
    assert before == (editor._layers, editor._cursor, editor._candidate)
    if expired != "generation":
        assert editor.conversation[-1]["state"] == "failed"
    else:
        assert not editor.hasSelectionDraft


def test_composed_result_with_wrong_dimensions_keeps_existing_range(ui):  # noqa: F811
    editor, _, _, _, _ = ui
    prepare(editor)
    editor.draftAction("all")
    wait_for(lambda: settled(editor))
    context = {"purpose": "objects", "ids": ["object-1", "object-2"], "exclude": [],
               "mode": "replace", "auto_apply": True, "composed": True}
    before = deepcopy((editor._layers, editor._candidate, editor._draft_history, editor._cursor))
    with pytest.raises(ValueError, match="原图尺寸"):
        pixel_selections.complete(editor, {"items": [], "mask": fixtures()["a"]}, context)
    assert before == (editor._layers, editor._candidate, editor._draft_history, editor._cursor)
