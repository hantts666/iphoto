"""A shortcut effect exposes its editable strength in the complete window."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def contained(item, viewport):
    point = item.mapToItem(viewport, QPointF())
    return (
        item.isVisible()
        and point.y() >= 0
        and point.y() + item.height() <= viewport.height()
    )


@pytest.mark.parametrize("chat", [False, True])
def test_preset_reveals_strength_without_losing_folds_history_or_manual_scroll(canvas, chat):  # noqa: F811
    ui = canvas
    ui.w.setProperty("chatOpen", chat)
    ui.click("addLayerButton")
    wait_for(lambda: settled(ui.e))
    scroll = ui.find("propertyScroll")
    flick = scroll.property("contentItem")
    row = ui.find("parameterRow_skin_smoothing")
    slider = ui.find("parameter_skin_smoothing")
    # Preserve an existing expanded group, rather than resetting all folds.
    ui.find("adjustmentSection_1").setProperty("expanded", True)
    QTest.qWait(40)
    assert not slider.isVisible()
    assert contained(ui.find("skinSmoothPresetButton"), scroll)
    before, cursor, lid = deepcopy(ui.e._layers), ui.e._cursor, ui.e.activeLayerId
    ui.click("skinSmoothPresetButton")
    wait_for(lambda: settled(ui.e) and contained(row, scroll))
    assert contained(slider, scroll)
    assert ui.e.parameters["skin_smoothing"] == 35
    assert "skin_smoothing" in ui.e.lockedFields
    assert ui.e._cursor == cursor + 1 and ui.e.activeLayerId == lid
    assert ui.e._layers[:-1] == before[:-1]
    assert ui.find("layerList").isVisible()
    assert all(ui.find(f"adjustmentSection_{i}").property("expanded") for i in range(3))
    # Adjust the exposed control by actual input. One drag remains one undo.
    ui.drag(
        ui.point("parameter_skin_smoothing", slider.property("visualPosition")),
        ui.point("parameter_skin_smoothing", 0.65),
    )
    wait_for(lambda: settled(ui.e))
    assert ui.e.parameters["skin_smoothing"] > 35
    assert ui.e._cursor == cursor + 2
    ui.key(Qt.Key_Z, Qt.ControlModifier)
    wait_for(lambda: settled(ui.e))
    assert ui.e.parameters["skin_smoothing"] == 35
    # Closing and reapplying the same effect reveals it with no duplicate edit.
    ui.click("adjustmentSection_2Toggle")
    assert not slider.isVisible()
    flick.setProperty("contentY", 0)
    wait_for(lambda: not flick.property("moving"))
    assert contained(ui.find("skinSmoothPresetButton"), scroll)
    ui.click("skinSmoothPresetButton")
    assert ui.find("adjustmentSection_2").property("expanded")
    try:
        wait_for(lambda: settled(ui.e) and contained(row, scroll))
    except AssertionError:
        raise AssertionError({
            "row_visible": row.isVisible(),
            "row_y": row.mapToItem(scroll, QPointF()).y(),
            "row_height": row.height(),
            "section_y": ui.find("adjustmentSection_2").mapToItem(scroll, QPointF()).y(),
            "scroll_height": scroll.height(),
            "content_y": flick.property("contentY"),
            "content_height": flick.property("contentHeight"),
            "settled": settled(ui.e),
        }) from None
    assert ui.e._cursor == cursor + 1
    # A subsequent render/parameter update must not scroll back to this shortcut.
    flick.setProperty("contentY", 0)
    ui.e.setParameter("sharpness", 10)
    ui.e.finishGesture()
    wait_for(lambda: settled(ui.e))
    assert flick.property("contentY") == 0
    ui.key(Qt.Key_Z, Qt.ControlModifier)
    wait_for(lambda: settled(ui.e))
    ui.key(Qt.Key_Z, Qt.ControlModifier)
    wait_for(lambda: settled(ui.e))
    assert ui.e._layers == before


def test_pending_parameter_reveal_cannot_scroll_the_range_view(canvas):  # noqa: F811
    ui = canvas
    ui.click("addLayerButton")
    wait_for(lambda: settled(ui.e))
    scroll = ui.find("propertyScroll")
    flick = scroll.property("contentItem")
    # Navigate before the deferred layout timer fires. The former parameter
    # target must not be interpreted in the newly visible range module.
    QTest.mouseClick(ui.w, Qt.LeftButton, Qt.NoModifier, ui.point("skinSmoothPresetButton"))
    ui.e.selection.clearPick()
    wait_for(lambda: settled(ui.e))
    QTest.qWait(60)
    assert ui.e.selection.pickedLayerId == ""
    assert ui.find("selectionDescriptionInput").isVisible()
    assert flick.property("contentY") == 0
    assert ui.e.parameters["skin_smoothing"] == 35
