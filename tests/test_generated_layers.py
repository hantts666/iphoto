"""Automatic local results are atomic, visible and independent of draft UI."""

from copy import deepcopy
import json

import numpy as np
from PIL import Image, ImageDraw
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.controllers import layers, objects, pixel_selections, worker_bridge
from iphoto.document import MAX_LAYERS, empty_mask, new_layer, raster_mask, read_project
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui as shared_ui
from test_v14 import catalog

ui = shared_ui


def mask():
    alpha = Image.new("L", (64, 96))
    drawing = ImageDraw.Draw(alpha)
    drawing.rectangle((10, 10, 48, 85), fill=255)
    drawing.rectangle((10, 10, 15, 85), fill=96)
    drawing.ellipse((25, 30, 35, 45), fill=0)
    return {**empty_mask(), "label": "透明度与孔洞",
            "bitmap": encode_bitmap(alpha, sampling="alpha", preserve_resolution=True)}


def cached(editor):
    editor._scene.set(catalog())
    result = mask()
    editor._scene.set_precise("object-1", result, {})
    editor.checkSceneObject("object-1", True)
    return result


def click(window, item):
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(30)


def regions():
    definitions = [{"name": "面部", "reason": "只提亮此处", "recipe": Recipe(exposure=.2).to_dict()},
                   {"name": "手臂", "reason": "保留纹理", "recipe": Recipe(skin_smoothing=20).to_dict()}]
    items = [{"id": str(i), "mask": mask(), "quality": {}} for i in range(2)]
    context = {"purpose": "regions", "regions": definitions, "summary": "两处局部处理",
               "auto_apply": True, "origin": {"mode": "auto", "model": "protocol-fixture"}}
    return {"items": items}, context


def test_single_object_retains_native_alpha_and_does_not_combine_at_source_size(ui, monkeypatch):
    editor, _, _, _, _ = ui
    original = cached(editor)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("A single cached object must not rasterize or encode the full source")

    monkeypatch.setattr(objects, "combine_masks", unexpected)
    result = editor._objects_mask(["object-1"])
    assert result["bitmap"] == original["bitmap"]
    assert result["ops"] == original["ops"]
    assert np.array_equal(np.array(raster_mask(result, (4016, 6016))),
                          np.array(raster_mask(original, (4016, 6016))))
    result["bitmap"]["width"] = 1
    assert editor._scene.precise["object-1"]["mask"]["bitmap"]["width"] == 64


