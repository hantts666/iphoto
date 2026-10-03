"""Actual rendered pixels and hover ownership across editor state changes.

Enter/exit signals are controlled because offscreen has no native pointer.
Photo loading, catalog updates, complete QML, buttons and rendering are real.
"""

from copy import deepcopy

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QMetaObject, QModelIndex, QPointF, Qt
from PySide6.QtTest import QTest
from shiboken6 import isValid

from iphoto.document import empty_mask
from iphoto.masks import encode_bitmap
from iphoto.scene_rows_model import SceneRowsModel
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui as shared_ui
from test_preview_continuity_ui import frames  # noqa: F401
from test_v14 import catalog

ui = shared_ui


@pytest.fixture(params=[(1440, 930), (1080, 700)])
def hover_ui(ui, request):
    editor, window, find, errors, directory = ui
    window.resize(*request.param)
    window.setProperty("chatOpen", False)
    editor._scene.set(catalog())
    editor.changed.emit()
    wait_for(lambda: window.property("previewReady"))
    QTest.qWait(100)

    class View:
        e, w, warnings, path = editor, window, errors, directory

        @staticmethod
        def find(name):
            return find(name)

        def point(self, name, x=.5, y=.5):
            item = find(name)
            return item.mapToScene(QPointF(item.width()*x, item.height()*y)).toPoint()

        def click(self, name):
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, self.point(name))
            QTest.qWait(40)

        def color(self, x=.25, y=.45):
            QTest.qWait(40)  # Let bound properties and the Canvas render a frame.
            return window.grabWindow().pixelColor(self.point("photoCanvas", x, y)).getRgb()[:3]

        def enter(self, lid="object-1"):
            assert QMetaObject.invokeMethod(find("sceneRowHover_"+lid), "entered", Qt.DirectConnection)
            return find("canvasOverlayGroup")

        def state(self):
            return deepcopy((editor._layers, editor._history, editor._cursor, editor._generation,
                             editor._candidate, editor._draft_history, editor._pixel_points))

    view = View()
    yield view
    assert not errors, errors


def test_scene_rows_reset_only_for_catalog_identity_and_update_changed_rows(qt_app):
    model = SceneRowsModel()
    resets, changed = [], []
    model.modelReset.connect(lambda: resets.append(True))
    model.dataChanged.connect(lambda first, last, roles: changed.append((first.row(), last.row(), roles)))
    rows = [{"id": "one", "checked": False}, {"id": "two", "checked": False}]
    model.replace(deepcopy(rows), 1)
    model.replace(deepcopy(rows), 1)
    assert resets == [True] and not changed
    model.replace([rows[0], {**rows[1], "checked": True}], 1)
    assert changed == [(1, 1, [model.ROW_ROLE])]
    assert model.rowCount() == 2 and model.rowCount(model.index(0, 0)) == 0
    assert model.data(model.index(1, 0), model.ROW_ROLE)["checked"]
    assert model.data(QModelIndex(), model.ROW_ROLE) is None
    model.replace(deepcopy(rows), 2)  # Identical ids can belong to a new analysis.
    assert resets == [True, True]


def test_normal_row_preview_switch_exit_and_status_update_keep_pixel_behavior(hover_ui):
    view = hover_ui
    original, state = view.color(), view.state()
    row = view.find("sceneRowHover_object-1")
    resets = []
    view.e.sceneRowsModel.modelReset.connect(lambda: resets.append(True))
    view.enter()
    assert view.color() != original
    assert view.w.property("sceneHoverId") == "object-1"
    view.e._status = "ordinary progress without a new catalog"
    view.e.changed.emit()
    assert isValid(row) and view.find("sceneRowHover_object-1") is row
    assert view.color() != original and not resets
    view.enter("object-2")
    assert QMetaObject.invokeMethod(row, "exited", Qt.DirectConnection)
    assert view.w.property("sceneHoverId") == "object-2"
    assert view.color() == original and view.color(.75) != original
    assert QMetaObject.invokeMethod(view.find("sceneRowHover_object-2"), "exited", Qt.DirectConnection)
    assert view.color(.75) == original
    assert view.state() == state


def test_checked_row_and_precise_mask_update_do_not_destroy_hover_owner(hover_ui):
    view = hover_ui
    original, state = view.color(), view.state()
    row = view.find("sceneRowHover_object-1")
    resets, changed = [], []
    view.e.sceneRowsModel.modelReset.connect(lambda: resets.append(True))
    view.e.sceneRowsModel.dataChanged.connect(lambda first, *_args: changed.append(first.row()))
    view.enter()
    view.click("sceneCheck_object-1")
    assert view.e.checkedObjectCount == 1
    assert isValid(row) and view.find("sceneRowHover_object-1") is row
    assert view.w.property("sceneHoverId") == "object-1"
    assert changed == [0] and not resets
    pixels = np.zeros((24, 32), dtype=np.uint8)
    pixels[:, :16] = 128
    pixels[7:17, 4:12] = 0
    precise = {**empty_mask(), "bitmap": encode_bitmap(Image.fromarray(pixels, "L"), sampling="alpha", preserve_resolution=True)}
    view.e._scene.set_precise("object-1", precise, {})
    view.e.changed.emit()
    assert view.color() == original  # The new real mask has a protected hole.
    assert view.color(.15, .2) != original
    assert view.w.property("sceneHoverId") == "object-1"
    assert isValid(row) and view.find("sceneRowHover_object-1") is row
    assert changed == [0, 0] and not resets and view.state() == state


