"""Actual navigator pixels and input with controlled preview image delivery."""

from copy import deepcopy

from PIL import Image
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from shiboken6 import isValid

from test_ai import wait_for
from test_editor import settled
from test_import_export import ui as shared_ui
from test_preview_continuity_ui import frames  # noqa: F401

ui = shared_ui


@pytest.fixture(params=[(1440, 930), (1080, 700)])
def navigator_ui(ui, request):
    editor, window, find, errors, directory = ui
    window.resize(*request.param)
    window.setProperty("chatOpen", False)
    editor.viewport.setZoom(8)
    wait_for(lambda: find("canvasNavigator").property("previewReady"))
    QTest.qWait(100)

    class View:
        e, w, warnings, path = editor, window, errors, directory

        @staticmethod
        def find(name):
            return find(name)

        def point(self, name="navigatorMouse", x=.12, y=.83):
            item = find(name)
            return item.mapToScene(QPointF(item.width()*x, item.height()*y)).toPoint()

        def color(self):
            QTest.qWait(40)
            return window.grabWindow().pixelColor(self.point()).getRgb()[:3]

        def click(self, name="navigatorMouse", x=.5, y=.5):
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, self.point(name, x, y))
            QTest.qWait(40)

        def start(self, frame):
            editor._preview = frame["url"]
            editor.changed.emit()
            wait_for(lambda: frame["requested"].is_set()
                     and not find("canvasNavigator").property("previewReady"))

        def finish(self, frame, color):
            frame["release"].set()
            wait_for(lambda: find("canvasNavigator").property("previewReady") and self.color() == color)

        def state(self):
            return deepcopy((editor._layers, editor._history, editor._cursor, editor._generation,
                             editor._candidate, editor._draft_history, editor._pixel_points))

        def center(self):
            surface = find("canvasSurface")
            viewport = editor.viewport
            return ((surface.width()/2-viewport.imageX)/viewport.imageWidth,
                    (surface.height()/2-viewport.imageY)/viewport.imageHeight)

    view = View()
    yield view
    assert not errors, errors


def test_pending_thumbnail_keeps_pixels_and_click_drag_locate_without_editing(navigator_ui, frames):  # noqa: F811
    view = navigator_ui
    original, state = view.color(), view.state()
    target = view.find("navigatorMouse")
    size = (target.width(), target.height())
    slow = frames((180, 70, 40))
    view.start(slow)
    assert view.color() == original
    assert not view.find("canvasNavigator").property("useOriginal")
    assert (target.width(), target.height()) == size
    view.click(x=.7, y=.3)
    assert view.center() == pytest.approx((.7, .3), abs=.01)
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, view.point(x=.7, y=.3))
    QTest.mouseMove(view.w, view.point(x=.6, y=.4), 40)
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, view.point(x=.6, y=.4))
    assert view.center() == pytest.approx((.6, .4), abs=.01)
    assert view.state() == state and not view.w.property("previewReady")
    view.finish(slow, (180, 70, 40))
    assert view.state() == state and view.center() == pytest.approx((.6, .4), abs=.01)


def test_late_thumbnail_cannot_replace_latest_frame(navigator_ui, frames):  # noqa: F811
    view = navigator_ui
    state = view.state()
    slow, latest = frames((180, 70, 40)), frames((20, 150, 90))
    view.start(slow)
    view.start(latest)
    view.finish(latest, (20, 150, 90))
    slow["release"].set()
    QTest.qWait(100)
    assert view.color() == (20, 150, 90)
    assert view.find("navigatorPreviewImage").property("source").toString() == latest["url"]
    assert view.state() == state