@pytest.mark.parametrize("size", [(1440, 930), (1080, 700)])
def test_one_click_applies_once_without_draft_and_exits_comparison(ui, size, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    window.resize(*size)
    original = cached(editor)
    QTest.qWait(100)
    before = deepcopy(editor._layers)
    cursor, generation = editor._cursor, editor._generation
    draft_events, render_states, visible_drafts = [], [], []
    editor.selection.draftBegan.connect(draft_events.append)
    editor.changed.connect(lambda: visible_drafts.append(editor.hasSelectionDraft or editor.hasRegionDraft))
    original_render = editor._schedule_render

    def render():
        render_states.append((editor._generation, editor.hasSelectionDraft, editor.hasRegionDraft))
        return original_render()

    monkeypatch.setattr(editor, "_schedule_render", render)
    click(window, find("compareButton"))
    assert window.property("compare")
    click(window, find("adjustCheckedObjectsButton"))
    wait_for(lambda: settled(editor))
    assert not window.property("compare")
    assert not draft_events and not any(visible_drafts)
    assert render_states == [(generation+1, False, False)]
    assert editor._cursor == cursor+1 and editor._layers[:-1] == before
    assert editor.activeLayerName == "左侧方块"
    assert editor._layer()["mask"]["bitmap"] == original["bitmap"]
    assert editor.selection.pickedLayerId == editor.activeLayerId
    assert editor.checkedObjectCount == 0
    assert editor.conversation[-1]["role"] == "assistant"
    assert editor.conversation[-1]["state"] == "applied"
    editor.saveProject(str(tmp_path / "direct.iphoto"))
    wait_for(lambda: not editor.savingProject)
    assert read_project(tmp_path / "direct.iphoto")["layers"] == editor._layers
    applied = deepcopy(editor._layers)
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before
    editor.redo()
    wait_for(lambda: settled(editor))
    assert editor._layers == applied
    assert not warnings


@pytest.mark.parametrize("blocking", ["hidden", "zero_opacity", "restricted_mask"])
def test_object_adjustment_is_visible_outside_existing_group(ui, blocking):
    editor, _, _, warnings, _ = ui
    group = new_layer("已有组", True, kind="group")
    if blocking == "hidden":
        group["visible"] = False
    elif blocking == "zero_opacity":
        group["opacity"] = 0
    else:
        group["mask"] = {**empty_mask(), "ops": [{"kind": "rect", "mode": "add", "points": [[0, 0], [.2, .2]]}]}
    editor._layers[0]["parent_id"] = group["id"]
    editor._layers.append(group)
    editor._selected = group["id"]
    editor._load_layer()
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    cached(editor)
    before = deepcopy(editor._layers)
    editor.adjustCheckedObjects()
    wait_for(lambda: settled(editor))
    assert editor._layers[:-1] == before
    assert editor._layer()["parent_id"] == "" and editor.activeDisplay["enabled"]
    assert editor._layer()["opacity"] == 1
    assert not warnings


def test_automatic_regions_have_one_history_generation_render_and_no_preview(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    editor._scene.set(catalog())
    editor.checkSceneObject("object-1", True)
    result, context = regions()
    before = deepcopy(editor._layers)
    cursor, generation = editor._cursor, editor._generation
    transitions, render_states = [], []
    editor.selection.draftBegan.connect(transitions.append)
    original_render = editor._schedule_render

    def render():
        render_states.append(editor.hasRegionDraft)
        return original_render()

    monkeypatch.setattr(editor, "_schedule_render", render)
    click(window, find("compareButton"))
    pixel_selections.complete(editor, result, context)
    wait_for(lambda: settled(editor))
    assert not transitions and not editor.hasRegionDraft and not window.property("compare")
    assert editor._generation == generation+1 and editor._cursor == cursor+1
    assert editor._scene.selected == {"object-1"}
    assert render_states == [False]
    assert editor._layers[:len(before)] == before
    assert [layer["name"] for layer in editor._layers[-2:]] == ["面部", "手臂"]
    assert editor.conversation[-1]["state"] == "applied" and "手臂：保留纹理" in editor.conversation[-1]["text"]
    assert editor.conversation[-1]["model"] == "protocol-fixture"
    editor.saveProject(str(tmp_path / "two.iphoto"))
    wait_for(lambda: not editor.savingProject)
    assert read_project(tmp_path / "two.iphoto")["region_draft"] is None
    applied = deepcopy(editor._layers)
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before
    editor.redo()
    wait_for(lambda: settled(editor))
    assert editor._layers == applied
    assert not warnings


@pytest.mark.parametrize("failure", ["capacity", "invalid_second", "empty_second", "missing_second", "coarse_second"])
def test_failed_regions_leave_no_partial_layers_or_draft(ui, failure):
    editor, _, _, _, _ = ui
    result, context = regions()
    if failure == "capacity":
        editor._layers.extend(new_layer("已有层", True) for _ in range(MAX_LAYERS-len(editor._layers)))
        editor._commit()
    elif failure == "invalid_second":
        result["items"][1]["mask"]["bitmap"]["width"] = 100
    elif failure == "empty_second":
        result["items"][1]["mask"]["bitmap"] = encode_bitmap(Image.new("L", (64, 96)))
    elif failure == "missing_second":
        result["items"].pop()
    else:
        result["items"][1]["mask"] = empty_mask(True)
    before, cursor, generation = deepcopy(editor._layers), editor._cursor, editor._generation
    with pytest.raises((ValueError, KeyError)):
        pixel_selections.complete(editor, result, context)
    assert editor._layers == before and editor._cursor == cursor and editor._generation == generation
    assert not editor.hasRegionDraft and not editor.hasSelectionDraft


def test_manual_regions_still_preview_before_confirmation(ui):
    editor, window, find, warnings, _ = ui
    result, context = regions()
    context["auto_apply"] = False
    before = deepcopy(editor._layers)
    pixel_selections.complete(editor, result, context)
    wait_for(lambda: settled(editor))
    assert editor.hasRegionDraft and editor._layers == before
    assert editor.conversation[-1]["state"] == "region_draft"
    wait_for(lambda: find("selectionToLayerButton").property("enabled"))
    click(window, find("selectionToLayerButton"))
    wait_for(lambda: settled(editor))
    assert not editor.hasRegionDraft and len(editor._layers) == len(before)+2
    assert not warnings


def test_new_layer_undo_keeps_preceding_unfinished_parameter_gesture(ui):
    editor, _, _, _, _ = ui
    cached(editor)
    editor.setParameter("exposure", .2)
    wait_for(lambda: settled(editor))
    editor.adjustCheckedObjects()
    wait_for(lambda: settled(editor))
    editor.undo()
    wait_for(lambda: settled(editor))
    assert len(editor._layers) == 1 and editor._recipe["exposure"] == .2
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._recipe["exposure"] == 0


def test_automatic_objects_consume_current_draft_only_on_success(ui):
    editor, _, _, _, _ = ui
    cached(editor)
    editor.beginSelection("current")
    editor._message("assistant", "原范围待检查", state="draft")
    wait_for(lambda: settled(editor))
    editor.adjustCheckedObjects()
    wait_for(lambda: settled(editor))
    assert not editor.hasSelectionDraft and not editor._draft_history and not editor._selection_target_id
    assert editor.conversation[-2]["state"] == "discarded"
    assert editor.conversation[-1]["state"] == "applied"


def test_incomplete_automatic_worker_result_is_recorded_and_does_not_use_coarse_box(ui):
    editor, _, _, _, _ = ui
    editor._scene.set(catalog())
    editor.beginSelection("current")
    wait_for(lambda: settled(editor))
    before, candidate = deepcopy(editor._layers), deepcopy(editor._candidate)
    cursor, generation = editor._cursor, editor._generation
    context = {"purpose": "objects", "ids": ["object-1", "object-2"], "exclude": [],
               "mode": "replace", "base": candidate, "auto_apply": True,
               "origin": {"mode": "selection", "model": "protocol-fixture"}}
    response = {"id": 9, "ok": True, "generation": generation,
                "result": {"items": [{"id": "object-1", "mask": mask(), "quality": {}}]}}

    class Output:
        def readAllStandardOutput(self):
            return (json.dumps(response)+"\n").encode()

    real_process = editor._pixel_process
    editor._pixel_process = Output()
    editor._pixel_active = {"id": 9, "context": context}
    try:
        worker_bridge._pixel_read(editor)
    finally:
        editor._pixel_process = real_process
    assert editor._layers == before and editor._candidate == candidate
    assert editor._cursor == cursor and editor._generation == generation
    assert editor.conversation[-1]["state"] == "failed"
    assert "全部目标" in editor.conversation[-1]["text"]
    assert "未应用" in editor.status


def test_direct_transaction_rejects_hidden_or_grouped_output_before_mutation(ui):
    editor, _, _, _, _ = ui
    proposed = new_layer("无效层", True)
    proposed["visible"] = False
    before, cursor, generation = deepcopy(editor._layers), editor._cursor, editor._generation
    with pytest.raises(ValueError):
        layers.addLocalLayers(editor, [proposed])
    assert editor._layers == before and editor._cursor == cursor and editor._generation == generation
