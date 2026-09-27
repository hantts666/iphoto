"""Element list flows: checkboxes for batch combine, precache on restore."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.controllers import pixel_selections
from iphoto.document import raster_mask, read_project
from iphoto.segmentation.classical import bitmap_mask
from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled
from test_v14 import catalog


@pytest.fixture
def ui(qt_app, ai_store, tmp_path):
    for font in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font_path = Path("C:/Windows/Fonts") / font
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    image = Image.new("RGB", (600, 420), (44, 75, 102))
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 42, 240, 294), fill=(230, 151, 65))
    draw.rectangle((360, 42, 540, 294), fill=(94, 175, 136))
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
    editor._scene.set(catalog())
    editor.changed.emit()
    QTest.qWait(200)

    def find(name):
        stack = [window.contentItem()]
        while stack:
            item = stack.pop()
            if item.objectName() == name:
                return item
            stack.extend(item.childItems())
        raise AssertionError(name)

    def click(item):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(120)

    yield editor, window, find, click, warnings
    window.close()
    editor.close()
    qt_app.processEvents()


def test_checkbox_drives_direct_adjust_action(ui):
    editor, window, find, click, warnings = ui
    ids = [row["id"] for row in editor.sceneObjects][:2]
    assert editor.checkedObjectCount == 0
    assert not find("adjustCheckedObjectsButton").property("enabled")
    click(find("sceneCheck_" + ids[0]))
    click(find("sceneCheck_" + ids[1]))
    assert editor.checkedObjectCount == 2
    assert find("adjustCheckedObjectsButton").property("enabled")
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-checked-object-action.png"))
    click(find("sceneCheck_" + ids[0]))
    assert editor.checkedObjectCount == 1
    assert not warnings, warnings


def test_checked_objects_open_adjustments_in_one_click(ui, tmp_path):
    editor, window, find, click, warnings = ui
    rows = editor._scene.catalog["objects"][:2]
    for row in rows:
        mask = bitmap_mask(raster_mask(row["mask"], (600, 420)), row["name"])
        editor._scene.set_precise(row["id"], mask, {})
        click(find("sceneCheck_" + row["id"]))
    assert editor.checkedObjectCount == 2
    before = len(editor.layers)
    click(find("adjustCheckedObjectsButton"))
    wait_for(lambda: settled(editor) and len(editor.layers) == before + 1)
    assert not editor.hasSelectionDraft
    assert editor.checkedObjectCount == 0
    assert editor.selection.pickedLayerId == editor.activeLayerId
    assert find("unpickButton").property("visible")
    assert find("parameter_skin_smoothing")
    click(find("skinSmoothPresetButton"))
    wait_for(lambda: settled(editor))
    assert editor.parameters["skin_smoothing"] == 35
    project = tmp_path / "portrait.iphoto"
    editor.saveProject(str(project))
    wait_for(lambda: project.exists() and not editor.savingProject)
    assert read_project(project)["layers"][-1]["recipe"]["skin_smoothing"] == 35
    editor.undo()
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == before + 1
    assert editor.parameters["skin_smoothing"] == 0
    editor.undo()
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == before
    assert not warnings, warnings


def test_restored_catalog_triggers_precache(ui, monkeypatch):
    editor, window, find, click, warnings = ui
    editor._scene.remember(editor._scene_key())
    editor._scene.set(None)
    editor.changed.emit()
    assert not editor.sceneObjects
    captured = []
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    monkeypatch.setattr(Editor, "_request", lambda self, op, **data: captured.append(data))
    editor.analyzeScene(False)
    assert any(
        data.get("context", {}).get("purpose") == "precache" for data in captured
    ), captured
