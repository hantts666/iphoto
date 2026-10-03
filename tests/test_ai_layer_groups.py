"""AI group transactions preserve scopes, composite order and user history."""

from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QUrl

from iphoto.ai_layer_groups import validate_group_plan
from iphoto.ai_layer_edits import validate_layer_edits
from iphoto.ai_protocol import parse_auto
from iphoto.controllers import layers
from iphoto.document import MAX_LAYERS, new_layer, read_project, render_nodes
from iphoto.engine import Recipe
from iphoto.layer_tree import forest
from test_ai import configure, mock_api, wait_for
from test_ai_layer_edits import response, setup_layers, click
from test_editor import settled
from test_selection_ui_modes import ui as shared_ui

ui = shared_ui


def group_plan(*members, opacity=.5, visible=True, name="人像精修"):
    return {"name": name, "layer_ids": [layer["id"] for layer in members],
            "visible": visible, "opacity": opacity}


def group_response(current, plan):
    payload = {"action": "group", "scope": "existing_layers", "summary": "已经自动完成编组",
               "recipe": dict(current), "regions": [], "layer_edits": [], "group": plan}
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}


def controls(layer, *, visible=None, opacity=None):
    return {"layer_id": layer["id"], "recipe": None, "visible": visible, "opacity": opacity}


def preview(editor):
    with Image.open(QUrl(editor._preview).toLocalFile()) as photo:
        return np.array(photo.convert("RGB"))


def test_conversation_groups_named_layers_with_one_undo_and_saves(ui, tmp_path):
    editor, window, find, warnings = ui
    face, arm, whole = setup_layers(editor)
    face["locked"] = ["skin_smoothing"]
    arm["visible"] = False
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    before = deepcopy(editor._layers)
    previous_id = editor.activeLayerId
    generation, cursor = editor._generation, editor._cursor
    before_pixels = preview(editor)
    with mock_api(group_response(editor._recipe, group_plan(arm, face))) as (url, requests):
        configure(editor.ai, url)
        find("descriptionInput").setProperty("text", "把面部和手臂磨皮编成人像精修组，整体50%，保留原参数")
        click(window, find("applyDescriptionButton"))
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None and settled(editor))
        group = editor._layer()
        assert group["kind"] == "group" and group["opacity"] == .5
        assert editor.selection.pickedLayerId == group["id"]
        assert len(requests) == 1 and len(editor._layers) == 5
        assert editor._generation == generation + 1 and editor._cursor == cursor + 1
        assert group["parent_id"] == "" and not any(group["recipe"].values())
        assert [l["id"] for l in editor._layers if l["parent_id"] == group["id"]] == [face["id"], arm["id"]]
        for old in before:
            new = next(l for l in editor._layers if l["id"] == old["id"])
            assert {k: v for k, v in new.items() if k != "parent_id"} == {k: v for k, v in old.items() if k != "parent_id"}
            assert new["parent_id"] == (group["id"] if old["id"] in (face["id"], arm["id"]) else old["parent_id"])
        assert whole == before[-1]
        assert editor.conversation[-1]["layer_id"] == group["id"]
        assert "50%" in editor.conversation[-1]["text"] and "子层参数" in editor.conversation[-1]["text"]
        changed = deepcopy(editor._layers)
        changed_pixels = preview(editor)
        assert not np.array_equal(changed_pixels, before_pixels)
        project = tmp_path / "ai-group.iphoto"
        editor.saveProject(str(project))
        assert read_project(project)["layers"] == changed
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before and editor.activeLayerId == previous_id
        assert np.array_equal(preview(editor), before_pixels)
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor._layers == changed and np.array_equal(preview(editor), changed_pixels)
        editor.openProject(str(project))
        wait_for(lambda: editor._pending_project is None and settled(editor))
        assert editor._layers == changed and editor.activeLayerId == group["id"]
    assert not warnings, warnings


def test_selected_group_chat_controls_do_not_rewrite_children_or_need_capacity(ui):
    editor, window, find, warnings = ui
    face, arm, _ = setup_layers(editor)
    gid = layers.applyGroupPlan(editor, group_plan(face, arm, opacity=1), deepcopy(editor._layers))
    wait_for(lambda: settled(editor))
    editor._layers.extend(new_layer("其他层", True) for _ in range(MAX_LAYERS - len(editor._layers)))
    editor._commit()
    editor.changed.emit()
    before = deepcopy(editor._layers)
    cursor, generation = editor._cursor, editor._generation
    group = editor._layer()
    with mock_api(response(editor._recipe, [controls(group, opacity=.8)])) as (url, requests):
        configure(editor.ai, url)
        find("descriptionInput").setProperty("text", "人像精修组整体强度80%，保留子层参数")
        click(window, find("applyDescriptionButton"))
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None and settled(editor))
        assert editor.activeLayerId == gid and group["opacity"] == .8
        assert editor._cursor == cursor + 1 and editor._generation == generation + 1
        assert len(editor._layers) == MAX_LAYERS and len(requests) == 1
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        assert context["active_is_group"] and context["max_new_layers"] == 0
        assert find("layerOpacityLabel").property("text") == "整体强度"
        assert [l for l in editor._layers if l["id"] != gid] == [l for l in before if l["id"] != gid]
        assert "整体效果强度 80%" in editor.conversation[-1]["text"]
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before and editor.activeLayerId == gid
    assert not warnings, warnings


