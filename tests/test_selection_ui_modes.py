"""State-driven inspector: picked layer shows adjustments, otherwise the range module."""

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.workspace import Editor, ROOT
from iphoto.controllers import layers as layer_actions
from test_ai import wait_for
from test_editor import settled


@pytest.fixture
def ui(qt_app, ai_store, tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (300, 200), (55, 90, 130)).save(path)
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

    yield editor, window, find, warnings
    window.close()
    editor.close()
    qt_app.processEvents()


def test_no_tabs_and_default_is_range_module(ui):
    editor, window, find, warnings = ui
    for gone in ("adjustmentsTab", "sceneTab", "selectionTab", "selectionUiToggle"):
        stack = [window.contentItem()]
        while stack:
            item = stack.pop()
            assert item.objectName() != gone, gone
            stack.extend(item.childItems())
    assert find("selectionDescriptionInput").property("visible")
    assert not find("parameter_exposure").property("visible")
    assert not warnings, warnings


def test_pick_layer_shows_adjustments_and_unpick_returns(ui):
    editor, window, find, warnings = ui
    lid = editor.activeLayerId
    editor.selection.pickLayer(lid)
    QTest.qWait(150)
    assert find("parameter_exposure").property("visible")
    assert not find("selectionDescriptionInput").property("visible")
    # clicking the picked row again unpicks
    row = find("layerSelect_" + lid)
    point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(150)
    assert editor.selection.pickedLayerId == ""
    assert find("selectionDescriptionInput").property("visible")
    # header button also unpicks
    editor.selection.pickLayer(lid)
    QTest.qWait(150)
    click_target = find("unpickButton")
    point = click_target.mapToScene(
        QPointF(click_target.width() / 2, click_target.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(150)
    assert editor.selection.pickedLayerId == ""
    assert not warnings, warnings


def test_draft_forces_range_module(ui):
    editor, window, find, warnings = ui
    editor.selection.pickLayer(editor.activeLayerId)
    QTest.qWait(120)
    editor.beginSelection("empty")
    QTest.qWait(150)
    assert editor.selection.pickedLayerId == ""
    assert find("selectionDescriptionInput").property("visible")
    editor.discardSelection()
    wait_for(lambda: settled(editor))
    assert not warnings, warnings


def test_layer_thumbnail_updates_when_mask_changes_but_label_does_not(ui):
    editor, window, find, warnings = ui
    editor._layer()["mask"]["label"] = "手动选区"
    editor.changed.emit()
    QTest.qWait(50)
    name = "layerMaskThumb_" + editor.activeLayerId
    original = find(name).property("source")
    editor.drawDraft("rect", "replace", [[0.2, 0.2], [0.8, 0.8]], 0)
    wait_for(lambda: editor.hasSelectionDraft and settled(editor))
    editor.selection.apply("replace_mask")
    wait_for(lambda: not editor.hasSelectionDraft and settled(editor))
    assert editor._layer()["mask"]["label"] == "手动选区"
    changed = find(name).property("source")
    assert changed != original
    editor.undo()
    wait_for(lambda: settled(editor))
    assert find(name).property("source") == original
    assert not warnings, warnings


def test_opacity_updates_keep_thumbnail_delegate_and_source(ui, monkeypatch):
    editor, window, find, warnings = ui
    editor.selection.pickLayer(editor.activeLayerId)
    QTest.qWait(50)
    name = "layerMaskThumb_" + editor.activeLayerId
    thumbnail = find(name)
    source = thumbnail.property("source")
    calls = []
    original = layer_actions.layerMaskThumbnail

    def counted(owner, lid):
        calls.append(lid)
        return original(owner, lid)

    monkeypatch.setattr(layer_actions, "layerMaskThumbnail", counted)
    editor.setOpacity(57)
    wait_for(lambda: settled(editor))
    assert find(name) == thumbnail
    assert thumbnail.property("source") == source
    assert not calls
    assert not warnings, warnings