def test_new_photo_discards_old_thumbnail_and_cancels_late_frame(navigator_ui, frames):  # noqa: F811
    view = navigator_ui
    shown, slow = frames((180, 70, 40)), frames((20, 150, 90))
    view.start(shown)
    view.finish(shown, (180, 70, 40))
    previous = view.find("navigatorPreviewImage")
    view.start(slow)
    original = view.e.originalUrl
    other = view.path / "new-portrait.png"
    Image.new("RGB", (240, 400), (30, 40, 180)).save(other)
    view.e.openImage(str(other))
    # Source publication/rebinding precedes Qt's deferred object deletion.
    # Wait for the lifetime boundary itself, retaining the normal 12s limit.
    wait_for(lambda: view.e.originalUrl != original and not isValid(previous))
    assert not isValid(previous)
    assert view.find("navigatorPreviewImage") is not previous
    view.e.viewport.setZoom(8)
    wait_for(lambda: settled(view.e) and view.find("canvasNavigator").property("previewReady"))
    assert view.color() == (30, 40, 180)
    slow["release"].set()
    QTest.qWait(100)
    assert view.color() == (30, 40, 180)
    assert view.find("navigatorMouse").width()/view.find("navigatorMouse").height() == pytest.approx(.6, abs=.01)
    assert view.e.imageName == other.name


def test_failed_thumbnail_uses_original_and_keeps_locating_during_recovery(navigator_ui, frames):  # noqa: F811
    view = navigator_ui
    original = view.color()
    shown = frames((180, 70, 40))
    view.start(shown)
    view.finish(shown, (180, 70, 40))
    view.e.drawDraft("rect", "replace", [[.1, .1], [.9, .9]], .025)
    wait_for(lambda: settled(view.e) and view.w.property("previewReady")
             and view.find("canvasNavigator").property("previewReady"))
    state = view.state()
    broken = frames(None)
    view.start(broken)
    broken["release"].set()
    wait_for(lambda: view.find("canvasNavigator").property("previewFailed"))
    assert view.find("canvasNavigator").property("useOriginal") and view.color() == original
    assert view.e.selection.showMask and view.e.hasSelectionDraft
    view.click(x=.65, y=.35)
    assert view.center() == pytest.approx((.65, .35), abs=.01) and view.state() == state
    wait_for(lambda: len(view.warnings) == 2)
    assert all(broken["url"] in error and "server replied: Not Found" in error
               and ("CanvasViewport.qml" in error or "CanvasNavigator.qml" in error)
               for error in view.warnings), view.warnings
    view.warnings.clear()
    recovery = frames((20, 150, 90))
    view.start(recovery)
    assert view.find("canvasNavigator").property("useOriginal") and view.color() == original
    canvas_color = view.w.grabWindow().pixelColor(view.point("canvasSurface", .5, .5)).getRgb()[:3]
    assert canvas_color == original  # The main canvas must also keep the clean fallback while retrying.
    assert view.find("canvasViewport").property("previewRecovering")
    assert view.find("previewLoadFailure").isVisible()
    view.click(x=.7, y=.3)
    assert view.center() == pytest.approx((.7, .3), abs=.01)
    view.finish(recovery, (20, 150, 90))
    assert not view.find("canvasNavigator").property("useOriginal") and view.state() == state
    assert not view.find("previewLoadFailure").isVisible()


def test_hidden_navigator_completes_pending_load_and_reopens_with_current_pixels(navigator_ui, frames):  # noqa: F811
    view = navigator_ui
    state = view.state()
    slow = frames((20, 150, 90))
    view.start(slow)
    image = view.find("navigatorPreviewImage")
    view.click("navigatorToggle")
    assert not view.find("canvasNavigator").isVisible()
    slow["release"].set()
    wait_for(lambda: view.find("canvasNavigator").property("previewReady"))
    view.click("navigatorToggle")
    assert view.find("canvasNavigator").isVisible()
    assert view.find("navigatorPreviewImage") is image and view.color() == (20, 150, 90)
    assert view.state() == state


def test_thumbnail_decodes_to_its_display_pixel_budget(navigator_ui):
    view = navigator_ui
    image = view.find("navigatorPreviewImage")
    original = view.find("navigatorOriginalImage")
    dpr = view.w.devicePixelRatio()
    content = image.parentItem()
    for item in (image, original):
        size = item.property("sourceSize")
        assert 0 < size.width() <= content.width()*dpr+1
        assert 0 < size.height() <= content.height()*dpr+1
        assert item.implicitWidth() <= content.width()*dpr+1
        assert item.implicitHeight() <= content.height()*dpr+1
    assert view.color() == (55, 90, 130)
