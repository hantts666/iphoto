"""Real Qt key/mouse/wheel delivery against the complete editor window."""

from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.document import raster_mask
from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled

LIVE = []


@pytest.fixture(params=[(1440, 930), (1080, 700)])
def canvas(qt_app, ai_store, tmp_path, request):
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.resize(*request.param)
    window.requestActivate()
    LIVE.append((engine, editor, window))
    path = tmp_path / "canvas.png"
    Image.new("RGB", (2400, 1600), (74, 123, 142)).save(path)
    editor.openImage(str(path))
    wait_for(
        lambda: editor.hasImage and settled(editor) and window.property("previewReady")
    )
    window.setProperty("chatOpen", False)
    QTest.qWait(150)

    class UI:
        app = qt_app
        e = editor
        w = window
        n = editor.viewport
        size = request.param

        def find(self, name):
            item = self.w.findChild(QObject, name)
            assert item is not None, name
            return item

        def point(self, name, x=0.5, y=0.5):
            item = self.find(name)
            return item.mapToScene(
                QPointF(item.width() * x, item.height() * y)
            ).toPoint()

        def click(self, name, x=0.5, y=0.5, mod=Qt.NoModifier):
            QTest.mouseClick(self.w, Qt.LeftButton, mod, self.point(name, x, y))
            QTest.qWait(40)

        def key(self, key, mod=Qt.NoModifier):
            QTest.keyClick(self.w, key, mod)
            QTest.qWait(30)

        def type(self, text):
            for char in text:
                QTest.keyClick(self.w, char)

        def drag(self, a, b, button=Qt.LeftButton):
            QTest.mousePress(self.w, button, Qt.NoModifier, a)
            QTest.mouseMove(self.w, b, 40)
            QTest.mouseRelease(self.w, button, Qt.NoModifier, b)
            QTest.qWait(30)

        def wheel(self, mod=Qt.NoModifier, pixel=None):
            point = self.point("canvasSurface", 0.35, 0.4)
            global_point = self.w.mapToGlobal(point)
            event = QWheelEvent(
                point,
                global_point,
                pixel or QPoint(),
                QPoint(0, 120),
                Qt.NoButton,
                mod,
                Qt.NoScrollPhase,
                False,
            )
            qt_app.sendEvent(self.w, event)
            QTest.qWait(30)

    ui = UI()
    try:
        yield ui
        assert not warnings, warnings
    finally:
        editor.close()
        window.hide()


def test_shortcuts_anchor_wheel_percent_resize_and_navigator(canvas):
    ui = canvas
    n = ui.n
    before = (deepcopy(ui.e._layers), ui.e._generation, ui.e._serial, ui.e.dirty)
    ui.click("photoCanvas")
    ui.key(Qt.Key_1, Qt.ControlModifier)
    assert n.zoom == 1
    ui.key(Qt.Key_Equal, Qt.ControlModifier)
    assert n.zoom == 1.5
    ui.key(Qt.Key_Minus, Qt.ControlModifier)
    assert n.zoom == 1
    ui.key(Qt.Key_Plus, Qt.ControlModifier)
    assert n.zoom == 1.5
    point = ui.point("canvasSurface", 0.35, 0.4)
    surface = ui.find("canvasSurface")
    local = surface.mapFromScene(QPointF(point))
    source = (
        (local.x() - n.imageX) / n.imageWidth,
        (local.y() - n.imageY) / n.imageHeight,
    )
    ui.wheel(Qt.AltModifier)
    assert n.zoom == pytest.approx(1.8)
    assert (
        (local.x() - n.imageX) / n.imageWidth,
        (local.y() - n.imageY) / n.imageHeight,
    ) == pytest.approx(source)
    x, y = n.imageX, n.imageY
    ui.wheel()
    assert n.imageX == x and n.imageY == pytest.approx(y + 60)
    ui.wheel(Qt.ShiftModifier)
    assert n.imageX == pytest.approx(x + 60)
    ui.wheel(pixel=QPoint(-17, 23))
    assert n.imageX == pytest.approx(x + 43) and n.imageY == pytest.approx(y + 83)
    ui.click("zoomPercentInput")
    ui.key(Qt.Key_A, Qt.ControlModifier)
    ui.type("225%")
    ui.key(Qt.Key_Return)
    assert n.zoom == 2.25
    assert not ui.w.property("textFocus")
    ui.click("zoomPercentInput")
    ui.key(Qt.Key_A, Qt.ControlModifier)
    ui.type("oops")
    ui.key(Qt.Key_Escape)
    assert n.zoom == 2.25
    ui.click("navigatorMouse", 0.7, 0.3)
    assert (surface.width() / 2 - n.imageX) / n.imageWidth == pytest.approx(
        0.7, abs=0.01
    )
    assert (surface.height() / 2 - n.imageY) / n.imageHeight == pytest.approx(
        0.3, abs=0.01
    )
    ui.w.resize(ui.size[0] + 90, ui.size[1] + 60)
    QTest.qWait(60)
    assert n.zoom == 2.25
    assert (surface.width() / 2 - n.imageX) / n.imageWidth == pytest.approx(
        0.7, abs=0.01
    )
    ui.click("fitCanvasButton")
    assert n.fitMode
    # Focusing/canceling percentage entry must not silently leave auto-fit mode.
    ui.click("zoomPercentInput")
    ui.key(Qt.Key_Escape)
    assert n.fitMode
    ui.click("zoomPercentInput")
    ui.key(Qt.Key_A, Qt.ControlModifier)
    ui.type("invalid")
    ui.key(Qt.Key_Return)
    assert n.fitMode
    ui.w.setProperty("chatOpen", True)
    QTest.qWait(50)
    assert (
        n.imageWidth <= surface.width() + 0.01
        and n.imageHeight <= surface.height() + 0.01
    )
    assert before == (ui.e._layers, ui.e._generation, ui.e._serial, ui.e.dirty)


