"""Context menus read and mutate the clicked target without navigation side effects."""
# ruff: noqa: F811 -- imported pytest fixture parameters

from copy import deepcopy
import time

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.document import MAX_LAYERS, new_layer
from test_ai import wait_for
from test_ai_hidden_effects import preview
from test_ai_layer_edits import setup_layers
from test_editor import settled
from test_selection_ui_modes import ui, click  # noqa: F401 -- shared pytest fixture


def grouped(editor):
    face, arm, whole = setup_layers(editor)
    editor.selection.pickLayer(face["id"])
    editor.groupLayer()
    gid = editor.activeLayerId
    editor.renameLayer("人像组")
    editor.selection.pickLayer(whole["id"])
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    return face["id"], arm["id"], whole["id"], gid


def show_menu(window, find, lid):
    row = find("layerSelect_" + lid)
    viewport = find("layerList")
    # A clicked, non-active row can be partially below the viewport. Scroll
    # it into view without selecting it; menu tests still assert no navigation
    # or document mutation before the requested action.
    top = row.mapToItem(viewport,QPointF()).y()
    bottom = top + row.height()
    if top < 3 or bottom > viewport.height()-3:
        delta = top-3 if top<3 else bottom-viewport.height()+3
        viewport.setProperty('contentY',viewport.property('contentY')+delta)
        QTest.qWait(40)
    point = row.mapToScene(QPointF(row.width()/2, row.height()/2))
    assert 0 < viewport.mapFromScene(point).y() < viewport.height()
    QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, point.toPoint())
    QTest.qWait(80)


def show_destinations(window):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.property("text") == "移入组" and "MenuItem" in item.metaObject().className():
            click(window, item)
            QTest.qWait(80)
            return
        stack.extend(item.childItems())
    raise AssertionError("移入组")


def snapshot(editor):
    return (deepcopy(editor._layers), editor.activeLayerId, editor.selection.pickedLayerId,
            editor._cursor, editor._generation, editor._edit_revision)


def test_clicked_child_can_ungroup_without_selecting_first_and_one_render(ui):
    editor, window, find, warnings = ui
    face, _, whole, _ = grouped(editor)
    before, pixels = snapshot(editor), preview(editor)
    serial = editor._serial
    show_menu(window, find, face)
    assert snapshot(editor) == before
    assert find("layerMenuTarget").property("text") == "面部磨皮"
    assert find("layerMenuUngroup").property("enabled")
    click(window, find("layerMenuUngroup"))
    wait_for(lambda: settled(editor))
    assert editor.activeLayerId == face and editor.activeParentId == ""
    assert editor._cursor == before[3] + 1 and editor._generation == before[4] + 1
    assert editor._serial == serial + 1
    for old, layer in zip(before[0], editor._layers):
        assert layer == ({**old, "parent_id": ""} if old["id"] == face else old)
    assert preview(editor) == pixels
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before[0] and editor.activeLayerId == whole and preview(editor) == pixels
    assert not warnings, warnings


def test_root_clicked_while_child_active_disables_ungroup_and_top_movement(ui):
    editor, window, find, warnings = ui
    face, _, whole, _ = grouped(editor)
    editor.selection.pickLayer(face)
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    before = snapshot(editor)
    show_menu(window, find, whole)
    assert not find("layerMenuUngroup").property("enabled")
    assert not find("layerMenuUp").property("enabled") and find("layerMenuDown").property("enabled")
    assert snapshot(editor) == before
    assert not warnings, warnings


def test_clicked_root_offers_active_group_and_moves_in_one_transaction(ui):
    editor, window, find, warnings = ui
    face, _, whole, group = grouped(editor)
    editor.selection.pickLayer(group)
    wait_for(lambda: settled(editor))
    QTest.qWait(100)
    before = snapshot(editor)
    show_menu(window, find, whole)
    show_destinations(window)
    choice = find("layerMenuMove_" + group)
    assert choice.isVisible() and choice.property("text") == "人像组"
    assert snapshot(editor) == before
    click(window, choice)
    wait_for(lambda: settled(editor))
    assert editor.activeLayerId == whole and editor.activeParentId == group
    assert editor._cursor == before[3] + 1 and editor._generation == before[4] + 1
    assert not window.findChild(QObject, "layerContextMenu").property("visible")
    QTest.qWait(100)
    show_menu(window, find, face)
    assert find("layerMenuTarget").property("text") == "面部磨皮"
    QTest.keyClick(window, Qt.Key_Escape)
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before[0]
    assert not warnings, warnings


@pytest.mark.parametrize("action", ["toggle", "delete"])
def test_other_layer_visibility_or_delete_preserves_current_parameters(ui, action):
    editor, window, find, warnings = ui
    face, _, whole, _ = grouped(editor)
    current = deepcopy(editor._layer())
    before = snapshot(editor)
    show_menu(window, find, face)
    click(window, find("layerMenuToggle" if action == "toggle" else "layerMenuDelete"))
    wait_for(lambda: settled(editor))
    assert editor.activeLayerId == whole and editor.selection.pickedLayerId == whole
    assert editor._layer() == current and editor._cursor == before[3] + 1
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before[0] and editor.activeLayerId == whole
    assert not warnings, warnings


@pytest.mark.parametrize("reason", ["busy", "selection", "regions"])
def test_menu_mutations_are_disabled_while_unavailable(ui, reason):
    editor, window, find, warnings = ui
    lid = editor.activeLayerId
    if reason == "busy":
        editor._export_aborting = True
    elif reason == "selection":
        editor.beginSelection("empty")
        wait_for(lambda: settled(editor))
    else:
        editor._region_candidate = []
        editor.changed.emit()
    try:
        before = snapshot(editor)
        show_menu(window, find, lid)
        for name in ("Pick", "Range", "Rename", "Duplicate", "Toggle", "Group", "Up", "Down", "Ungroup", "Delete"):
            assert not find("layerMenu" + name).property("enabled"), name
        assert not editor.runLayerAction(lid, "duplicate", "")
        assert snapshot(editor) == before
    finally:
        editor._export_aborting = False
        editor._candidate = editor._region_candidate = None
    assert not warnings, warnings


