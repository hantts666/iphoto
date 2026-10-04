"""State-driven inspector: picked layer shows adjustments, otherwise the range module."""

from copy import deepcopy

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
    for gone in ("adjustmentsTab", "sceneTab", "selectionTab", "selectionUiToggle", "directSelectionButton"):
        stack = [window.contentItem()]
        while stack:
            item = stack.pop()
            assert item.objectName() != gone, gone
            stack.extend(item.childItems())
    assert find("selectionDescriptionInput").property("visible")
    assert find("aiSelectionButton").property("text") == "选择"
    prompt, button = find("selectionDescriptionInput"), find("aiSelectionButton")
    assert abs(prompt.mapToScene(QPointF()).y() - button.mapToScene(QPointF()).y()) < 1
    assert button.mapToScene(QPointF()).x() >= prompt.mapToScene(QPointF()).x() + prompt.width()
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


def click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def test_thumbnail_review_primary_updates_original_layer_and_undo(ui):
    editor, window, find, warnings = ui
    editor.setParameter("exposure", 0.4)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    lid = editor.activeLayerId
    original = deepcopy(editor._layer())
    count = len(editor.layers)

    click(window, find("layerMaskThumb_" + lid))
    wait_for(lambda: editor.hasSelectionDraft and settled(editor))
    editor.drawDraft("rect", "subtract", [[0.05, 0.05], [0.4, 0.5]], 0)
    wait_for(lambda: settled(editor))
    primary = find("selectionToLayerButton")
    wait_for(lambda: primary.property("enabled"))
    assert primary.property("text") == "保存范围修改"
    assert original["name"] in find("draftStateCaption").property("text")
    assert original["name"] in find("editingContextLabel").property("text")
    revised = deepcopy(editor._candidate)
    click(window, primary)
    wait_for(lambda: not editor.hasSelectionDraft and settled(editor))

    assert len(editor.layers) == count
    assert editor.activeLayerId == lid
    assert editor._layer()["mask"] == revised
    assert editor._layer()["recipe"] == original["recipe"]
    assert editor.selection.pickedLayerId == lid
    assert find("parameter_exposure").property("visible")
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layer() == original
    assert not warnings, warnings


def test_thumbnail_review_cancel_returns_to_original_adjustments(ui):
    editor, window, find, warnings = ui
    original = deepcopy(editor._layer())
    click(window, find("layerMaskThumb_" + editor.activeLayerId))
    wait_for(lambda: editor.hasSelectionDraft and settled(editor))
    editor.draftAction("clear")
    wait_for(lambda: settled(editor))
    cancel = find("discardSelectionButton")
    assert cancel.property("text") == "取消修改"
    click(window, cancel)
    wait_for(lambda: not editor.hasSelectionDraft and settled(editor))
    assert editor._layer() == original
    assert editor.selection.pickedLayerId == original["id"]
    assert find("parameter_exposure").property("visible")
    assert not warnings, warnings


def test_new_range_primary_still_creates_adjustment(ui):
    editor, window, find, warnings = ui
    count = len(editor.layers)
    editor.drawDraft("rect", "replace", [[0.2, 0.2], [0.8, 0.8]], 0)
    primary = find("selectionToLayerButton")
    wait_for(lambda: primary.property("enabled"))
    assert primary.property("text") == "开始调整此范围"
    click(window, primary)
    wait_for(lambda: not editor.hasSelectionDraft and settled(editor))
    assert len(editor.layers) == count + 1
    assert find("parameter_exposure").property("visible")
    assert not warnings, warnings


def test_review_can_explicitly_create_another_layer(ui):
    editor, window, find, warnings = ui
    original = deepcopy(editor._layer())
    click(window, find("layerMaskThumb_" + editor.activeLayerId))
    wait_for(lambda: editor.hasSelectionDraft and settled(editor))
    click(window, find("moreOutputButton"))
    other = find("acceptSelectionButton")
    wait_for(lambda: other.property("visible") and other.property("enabled"))
    assert other.property("text") == "另建调整层"
    click(window, other)
    wait_for(lambda: not editor.hasSelectionDraft and settled(editor))
    assert len(editor.layers) == 2
    assert editor._layers[0] == original
    assert editor.activeLayerId != original["id"]
    assert not editor.selection.editingLayerMask
    assert not warnings, warnings