def test_child_navigation_before_group_action_is_restored_by_undo(ui):
    editor, _, _, _ = ui
    face, arm, _ = setup_layers(editor)
    editor.selection.pickLayer(arm["id"])
    wait_for(lambda: settled(editor))
    layers.applyGroupPlan(editor, group_plan(face, arm), deepcopy(editor._layers))
    wait_for(lambda: settled(editor))
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor.activeLayerId == arm["id"] and editor.selection.pickedLayerId == arm["id"]


def test_group_controls_and_child_edits_are_atomic_and_report_hidden_parent(ui):
    editor, _, _, _ = ui
    face, arm, _ = setup_layers(editor)
    gid = layers.applyGroupPlan(editor, group_plan(face, arm, opacity=1), deepcopy(editor._layers))
    wait_for(lambda: settled(editor))
    group = editor._layer()
    before = deepcopy(editor._layers)
    edits = [controls(group, visible=False), {"layer_id": face["id"], "recipe": {**face["recipe"], "exposure": .45}}]
    with mock_api(response(editor._recipe, edits)) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("隐藏人像精修组，同时保存面部曝光0.45EV", "auto")
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None and settled(editor))
        assert len(requests) == 1 and not editor._layer()["visible"] and editor.activeLayerId == gid
        assert next(l for l in editor._layers if l["id"] == face["id"])["recipe"]["exposure"] == .45
        assert "父组“人像精修”仍隐藏" in editor.conversation[-1]["text"]
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before


def test_identity_group_preserves_pixels_and_order_even_with_interleaved_descendant_records():
    base = new_layer("基础", True)
    parent = new_layer("已有组", True, kind="group")
    child = new_layer("已有组内提亮", True, parent["id"])
    child["recipe"]["exposure"] = .4
    upper = new_layer("相邻调色", True)
    upper["recipe"]["warmth"] = 20
    records = [base, parent, child, upper]
    # The descendant record between parent and upper is not a sibling gap.
    clean = validate_group_plan(group_plan(upper, parent, opacity=1), records)
    assert clean["layer_ids"] == [parent["id"], upper["id"]]
    wrapper = new_layer("组合", True, kind="group")
    candidate = deepcopy(records)
    for layer in candidate:
        if layer["id"] in clean["layer_ids"]:
            layer["parent_id"] = wrapper["id"]
    candidate.insert(1, wrapper)
    photo = Image.fromarray(np.arange(64 * 48 * 3, dtype=np.uint8).reshape(48, 64, 3))
    assert np.array_equal(render_nodes(photo, forest(records)), render_nodes(photo, forest(candidate)))


@pytest.mark.parametrize("case", ["duplicate", "missing", "other_parent", "interleaved", "empty", "unknown_field",
                                 "long_name", "blank_name", "bad_id", "boolean_opacity", "percent_opacity",
                                 "nonfinite", "null_opacity", "wrong_visible", "null_visible", "capacity", "depth"])
def test_group_protocol_rejects_invalid_or_reordering_plans(case):
    face, arm, middle = new_layer("面部"), new_layer("手臂"), new_layer("中间")
    records = [face, arm]
    plan = group_plan(face, arm)
    if case == "duplicate":
        plan["layer_ids"] = [face["id"], face["id"]]
    elif case == "missing":
        plan["layer_ids"] = ["unknown"]
    elif case == "other_parent":
        parent = new_layer("父组", True, kind="group")
        records.append(parent)
        arm["parent_id"] = parent["id"]
    elif case == "interleaved":
        records.insert(1, middle)
    elif case == "empty":
        plan["layer_ids"] = []
    elif case == "unknown_field":
        plan["delete"] = True
    elif case == "long_name":
        plan["name"] = "组" * 81
    elif case == "blank_name":
        plan["name"] = "  "
    elif case == "bad_id":
        plan["layer_ids"] = [True]
    elif case == "boolean_opacity":
        plan["opacity"] = True
    elif case == "percent_opacity":
        plan["opacity"] = 50
    elif case == "nonfinite":
        plan["opacity"] = float("nan")
    elif case == "null_opacity":
        plan["opacity"] = None
    elif case == "wrong_visible":
        plan["visible"] = 1
    elif case == "null_visible":
        plan["visible"] = None
    elif case == "capacity":
        records.extend(new_layer("占位") for _ in range(MAX_LAYERS - len(records)))
    else:
        parent_id = ""
        for _ in range(4):
            parent = new_layer("嵌套组", True, parent_id, "group")
            records.append(parent)
            parent_id = parent["id"]
        face["parent_id"] = arm["parent_id"] = parent_id
    with pytest.raises(ValueError):
        validate_group_plan(plan, records)


