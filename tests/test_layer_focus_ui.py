"""Visible layer focus follows operations without stealing range navigation."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.document import MAX_LAYERS, new_layer, read_project
from test_ai import wait_for
from test_ai_hidden_effects import preview
from test_ai_layer_edits import setup_layers
from test_conversation_scroll_ui import _wheel
from test_editor import settled
from test_selection_ui_modes import ui, click


def focused(editor, find):
    lid = editor.activeLayerId
    assert editor.selection.pickedLayerId == lid
    assert not find("selectionDescriptionInput").isVisible()
    row = find("layerSelect_" + lid)
    assert row.parentItem().parentItem().parentItem().property("color").name() == "#435c53"
    viewport = find("layerList")
    point = viewport.mapFromScene(row.mapToScene(QPointF(row.width()/2, row.height()/2)))
    assert 0 < point.y() < viewport.height()


def menu_action(window, find, lid, name):
    row = find("layerSelect_" + lid)
    point = row.mapToScene(QPointF(row.width()/2, row.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, point)
    QTest.qWait(60)
    click(window, find(name))
    QTest.qWait(80)


def test_group_then_one_click_returns_to_source_layer(ui):
    editor, window, find, warnings = ui
    face, _, _ = setup_layers(editor)
    editor.selection.pickLayer(face["id"])
    QTest.qWait(100)
    before, pixels = deepcopy(editor._layers), preview(editor)
    cursor = editor._cursor
    click(window, find("groupLayerButton"))
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    assert editor.activeIsGroup and editor._cursor == cursor + 1
    focused(editor, find)
    assert preview(editor) == pixels
    click(window, find("layerSelect_" + face["id"]))
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    assert editor.activeLayerId == face["id"] and find("parameter_exposure").isVisible()
    focused(editor, find)
    assert editor._cursor == cursor + 1
    editor.undo()
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    assert editor._layers == before and preview(editor) == pixels
    focused(editor, find)
    assert not warnings, warnings


def test_add_from_range_opens_parameters_and_changes_only_new_layer(ui):
    editor, window, find, warnings = ui
    before = deepcopy(editor._layers)
    assert editor.selection.pickedLayerId == ""
    click(window, find("addLayerButton"))
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    focused(editor, find)
    slider = find("parameter_exposure")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                    slider.mapToScene(QPointF(slider.width()*.7, slider.height()/2)).toPoint())
    wait_for(lambda: settled(editor))
    assert editor._layers[:-1] == before and editor.parameters["exposure"] > 0
    assert not warnings, warnings


def test_copy_edit_undo_redo_delete_keep_highlight_and_target_together(ui):
    editor, window, find, warnings = ui
    face, _, _ = setup_layers(editor)
    editor.selection.pickLayer(face["id"])
    QTest.qWait(100)
    original = deepcopy(editor._layers)
    cursor = editor._cursor
    menu_action(window, find, face["id"], "layerMenuDuplicate")
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    copy_id = editor.activeLayerId
    assert copy_id != face["id"] and editor._cursor == cursor + 1
    focused(editor, find)
    slider = find("parameter_exposure")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                    slider.mapToScene(QPointF(slider.width()*.7, slider.height()/2)).toPoint())
    wait_for(lambda: settled(editor))
    assert face["recipe"] == original[1]["recipe"] and editor.parameters["exposure"] > .15
    editor.undo()  # parameter gesture
    wait_for(lambda: settled(editor))
    assert editor.parameters["exposure"] == .15
    editor.undo()  # copy creation
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    assert editor._layers == original
    focused(editor, find)
    editor.redo()
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    assert editor.activeLayerId == copy_id
    focused(editor, find)
    menu_action(window, find, copy_id, "layerMenuDelete")
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    assert all(l["id"] != copy_id for l in editor._layers)
    focused(editor, find)
    editor.undo()
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    assert editor.activeLayerId == copy_id
    focused(editor, find)
    assert not warnings, warnings


@pytest.mark.parametrize("action", ["select", "delete", "undo", "redo"])
def test_explicit_range_navigation_remains_clear_for_noncreating_operations(ui, action):
    editor, _, find, warnings = ui
    face, _, whole = setup_layers(editor)
    editor.selection.pickLayer(face["id"])
    if action == "redo":
        editor.duplicateLayer()
        editor.undo()
        wait_for(lambda: settled(editor))
    editor.selection.clearPick()
    if action == "select":
        editor.selectLayer(whole["id"])
    else:
        getattr(editor, {"delete": "deleteLayer"}.get(action, action))()
    wait_for(lambda: settled(editor))
    QTest.qWait(60)
    assert editor.selection.pickedLayerId == "" and find("selectionDescriptionInput").isVisible()
    assert not warnings, warnings


def test_undo_reveals_collapsed_ancestors_without_extra_render_or_history(ui):
    editor, _, find, warnings = ui
    face, _, whole = setup_layers(editor)
    outer = new_layer("外组", True, kind="group")
    inner = new_layer("内组", True, outer["id"], "group")
    face["parent_id"] = inner["id"]
    outer["collapsed"] = inner["collapsed"] = True
    editor._layers.extend([outer, inner])
    editor._selected = face["id"]
    editor._load_layer()
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    editor.selection.pickLayer(whole["id"])
    editor.setParameter("warmth", 25)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    cursor, generation = editor._cursor, editor._generation
    editor.undo()
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    assert editor.activeLayerId == face["id"]
    assert editor._cursor == cursor - 1 and editor._generation == generation + 1
    assert all(not l["collapsed"] for l in editor._layers if l["kind"] == "group")
    focused(editor, find)
    assert not warnings, warnings


@pytest.mark.parametrize("size", [(1440, 930), (1080, 700)])
def test_many_layers_reveal_focus_and_manual_scroll_survives_parameter_edits(ui, size):
    editor, window, find, warnings = ui
    window.resize(*size)
    for i in range(15):
        editor._layers.append(new_layer(f"调整 {i}", True))
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    editor.selection.pickLayer(editor._layers[0]["id"])
    QTest.qWait(100)
    focused(editor, find)
    viewport = find("layerList")
    assert viewport.property("contentY") > 0
    previous_scroll = viewport.property("contentY")
    _wheel(window, viewport, 1)
    wait_for(lambda: not viewport.property("moving"))
    assert viewport.property("contentY") < previous_scroll, (previous_scroll, viewport.property("contentY"))
    user_scroll = viewport.property("contentY")
    editor.setParameter("exposure", .4)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    assert viewport.property("contentY") == pytest.approx(user_scroll)
    editor.selection.pickLayer(editor._layers[1]["id"])
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    focused(editor, find)
    click(window, find("addLayerButton"))
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    focused(editor, find)
    assert not warnings, warnings


def test_open_project_clears_prior_focus_without_expanding_saved_groups(ui, tmp_path):
    editor, _, find, warnings = ui
    face, _, whole = setup_layers(editor)
    editor.selection.clearPick()
    group = new_layer("保留折叠组", True, kind="group")
    face["parent_id"] = group["id"]
    group["collapsed"] = True
    editor._layers.append(group)
    editor._selected = face["id"]
    editor._load_layer()
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    path = tmp_path / "folded.iphoto"
    editor.saveProject(str(path))
    wait_for(lambda: not editor.savingProject and path.exists())
    assert next(l for l in read_project(path)["layers"] if l["id"] == group["id"])["collapsed"]
    editor.selection.pickLayer(whole["id"])
    editor.openProject(str(path))
    wait_for(lambda: editor._pending_project is None and settled(editor))
    QTest.qWait(80)
    assert editor.selection.pickedLayerId == "" and find("selectionDescriptionInput").isVisible()
    assert editor.activeLayerId == face["id"]
    assert next(l for l in editor._layers if l["id"] == group["id"])["collapsed"]
    assert not editor._dirty
    assert not warnings, warnings


@pytest.mark.parametrize("action", ["addGlobalLayer", "duplicateLayer", "groupLayer"])
def test_failed_creation_keeps_range_and_document(ui, action):
    editor, _, find, warnings = ui
    editor._layers.extend(new_layer(f"调整 {i}", True) for i in range(MAX_LAYERS - 1))
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    before, cursor = deepcopy(editor._layers), editor._cursor
    getattr(editor, action)()
    QTest.qWait(80)
    assert editor._layers == before and editor._cursor == cursor
    assert editor.selection.pickedLayerId == "" and find("selectionDescriptionInput").isVisible()
    assert not warnings, warnings


def test_move_current_layer_into_folded_nested_group_reveals_it(ui):
    editor, _, find, warnings = ui
    face, _, _ = setup_layers(editor)
    outer = new_layer("折叠外组", True, kind="group")
    inner = new_layer("折叠内组", True, outer["id"], "group")
    outer["collapsed"] = inner["collapsed"] = True
    editor._layers.extend([outer, inner])
    editor._commit()
    editor._change()
    editor.selection.pickLayer(face["id"])
    wait_for(lambda: settled(editor))
    before = deepcopy(editor._layers)
    pixels, cursor = preview(editor), editor._cursor
    editor.moveToGroup(inner["id"])
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    assert editor.activeLayerId == face["id"] and editor.activeParentId == inner["id"]
    assert editor._cursor == cursor + 1 and preview(editor) == pixels
    focused(editor, find)
    editor.undo()
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    assert editor._layers == before and preview(editor) == pixels
    focused(editor, find)
    editor.redo()
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    focused(editor, find)
    assert not warnings, warnings


def test_manual_group_fold_stays_closed_until_explicit_focus_request(ui):
    editor, _, find, warnings = ui
    face, _, _ = setup_layers(editor)
    group = new_layer("人像组", True, kind="group")
    face["parent_id"] = group["id"]
    editor._layers.append(group)
    editor._commit()
    editor._change()
    editor.selection.pickLayer(face["id"])
    wait_for(lambda: settled(editor))
    editor.toggleGroup(group["id"])
    assert group["collapsed"]
    editor.setParameter("exposure", .5)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    assert group["collapsed"]
    cursor, generation = editor._cursor, editor._generation
    editor.selection.pickLayer(face["id"])
    QTest.qWait(100)
    assert not group["collapsed"] and editor._cursor == cursor and editor._generation == generation
    focused(editor, find)
    assert not warnings, warnings
