"""Bound range correction, precision input and visible brush feedback."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtQml import QQmlProperty
from PySide6.QtTest import QTest

from iphoto.document import raster_mask
from test_ai import wait_for
from test_canvas_ui import canvas as shared_canvas
from test_editor import settled

canvas = shared_canvas


def ready(ui):
    return settled(ui.e) and ui.w.property("previewReady")


def pick_adjustment(ui):
    ui.e.setParameter("exposure", .2)
    ui.e.finishGesture()
    wait_for(lambda: ready(ui))
    ui.click("layerSelect_" + ui.e.activeLayerId)
    ui.e.selection.parameterFocusRequested.emit(ui.e.activeLayerId, "skin_smoothing")
    QTest.qWait(200)


@pytest.mark.parametrize("mode", ["add", "subtract"])
@pytest.mark.parametrize("save", [False, True])
def test_fixed_correction_buttons_preserve_layer_and_return_to_previous_control(canvas, mode, save):
    ui = canvas
    pick_adjustment(ui)
    before = deepcopy(ui.e._layers)
    cursor = ui.e._cursor
    flick = ui.find("propertyScroll").property("contentItem")
    position = flick.property("contentY")
    button_name = "addLayerMaskButton" if mode == "add" else "eraseLayerMaskButton"
    button = ui.find(button_name)
    point = ui.point(button_name)
    assert button.isVisible() and 0 < point.y() < ui.w.height()
    ui.w.setProperty("compare", True)
    ui.e.selection.setMaskView("grayscale")
    wait_for(lambda: ready(ui))
    ui.click(button_name)
    wait_for(lambda: ui.e.hasSelectionDraft and ready(ui))
    assert ui.e.selection.editingLayerMask and ui.e._selection_target_id == before[-1]["id"]
    assert ui.e.selection.tool == "brush" and ui.e.selection.mode == mode
    assert ui.e.maskView == "overlay" and not ui.w.property("compare")
    assert ui.e._layers == before
    ui.e.selection.setBrushDiameter(32)
    ui.click("photoCanvas", .5, .5)
    wait_for(lambda: ready(ui))
    candidate = deepcopy(ui.e._candidate)
    assert candidate["ops"][-1]["kind"] == "brush" and candidate["ops"][-1]["mode"] == mode
    assert ui.e._layers == before and ui.e._cursor == cursor
    ui.click("selectionToLayerButton" if save else "discardSelectionButton")
    wait_for(lambda: not ui.e.hasSelectionDraft and ready(ui))
    wait_for(lambda: abs(flick.property("contentY") - position) < 1)
    assert ui.e.selection.pickedLayerId == before[-1]["id"] and ui.e.selection.tool == "inspect"
    if save:
        assert len(ui.e._layers) == len(before) and ui.e._layer()["recipe"] == before[-1]["recipe"]
        assert ui.e._layer()["mask"] == candidate and ui.e._cursor == cursor + 1
        if mode == "subtract":
            assert raster_mask(candidate, (2400, 1600)).getpixel((1200, 800)) == 0
        ui.e.undo()
        wait_for(lambda: ready(ui))
    assert ui.e._layers == before


def test_brush_pixel_input_brackets_and_heal_memory_do_not_edit_document(canvas):
    ui = canvas
    ui.click("tool_brush")
    wait_for(lambda: ready(ui))
    before = deepcopy((ui.e._layers, ui.e._candidate, ui.e._generation, ui.e._cursor))
    field = ui.find("brushDiameterInput")
    assert field.isVisible() and field.property("editable")
    assert field.property("from") == 2
    field.property("contentItem").forceActiveFocus()
    ui.key(Qt.Key_A, Qt.ControlModifier)
    ui.type("20")
    ui.key(Qt.Key_Return)
    assert ui.e.selection.brushDiameter == 20
    ui.find("photoCanvas").forceActiveFocus()
    ui.key(Qt.Key_BracketRight)
    assert ui.e.selection.brushDiameter == 22
    ui.key(Qt.Key_BracketLeft)
    assert ui.e.selection.brushDiameter == 20
    assert deepcopy((ui.e._layers, ui.e._candidate, ui.e._generation, ui.e._cursor)) == before
    ui.e.selection.discard()
    wait_for(lambda: ready(ui))
    ui.click("tool_heal")
    healing_size = ui.e.selection.brushDiameter
    ui.click("tool_brush")
    assert ui.e.selection.brushDiameter == 20
    ui.e.selection.discard()
    ui.click("tool_heal")
    assert ui.e.selection.brushDiameter == healing_size


@pytest.mark.parametrize("tool", ["brush", "heal"])
def test_brush_footprint_follows_zoom_and_hides_for_pan_compare_and_dialog(canvas, tool):
    ui = canvas
    ui.click("tool_" + tool)
    wait_for(lambda: ready(ui))
    ui.e.selection.setBrushDiameter(40)
    photo = ui.find("photoCanvas")
    footprint = ui.find("brushFootprint")
    photo.forceActiveFocus()
    for zoom in (.5, 1):
        ui.n.setZoom(zoom)
        ui.n.centerOn(.5, .5)
        QTest.mouseMove(ui.w, ui.point("photoCanvas"))
        QTest.qWait(80)
        assert footprint.isVisible()
        assert footprint.width() == pytest.approx(40 * photo.width() / 2400)
    if tool == "brush":
        ui.e.selection.setMode("add")
        assert QQmlProperty(footprint, "border.color").read().name() == "#d7fff1"
        QTest.keyPress(ui.w, Qt.Key_Alt)
        QTest.qWait(40)
        assert QQmlProperty(footprint, "border.color").read().name() == "#ffad8d"
        QTest.keyRelease(ui.w, Qt.Key_Alt)
        ui.e.selection.setMode("subtract")
        QTest.keyPress(ui.w, Qt.Key_Shift)
        QTest.qWait(40)
        assert QQmlProperty(footprint, "border.color").read().name() == "#d7fff1"
        QTest.keyRelease(ui.w, Qt.Key_Shift)
    QTest.keyPress(ui.w, Qt.Key_Space)
    QTest.qWait(40)
    assert not footprint.isVisible()
    QTest.keyRelease(ui.w, Qt.Key_Space)
    ui.w.setProperty("compare", True)
    assert not footprint.isVisible()
    ui.w.setProperty("compare", False)
    ui.find("aiSettingsDialog").open()
    QTest.qWait(80)
    assert not footprint.isVisible()
    ui.find("aiSettingsDialog").close()


@pytest.mark.parametrize("condition", ["invalid", "new_draft", "bound_draft", "busy", "regions"])
def test_direct_correction_refuses_conflicting_state_without_rebinding(canvas, condition):
    ui = canvas
    if condition == "new_draft":
        ui.e.beginSelection("empty")
    elif condition == "bound_draft":
        ui.e.selection.reviewMask()
    elif condition == "busy":
        ui.e._active = {"op": "selection"}
    elif condition == "regions":
        ui.e._region_candidate = {"layers": []}
    before = deepcopy((ui.e._layers, ui.e._candidate, ui.e._selection_target_id, ui.e._generation))
    try:
        assert not ui.e.selection.correctMask("replace" if condition == "invalid" else "subtract")
        assert deepcopy((ui.e._layers, ui.e._candidate, ui.e._selection_target_id, ui.e._generation)) == before
    finally:
        if condition == "busy":
            ui.e._active = None
        elif condition == "regions":
            ui.e._region_candidate = None
