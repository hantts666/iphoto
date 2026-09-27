"""Context menus replace the button clutter: layers, elements, canvas, sliders."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled


@pytest.fixture
def ui(qt_app, ai_store, tmp_path):
    image = Image.new("RGB", (600, 420), (44, 75, 102))
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 42, 240, 294), fill=(230, 151, 65))
    path = tmp_path / "two.png"
    image.save(path)
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.resize(1440, 930)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))

    def find(name):
        stack = [window.contentItem()]
        while stack:
            item = stack.pop()
            if item.objectName() == name:
                return item
            stack.extend(item.childItems())
        raise AssertionError(name)

    def center(item):
        return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()

    def right_click(item):
        QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, center(item))
        QTest.qWait(150)

    def click(name):
        item = find(name)
        assert item.property("enabled") and item.property("visible"), name
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center(item))
        QTest.qWait(120)

    yield editor, window, find, right_click, click, warnings
    window.close()
    editor.close()
    qt_app.processEvents()


def test_layer_menu_delete_duplicate_and_undo(ui):
    editor, window, find, right_click, click, warnings = ui
    editor.addLayer()
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == 2
    lid = editor.activeLayerId
    editor.selection.pickLayer(lid)
    QTest.qWait(150)
    row = find("layerSelect_" + lid)
    right_click(row)
    click("layerMenuDuplicate")
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == 3
    right_click(find("layerSelect_" + editor.activeLayerId))
    click("layerMenuDelete")
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == 2
    editor.undo()
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == 3
    assert not warnings, warnings


def test_layer_menu_ungroup_disabled_outside_group(ui):
    editor, window, find, right_click, click, warnings = ui
    editor.selection.pickLayer(editor.activeLayerId)
    QTest.qWait(150)
    right_click(find("layerSelect_" + editor.activeLayerId))
    assert not find("layerMenuUngroup").property("enabled")
    QTest.keyClick(window, Qt.Key_Escape)
    assert not warnings, warnings


def test_element_row_menu_adds_to_range(ui, pixel_protocol_stub):
    from test_v14 import catalog

    editor, window, find, right_click, click, warnings = ui
    editor._scene.set(catalog())
    editor.changed.emit()
    QTest.qWait(200)
    right_click(find("sceneRow_object-1"))
    click("elemMenuReplace")
    wait_for(lambda: editor.hasSelectionDraft and settled(editor))
    right_click(find("sceneRow_object-2"))
    click("elemMenuAdd")
    wait_for(lambda: settled(editor))
    from iphoto.document import raster_mask

    mask = raster_mask(editor._candidate, (200, 140))
    assert mask.getpixel((40, 40)) == 255 and mask.getpixel((150, 40)) == 255
    assert not warnings, warnings


def test_canvas_menu_inverts_and_discards(ui):
    editor, window, find, right_click, click, warnings = ui
    editor.beginSelection("empty")
    editor.drawDraft("rect", "replace", [[0.2, 0.2], [0.6, 0.6]], 0.02)
    wait_for(lambda: settled(editor))
    right_click(find("canvasSurface"))
    click("canvasMenuInvert")
    wait_for(lambda: settled(editor))
    assert editor._candidate["inverted"]
    right_click(find("canvasSurface"))
    click("canvasMenuDiscard")
    wait_for(lambda: settled(editor))
    assert not editor.hasSelectionDraft
    assert not warnings, warnings


def test_slider_menu_unlocks(ui):
    editor, window, find, right_click, click, warnings = ui
    editor.selection.pickLayer(editor.activeLayerId)
    QTest.qWait(150)
    editor.setParameter("exposure", 0.5)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    assert "exposure" in editor.lockedFields
    right_click(find("parameter_exposure"))
    click("paramMenuUnlock")
    wait_for(lambda: settled(editor))
    assert "exposure" not in editor.lockedFields
    right_click(find("parameter_exposure"))
    click("paramMenuReset")
    wait_for(lambda: settled(editor))
    assert editor.parameters["exposure"] == 0
    assert not warnings, warnings
