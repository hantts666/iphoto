"""Manual edits explain inactive display controls and restore them in one step."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.document import new_layer
from test_ai import wait_for
from test_ai_hidden_effects import preview
from test_ai_layer_edits import setup_layers
from test_editor import settled
from test_selection_ui_modes import ui, click


@pytest.mark.parametrize("reason", ["hidden", "zero", "parent_hidden", "parent_zero"])
def test_manual_inactive_notice_restores_pixels_and_one_undo(ui, reason):
    editor, window, find, warnings = ui
    face, _, whole = setup_layers(editor)
    target = face
    if reason.startswith("parent"):
        target = new_layer("人像组", True, kind="group")
        face["parent_id"] = target["id"]
        editor._layers.append(target)
    target["visible"] = "hidden" not in reason
    target["opacity"] = 0 if "zero" in reason else .5
    editor._commit()
    editor._change()
    editor.selection.pickLayer(face["id"])
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    original_pixels = preview(editor)
    slider = find("parameter_exposure")
    point = slider.mapToScene(QPointF(slider.width() * .65, slider.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: settled(editor))
    assert editor.parameters["exposure"] != .15
    assert preview(editor) == original_pixels
    assert find("inactiveLayerNotice").isVisible()
    assert "效果未显示" in find("editingContextLabel").property("text")
    assert ("人像组" in editor.activeDisplay["reason"]) == reason.startswith("parent")
    assert ("0%" in editor.activeDisplay["reason"]) == ("zero" in reason)
    assert "exposure" in editor.lockedFields
    before = deepcopy(editor._layers)
    cursor = editor._cursor
    click(window, find("restoreLayerDisplayButton"))
    wait_for(lambda: settled(editor))
    assert editor._cursor == cursor + 1
    assert editor.activeDisplay["enabled"] and not find("inactiveLayerNotice").isVisible()
    assert preview(editor) != original_pixels
    assert face["recipe"] == before[1]["recipe"] and face["locked"] == before[1]["locked"]
    assert face["mask"] == before[1]["mask"]
    assert target["visible"] and target["opacity"] == (1 if "zero" in reason else .5)
    shown = deepcopy(editor._layers)
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before and preview(editor) == original_pixels
    assert find("inactiveLayerNotice").isVisible()
    editor.redo()
    wait_for(lambda: settled(editor))
    assert editor._layers == shown and not find("inactiveLayerNotice").isVisible()
    editor.selection.pickLayer(whole["id"])
    QTest.qWait(50)
    assert editor.activeDisplay["enabled"] and not find("inactiveLayerNotice").isVisible()
    assert not warnings, warnings


def test_restore_nested_blockers_preserves_fractional_opacity_and_sibling_controls(ui):
    editor, _, _, warnings = ui
    face, arm, whole = setup_layers(editor)
    outer = new_layer("外组", True, kind="group")
    inner = new_layer("内组", True, outer["id"], "group")
    outer["visible"], outer["opacity"] = False, .6
    inner["visible"], inner["opacity"] = False, 0
    face["visible"], face["opacity"], face["parent_id"] = False, .4, inner["id"]
    arm["visible"], arm["parent_id"] = False, outer["id"]
    editor._layers.extend([outer, inner])
    editor._commit()
    editor._change()
    editor.selection.pickLayer(face["id"])
    wait_for(lambda: settled(editor))
    before = deepcopy(editor._layers)
    assert editor.restoreLayerDisplay()
    wait_for(lambda: settled(editor))
    assert editor.activeDisplay["enabled"] and editor.activeDisplay["opacity"] == pytest.approx(.24)
    for old, layer in zip(before, editor._layers):
        if old["id"] in (outer["id"], inner["id"], face["id"]):
            assert layer == {**old, "visible": True, "opacity": old["opacity"] or 1}
        else:
            assert layer == old
    assert not arm["visible"] and whole["visible"]
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before
    assert not warnings, warnings


def test_restore_keeps_preceding_unfinished_parameter_gesture(ui):
    editor, _, _, warnings = ui
    face, _, _ = setup_layers(editor)
    editor.selection.pickLayer(face["id"])
    editor.toggleLayer(face["id"])
    wait_for(lambda: settled(editor))
    editor.setParameter("exposure", .75)
    wait_for(lambda: settled(editor))
    assert editor.restoreLayerDisplay()
    wait_for(lambda: settled(editor))
    editor.undo()
    wait_for(lambda: settled(editor))
    assert not editor._layer()["visible"] and editor.parameters["exposure"] == .75
    editor.undo()
    wait_for(lambda: settled(editor))
    assert not editor._layer()["visible"] and editor.parameters["exposure"] == .15
    assert not warnings, warnings


@pytest.mark.parametrize("gate", ["visible", "busy", "selection", "regions"])
def test_restore_noop_or_unavailable_does_not_edit_history(ui, gate):
    editor, _, _, warnings = ui
    if gate != "visible":
        editor.toggleLayer(editor.activeLayerId)
        wait_for(lambda: settled(editor))
    before, cursor = deepcopy(editor._layers), editor._cursor
    if gate == "busy":
        editor._queue.append({"op": "selection"})
    elif gate == "selection":
        editor._candidate = {"label": "草稿"}
    elif gate == "regions":
        editor._region_candidate = []
    try:
        assert not editor.restoreLayerDisplay()
        assert editor._layers == before and editor._cursor == cursor
    finally:
        editor._queue.clear()
        editor._candidate = editor._region_candidate = None
    assert not warnings, warnings


@pytest.mark.parametrize("size", [(1440, 930), (1080, 700)])
def test_notice_button_is_reachable_in_compact_inspector(ui, size):
    editor, window, find, warnings = ui
    window.resize(*size)
    editor.selection.pickLayer(editor.activeLayerId)
    editor.toggleLayer(editor.activeLayerId)
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    button = find("restoreLayerDisplayButton")
    point = button.mapToScene(QPointF(button.width()/2, button.height()/2))
    assert button.isVisible() and 0 < point.x() < size[0] and 0 < point.y() < size[1]
    click(window, button)
    wait_for(lambda: settled(editor))
    assert editor.activeDisplay["enabled"] and not find("inactiveLayerNotice").isVisible()
    assert not warnings, warnings