def test_result_inspector_clears_owner_and_range_return_does_not_resurrect_it(hover_ui):
    view = hover_ui
    original, state = view.color(), view.state()
    view.enter()
    assert view.color() != original
    view.click("layerSelect_"+view.e.activeLayerId)
    assert view.e.selection.pickedLayerId
    assert not view.w.property("sceneHoverId") and view.color() == original
    # An outdated writer cannot paint over the result or survive a scope change.
    view.w.setProperty("sceneHoverId", "object-1")
    view.enter()  # disabled row must reject an enter signal
    assert not view.find("objectHoverPreview").property("visible")
    assert view.color() == original
    view.click("unpickButton")
    assert not view.w.property("sceneHoverId") and view.color() == original
    view.enter()
    assert view.color() != original
    assert view.state() == state


def test_hold_original_and_comparison_show_clean_pixels_without_losing_preferences(hover_ui):
    view = hover_ui
    original = view.color()
    state = view.state()
    view.enter()
    assert view.color() != original
    button = view.point("holdOriginalButton")
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.color() == original
    assert not view.find("canvasOverlayGroup").property("visible")
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert not view.find("canvasOverlayGroup").property("activeHover")
    view.enter()
    view.click("compareButton")
    assert view.color() == original and view.color(.75) == original
    assert not view.w.property("sceneHoverId")
    view.click("compareButton")
    assert view.color() == original
    assert view.w.property("split") == .5 and view.state() == state


def test_canvas_object_hover_ends_with_tool_and_catalog_identity(hover_ui):
    view = hover_ui
    original = view.color()
    view.e.selection.chooseTool("object")
    if view.e.selection.showMask:  # Isolate transient hover from mask display.
        view.e.selection.toggleShowMask()
    overlay = view.find("canvasOverlayGroup")
    overlay.setProperty("hoverId", "object-1")
    assert view.color() != original
    view.e.selection.chooseTool("hand")
    assert view.color() == original and not overlay.property("activeHover")
    view.e.selection.chooseTool("object")
    if view.e.selection.showMask:
        view.e.selection.toggleShowMask()
    assert not overlay.property("hoverId")
    overlay.setProperty("hoverId", "object-1")
    view.e._scene.set(catalog())
    view.e.changed.emit()
    assert not overlay.property("hoverId") and view.color() == original


def test_catalog_refresh_and_new_photo_cannot_reuse_old_row_identity(hover_ui):
    view = hover_ui
    original = view.color()
    view.enter()
    view.e._scene.set(catalog())
    view.e.changed.emit()
    assert not view.w.property("sceneHoverId") and view.color() == original
    view.enter()
    other = view.path / "new-photo.png"
    Image.new("RGB", (350, 220), (30, 40, 180)).save(other)
    view.e.openImage(str(other))
    wait_for(lambda: view.e.imageName == other.name and settled(view.e) and view.w.property("previewReady"))
    view.e._scene.set(catalog())  # New photo has recycled object-1/2 ids.
    view.e.changed.emit()
    assert not view.w.property("sceneHoverId") and view.color() == (30, 40, 180)
    view.enter()
    assert view.color() != (30, 40, 180)


def test_modal_task_and_space_temporarily_hide_preview_and_keep_document(hover_ui):
    view = hover_ui
    original, state = view.color(), view.state()
    view.enter()
    view.e._pixel_queue.append({"op": "segment", "jobs": [], "context": {"purpose": "objects"}})
    view.e.changed.emit()
    assert view.e.busy and view.color() == original and not view.w.property("sceneHoverId")
    view.e._pixel_queue.clear()
    view.e.changed.emit()
    view.enter()
    view.w.setProperty("modalActive", True)
    assert view.color() == original
    view.w.setProperty("modalActive", False)
    assert not view.w.property("sceneHoverId")
    view.enter()
    view.find("photoCanvas").forceActiveFocus()
    QTest.keyPress(view.w, Qt.Key_Space)
    assert view.color() == original
    QTest.keyRelease(view.w, Qt.Key_Space)
    assert view.color() != original  # Same valid pointer owner after a temporary pan.
    assert view.state() == state


def test_original_view_hides_actual_smart_prompts_without_clearing_them(hover_ui, monkeypatch):
    view = hover_ui
    monkeypatch.setattr("iphoto.controllers.pixel_selections.warm", lambda _editor: None)
    original = view.color(.5, .5)
    view.e.selection.chooseTool("smart")
    if view.e.selection.showMask:  # Test the prompt, without the current layer's mask.
        view.e.selection.toggleShowMask()
    view.e._pixel_points = [[.5, .5, 1]]
    view.e.changed.emit()
    state = view.state()
    assert view.color(.5, .5) != original
    button = view.point("holdOriginalButton")
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.color(.5, .5) == original
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.color(.5, .5) != original
    view.click("compareButton")
    view.w.setProperty("split", .7)  # Keep the comparison divider away from the dot.
    assert view.color(.5, .5) == original
    view.click("compareButton")
    assert view.e.selection.tool == "inspect"
    assert view.color(.5, .5) == original and view.state() == state


def test_preview_failure_fallback_is_uncolored_even_with_a_stale_row_owner(hover_ui, frames):  # noqa: F811
    view = hover_ui
    original, state = view.color(), view.state()
    view.enter()
    broken = frames(None)
    view.e._preview = broken["url"]
    view.e.changed.emit()
    wait_for(lambda: broken["requested"].is_set())
    broken["release"].set()
    wait_for(lambda: view.find("canvasViewport").property("previewFailed"))
    assert view.color() == original and view.state() == state
    assert view.e.selection.showMask is False
    wait_for(lambda: len(view.warnings) == 2)
    assert all(broken["url"] in error and "server replied: Not Found" in error for error in view.warnings)
    view.warnings.clear()