@pytest.mark.parametrize("field", ["regions", "recipe", "layer_edits", "scope"])
def test_group_action_cannot_smuggle_other_changes(field):
    face, arm = new_layer("面部"), new_layer("手臂")
    data = group_response(Recipe().to_dict(), group_plan(face, arm))
    plan = json.loads(data["choices"][0]["message"]["content"])
    plan[field] = {"regions": [{}], "recipe": Recipe(exposure=.3).to_dict(),
                   "layer_edits": [controls(face, visible=False)], "scope": "regions"}[field]
    data["choices"][0]["message"]["content"] = json.dumps(plan)
    with pytest.raises(ValueError):
        parse_auto(data, Recipe().to_dict(), [], "whole_image", [face, arm])


def test_other_actions_cannot_smuggle_group_creation():
    face, arm = new_layer("面部"), new_layer("手臂")
    data = group_response(Recipe().to_dict(), group_plan(face, arm))
    plan = json.loads(data["choices"][0]["message"]["content"])
    plan.update(action="answer", scope="none")
    data["choices"][0]["message"]["content"] = json.dumps(plan)
    with pytest.raises(ValueError, match="夹带编组"):
        parse_auto(data, Recipe().to_dict(), [], "whole_image", [face, arm])


@pytest.mark.parametrize("change", ["recipe", "name", "locked", "parent", "order"])
def test_waiting_group_snapshot_cannot_overwrite_new_document_changes(ui, change):
    editor, _, _, _ = ui
    face, arm, whole = setup_layers(editor)
    expected = deepcopy(editor._layers)
    if change == "recipe":
        editor._recipe["warmth"] = 30
    elif change == "name":
        face["name"] = "手动改名"
    elif change == "locked":
        face["locked"] = ["skin_smoothing"]
    elif change == "parent":
        parent = new_layer("手动组", True, kind="group")
        editor._layers.append(parent)
        whole["parent_id"] = parent["id"]
    else:
        editor._layers[1], editor._layers[2] = editor._layers[2], editor._layers[1]
    editor._sync_layer()
    current = deepcopy(editor._layers)
    cursor = editor._cursor
    with pytest.raises(ValueError, match="等待期间"):
        layers.applyGroupPlan(editor, group_plan(face, arm), expected)
    assert editor._layers == current and editor._cursor == cursor


def test_invalid_group_controls_do_not_partially_modify_child(ui):
    editor, _, _, _ = ui
    face, arm, _ = setup_layers(editor)
    layers.applyGroupPlan(editor, group_plan(face, arm), deepcopy(editor._layers))
    wait_for(lambda: settled(editor))
    group = editor._layer()
    before = deepcopy(editor._layers)
    edits = [controls(face, visible=False), {"layer_id": group["id"], "recipe": Recipe(exposure=.2).to_dict()}]
    with pytest.raises(ValueError, match="图层组"):
        layers.applyLayerEdits(editor, edits, before)
    assert editor._layers == before
    with pytest.raises(ValueError, match="图层组"):
        validate_layer_edits(edits, before)


def test_group_control_noop_preserves_redo_and_reports_no_visual_change(ui):
    editor, _, _, _ = ui
    face, arm, _ = setup_layers(editor)
    layers.applyGroupPlan(editor, group_plan(face, arm), deepcopy(editor._layers))
    wait_for(lambda: settled(editor))
    group = editor._layer()
    editor.setOpacity(80)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    editor.undo()
    wait_for(lambda: settled(editor))
    cursor = editor._cursor
    group = editor._layer()
    with mock_api(response(editor._recipe, [controls(group, opacity=.5)])) as (url, _):
        configure(editor.ai, url)
        editor.sendMessage("人像组保持50%", "auto")
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None)
        assert editor._cursor == cursor and editor.canRedo
        assert editor.conversation[-1]["state"] == "answered"
        assert "照片未改变" in editor.conversation[-1]["text"]


def test_cancelled_group_reply_does_not_create_layers(ui):
    editor, _, _, _ = ui
    face, arm, _ = setup_layers(editor)
    before = deepcopy(editor._layers)
    with mock_api(group_response(editor._recipe, group_plan(face, arm)), delay=.25) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("把面部和手臂编组", "auto")
        wait_for(lambda: bool(requests))
        editor.ai.cancel()
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None)
        assert editor._layers == before