def test_temporary_hand_middle_pan_selection_coordinates_and_text_guards(canvas):
    ui = canvas
    n = ui.n
    ui.click("tool_rect")
    candidate = deepcopy(ui.e._candidate)
    ui.key(Qt.Key_1, Qt.ControlModifier)
    a = ui.point("canvasSurface", 0.4, 0.4)
    b = a + QPoint(85, 36)
    start = (n.imageX, n.imageY)
    QTest.keyPress(ui.w, Qt.Key_Space)
    assert n.spaceHeld
    ui.drag(a, b)
    QTest.keyRelease(ui.w, Qt.Key_Space)
    assert not n.spaceHeld
    assert (n.imageX, n.imageY) == pytest.approx((start[0] + 85, start[1] + 36))
    assert ui.w.property("selectionTool") == "rect" and ui.e._candidate == candidate
    ui.drag(b, a, Qt.MiddleButton)
    assert (n.imageX, n.imageY) == pytest.approx(start)
    assert ui.e._candidate == candidate
    # Geometry changes during an unfinished stroke must not commit mismatched points.
    QTest.mousePress(ui.w, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(ui.w, b, 40)
    ui.key(Qt.Key_Plus, Qt.ControlModifier)
    QTest.mouseRelease(ui.w, Qt.LeftButton, Qt.NoModifier, b)
    assert ui.e._candidate == candidate
    # A rectangle made after panning/zooming must still address original image coordinates.
    photo = ui.find("photoCanvas")
    left = photo.mapFromScene(QPointF(a))
    right = photo.mapFromScene(QPointF(b))
    expected = (
        (left.x() + right.x()) / 2 / photo.width(),
        (left.y() + right.y()) / 2 / photo.height(),
    )
    ui.drag(a, b)
    wait_for(lambda: settled(ui.e))
    mask = raster_mask(ui.e._candidate, (2400, 1600))
    assert mask.getpixel((int(expected[0] * 2400), int(expected[1] * 1600))) == 255
    assert mask.getpixel((10, 10)) == 0
    # Pressing Space midway through a selection cancels the unfinished stroke.
    candidate = deepcopy(ui.e._candidate)
    QTest.mousePress(ui.w, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(ui.w, b, 40)
    QTest.keyPress(ui.w, Qt.Key_Space)
    QTest.mouseRelease(ui.w, Qt.LeftButton, Qt.NoModifier, b)
    QTest.keyRelease(ui.w, Qt.Key_Space)
    assert ui.e._candidate == candidate
    ui.key(Qt.Key_H)
    assert ui.w.property("selectionTool") == "hand" and ui.e._candidate == candidate
    QTest.keyPress(ui.w, Qt.Key_Space)
    ui.app.sendEvent(ui.w, QEvent(QEvent.WindowDeactivate))
    assert not n.spaceHeld
    QTest.keyRelease(ui.w, Qt.Key_Space)
    ui.w.requestActivate()
    ui.w.setProperty("chatOpen", True)
    QTest.qWait(50)
    ui.click("descriptionInput")
    zoom = n.zoom
    ui.type("h z hello world")
    ui.key(Qt.Key_1, Qt.ControlModifier)
    assert "h z hello world" in ui.find("descriptionInput").property("text")
    assert (
        ui.w.property("selectionTool") == "hand" and n.zoom == zoom and not n.spaceHeld
    )
    ui.click("aiSettingsButton")
    wait_for(lambda: ui.w.property("modalActive"))
    ui.key(Qt.Key_0, Qt.ControlModifier)
    ui.key(Qt.Key_Z)
    assert n.zoom == zoom and ui.w.property("selectionTool") == "hand"
    ui.find("aiSettingsDialog").close()


def test_zoom_tool_scrub_double_click_comparison_and_bounded_overlay(canvas):
    ui = canvas
    n = ui.n
    ui.click("tool_zoom")
    assert not ui.e.hasSelectionDraft
    # The optional wheel preference and temporary Ctrl+Space zoom use the same model.
    viewport = ui.find("canvasViewport")
    viewport.setProperty("wheelZoom", True)
    zoom_before = n.zoom
    ui.wheel()
    assert n.zoom == pytest.approx(zoom_before * 1.2)
    viewport.setProperty("wheelZoom", False)
    ui.key(Qt.Key_H)
    QTest.keyPress(ui.w, Qt.Key_Space, Qt.ControlModifier)
    assert n.temporaryZoom
    zoom_before = n.zoom
    ui.click("canvasSurface", 0.4, 0.4)
    assert n.zoom == pytest.approx(zoom_before * 1.25)
    QTest.keyRelease(ui.w, Qt.Key_Space, Qt.ControlModifier)
    assert not n.spaceHeld
    ui.key(Qt.Key_Z)
    z = n.zoom
    ui.click("canvasSurface", 0.4, 0.4)
    assert n.zoom == pytest.approx(z * 1.25)
    ui.click("canvasSurface", 0.4, 0.4, Qt.AltModifier)
    assert n.zoom == pytest.approx(z)
    a = ui.point("canvasSurface", 0.4, 0.4)
    b = a + QPoint(95, 0)
    ui.drag(a, b)
    assert n.zoom == pytest.approx(z * math_exp(0.95))
    ui.key(Qt.Key_1, Qt.ControlModifier)
    n.centerOn(0.5, 0.5)
    ui.w.setProperty("compare", True)
    ui.w.setProperty("selectionTool", "inspect")
    handle = ui.point("compareHandle")
    ui.drag(handle, handle + QPoint(50, 0))
    assert ui.w.property("split") > 0.5
    split = ui.w.property("split")
    QTest.keyPress(ui.w, Qt.Key_Space)
    ui.drag(a, b)
    QTest.keyRelease(ui.w, Qt.Key_Space)
    assert ui.w.property("split") == split
    ui.w.setProperty("compare", False)
    n.setZoom(32)
    overlay = ui.find("canvasOverlays")
    surface = ui.find("canvasSurface")
    assert overlay.width() == surface.width() and overlay.height() == surface.height()
    assert overlay.width() < ui.find("photoCanvas").width() / 20
    ui.click("tool_hand")
    QTest.mouseDClick(ui.w, Qt.LeftButton, Qt.NoModifier, a)
    QTest.qWait(50)
    assert n.fitMode
    # Screenshot includes a zoomed image and navigator without affecting the document.
    ui.e.loadDemo()
    wait_for(lambda: settled(ui.e) and ui.w.property("previewReady") and ui.e._sample)
    ui.w.setProperty("chatOpen", False)
    n.setZoom(0.8)
    n.centerOn(0.55, 0.48)
    QTest.qWait(250)
    assert ui.w.grabWindow().save(str(ROOT / f"artifacts/canvas-v141-{ui.size[0]}.png"))


def math_exp(value):
    from math import exp

    return exp(value)