def test_last_adjustment_delete_and_root_noop_disabled(ui):
    editor, window, find, warnings = ui
    lid = editor.activeLayerId
    show_menu(window, find, lid)
    assert not find("layerMenuDelete").property("enabled")
    assert not find("layerMenuUp").property("enabled") and not find("layerMenuDown").property("enabled")
    before = snapshot(editor)
    assert not editor.runLayerAction(lid, "delete", "")
    assert not editor.runLayerAction(lid, "move", "")
    assert snapshot(editor) == before
    assert not warnings, warnings


@pytest.mark.parametrize("invalid", ["missing_layer", "unknown_action", "self", "descendant", "unknown_parent"])
def test_rejected_action_does_not_switch_target_or_render(ui, invalid):
    editor, _, _, warnings = ui
    face, _, whole, group = grouped(editor)
    before = snapshot(editor)
    lid, action, parent = group, "move", ""
    if invalid == "missing_layer": lid = "gone"
    elif invalid == "unknown_action": action = "arbitrary"
    elif invalid == "self": parent = group
    elif invalid == "descendant": parent = face
    else: parent = "gone"
    assert not editor.runLayerAction(lid, action, parent)
    assert snapshot(editor) == before and editor.activeLayerId == whole
    assert not warnings, warnings


def test_context_filters_descendants_current_parent_and_depth(ui):
    editor, _, _, warnings = ui
    face, _, _, group = grouped(editor)
    outer = new_layer("外组", True, kind="group")
    chain = [outer]
    for name in ("二层组", "三层组", "四层组"):
        chain.append(new_layer(name, True, chain[-1]["id"], "group"))
    leaf = new_layer("最深层", True, chain[-1]["id"])
    editor._layers.extend(chain + [leaf])
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    before = snapshot(editor)
    options = {g["id"] for g in editor.layerContext(group)["groups"]}
    assert group not in options and face not in options
    assert chain[-1]["id"] not in options and outer["id"] in options
    assert group not in {g["id"] for g in editor.layerContext(face)["groups"]}
    assert not editor.layerContext(leaf["id"])["group"]
    assert not editor.runLayerAction(group, "move", chain[-1]["id"])
    assert snapshot(editor) == before
    assert not warnings, warnings


def test_query_is_readonly_at_max_layers_and_creation_is_disabled(ui):
    editor, _, _, warnings = ui
    editor._layers.extend(new_layer(f"组 {i}", True, kind="group") for i in range(MAX_LAYERS - 1))
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    before = snapshot(editor)
    started = time.perf_counter()
    for _ in range(20):
        context = editor.layerContext(editor.activeLayerId)
        assert not context["duplicate"] and not context["group"]
        assert len(context["groups"]) == MAX_LAYERS - 1
    assert time.perf_counter() - started < 1.0
    assert not editor.runLayerAction(editor.activeLayerId, "duplicate", "")
    assert not editor.runLayerAction(editor.activeLayerId, "group", "")
    assert snapshot(editor) == before
    assert not warnings, warnings


def test_target_removed_while_menu_open_cannot_act_on_current_layer(ui):
    editor, window, find, warnings = ui
    face, _, whole, _ = grouped(editor)
    show_menu(window, find, face)
    assert editor.runLayerAction(face, "delete", "")
    wait_for(lambda: settled(editor))
    QTest.qWait(80)
    before = snapshot(editor)
    assert find("layerMenuTarget").property("text") == "图层已不存在"
    assert not find("layerMenuDuplicate").property("enabled")
    assert not editor.runLayerAction(face, "duplicate", "")
    assert snapshot(editor) == before and editor.activeLayerId == whole
    assert not warnings, warnings


@pytest.mark.parametrize("size", [(1440, 930), (1080, 700)])
def test_popup_uses_clicked_coordinates_and_stays_in_window(ui, size):
    editor, window, find, warnings = ui
    window.resize(*size)
    lid = editor.activeLayerId
    editor.selection.pickLayer(lid)
    QTest.qWait(100)
    row = find("layerSelect_" + lid)
    point = row.mapToScene(QPointF(row.width()/2, row.height()/2))
    show_menu(window, find, lid)
    top = find("layerMenuTarget").mapToScene(QPointF(0, 0))
    last = find("layerMenuDelete")
    bottom = last.mapToScene(QPointF(last.width(), last.height()))
    assert 0 <= top.x() < point.x() < bottom.x() <= size[0] + 1
    assert 0 <= top.y() <= point.y() <= bottom.y() <= size[1] + 1
    assert not warnings, warnings


def test_command_undo_retains_preceding_unfinished_slider_parameters(ui):
    editor, _, _, warnings = ui
    face, _, whole, _ = grouped(editor)
    editor.setParameter("exposure", .75)
    wait_for(lambda: settled(editor))
    before = deepcopy(editor._layers)
    assert editor.runLayerAction(face, "ungroup", "")
    wait_for(lambda: settled(editor))
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor.activeLayerId == whole and editor._layers == before
    assert editor.parameters["exposure"] == .75
    assert "exposure" in editor.lockedFields
    editor.undo()
    wait_for(lambda: settled(editor))
    assert next(l for l in editor._layers if l["id"] == whole)["recipe"]["exposure"] == .3
    assert not warnings, warnings
