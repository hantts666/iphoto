"""Chat can modify named existing layers atomically, without duplicate effects."""

from copy import deepcopy
import json

import pytest

from iphoto.ai_protocol import parse_auto
from iphoto.ai_layer_edits import validate_layer_edits
from iphoto.controllers import layers
from iphoto.document import MAX_LAYERS, new_layer, read_project
from iphoto.engine import Recipe
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_editor import settled
from test_selection_ui_modes import ui, click


def response(current, edits):
    body = auto_response("update_layers", [], current, "已减轻指定图层的磨皮，保留提亮和调色")
    plan = json.loads(body["choices"][0]["message"]["content"])
    plan["layer_edits"] = edits
    body["choices"][0]["message"]["content"] = json.dumps(plan)
    return body


def setup_layers(editor):
    face = new_layer("面部磨皮")
    face["mask"]["ops"] = [{"kind": "rect", "mode": "add", "points": [[.2, .2], [.45, .5]]}]
    face["recipe"] = Recipe(exposure=.15, skin_smoothing=30).to_dict()
    arm = new_layer("手臂磨皮")
    arm["mask"]["ops"] = [{"kind": "rect", "mode": "add", "points": [[.5, .4], [.75, .8]]}]
    arm["recipe"] = Recipe(skin_smoothing=25).to_dict()
    whole = new_layer("全图调色", True)
    whole["recipe"] = Recipe(exposure=.3, warmth=15).to_dict()
    editor._layers.extend([face, arm, whole])
    editor._selected = whole["id"]
    editor._load_layer()
    editor._commit()
    editor._change()
    editor.selection.pickLayer(whole["id"])
    wait_for(lambda: settled(editor))
    return face, arm, whole


def edit_for(layer, **changes):
    return {"layer_id": layer["id"], "recipe": {**layer["recipe"], **changes}}


def test_chat_named_face_edits_original_and_focuses_it(ui, tmp_path):
    editor, window, find, warnings = ui
    face, _, whole = setup_layers(editor)
    original = deepcopy(editor._layers)
    edits = [edit_for(face, skin_smoothing=15)]
    with mock_api(response(editor._recipe, edits)) as (url, requests):
        configure(editor.ai, url)
        find("descriptionInput").setProperty("text", "面部磨皮轻一点，保留曝光与全图调色")
        click(window, find("applyDescriptionButton"))
        wait_for(lambda: not editor.ai.busy and editor.activeLayerId == face["id"] and settled(editor))
        assert len(requests) == 1 and len(editor.layers) == 4
        assert face["recipe"]["skin_smoothing"] == 15 and face["recipe"]["exposure"] == .15
        assert editor.selection.pickedLayerId == face["id"]
        assert find("parameter_exposure").property("visible")
        assert [l for l in editor._layers if l["id"] != face["id"]] == [l for l in original if l["id"] != face["id"]]
        assert face["mask"] == original[1]["mask"]
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        offered = next(l for l in context["existing_layers"] if l["id"] == face["id"])
        assert offered["recipe"]["skin_smoothing"] == 30 and offered["locked"] == []
        assert editor.conversation[-1]["layer_id"] == face["id"]
        project = tmp_path / "named-target.iphoto"
        editor.saveProject(str(project))
        assert read_project(project)["layers"][1]["recipe"]["skin_smoothing"] == 15
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == original and editor.activeLayerId == whole["id"]
    assert not warnings, warnings


def test_two_targets_are_one_transaction_and_work_at_layer_capacity(ui):
    editor, _, _, warnings = ui
    face, arm, _ = setup_layers(editor)
    editor._layers.extend(new_layer("已有层", True) for _ in range(MAX_LAYERS - len(editor._layers)))
    editor._commit()
    editor.changed.emit()
    before = deepcopy(editor._layers)
    with mock_api(response(editor._recipe, [edit_for(face, skin_smoothing=15), edit_for(arm, skin_smoothing=12)])) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("面部和手臂的磨皮都轻一点", "auto")
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "applied" and settled(editor))
        assert len(editor.layers) == MAX_LAYERS and len(requests) == 1
        assert face["recipe"]["skin_smoothing"] == 15 and arm["recipe"]["skin_smoothing"] == 12
        changed = deepcopy(editor._layers)
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor._layers == changed
    assert not warnings, warnings


def test_locked_target_keeps_value_and_does_not_claim_it_was_modified(ui):
    editor, _, _, warnings = ui
    face, _, _ = setup_layers(editor)
    face["locked"] = ["skin_smoothing"]
    editor._commit()
    before = deepcopy(editor._layers)
    cursor = editor._cursor
    with mock_api(response(editor._recipe, [edit_for(face, skin_smoothing=15)])) as (url, _):
        configure(editor.ai, url)
        editor.sendMessage("面部磨皮轻一点", "auto")
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "answered" and settled(editor))
        assert editor._layers == before and editor._cursor == cursor
        assert editor.activeLayerId == face["id"]
        assert "照片未改变" in editor.conversation[-1]["text"]
        assert "磨皮" in editor.conversation[-1]["text"]
        assert editor._recipe["skin_smoothing"] == 30
    assert not warnings, warnings


def test_partial_locked_batch_reports_actual_changes(ui):
    editor, _, _, warnings = ui
    face, arm, _ = setup_layers(editor)
    face["locked"] = ["skin_smoothing"]
    with mock_api(response(editor._recipe, [edit_for(face, skin_smoothing=15), edit_for(arm, skin_smoothing=12)])) as (url, _):
        configure(editor.ai, url)
        editor.sendMessage("面部和手臂磨皮轻一点", "auto")
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "applied")
        assert face["recipe"]["skin_smoothing"] == 30 and arm["recipe"]["skin_smoothing"] == 12
        message = editor.conversation[-1]["text"]
        assert "已调整已有图层：手臂磨皮" in message
        assert "保留手动锁定：面部磨皮" in message
        assert "已减轻指定图层" not in message
    assert not warnings, warnings


def test_invalid_second_target_never_changes_first(ui):
    editor, _, _, warnings = ui
    face, _, _ = setup_layers(editor)
    before = deepcopy(editor._layers)
    bad = [edit_for(face, skin_smoothing=15), {"layer_id": "unknown", "recipe": Recipe().to_dict()}]
    with mock_api(response(editor._recipe, bad)) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("面部和手臂轻磨皮", "auto")
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "failed")
        assert len(requests) == 2 and editor._layers == before
    assert not warnings, warnings


def test_target_changed_during_network_wait_rejects_entire_batch(ui):
    editor, _, _, warnings = ui
    face, arm, _ = setup_layers(editor)
    original_face = deepcopy(face)
    with mock_api(response(editor._recipe, [edit_for(face, skin_smoothing=15), edit_for(arm, skin_smoothing=12)]), delay=.3) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("面部和手臂轻磨皮", "auto")
        wait_for(lambda: bool(requests))
        # Simulate a concurrent metadata change outside the UI's busy guard.
        arm["locked"] = ["skin_smoothing"]
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "failed")
        assert face == original_face and arm["recipe"]["skin_smoothing"] == 25
        assert editor._pending_request is None
    assert not warnings, warnings


def test_document_change_with_same_generation_is_not_applied(ui):
    editor, _, _, warnings = ui
    face, _, whole = setup_layers(editor)
    with mock_api(response(editor._recipe, [edit_for(face, skin_smoothing=15)]), delay=.3) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("面部轻磨皮", "auto")
        wait_for(lambda: bool(requests))
        editor._recipe["warmth"] = 25
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "failed")
        assert face["recipe"]["skin_smoothing"] == 30
        assert whole["recipe"]["warmth"] == 25
    assert not warnings, warnings


@pytest.mark.parametrize("case", ["duplicate", "missing", "group", "range", "partial", "empty", "five"])
def test_layer_edit_validation_is_bounded_and_uses_offered_ids(case):
    face = new_layer("面部")
    group = new_layer("组", kind="group")
    value = [edit_for(face, skin_smoothing=15)]
    if case == "duplicate":
        value *= 2
    elif case == "missing":
        value[0]["layer_id"] = "not-offered"
    elif case == "group":
        value[0]["layer_id"] = group["id"]
    elif case == "range":
        value[0]["recipe"]["sharpness"] = 150
    elif case == "partial":
        value[0]["recipe"] = {"skin_smoothing": 15}
    elif case == "empty":
        value = []
    else:
        value *= 5
    with pytest.raises(ValueError):
        validate_layer_edits(value, [face, group])


def test_other_action_cannot_sneak_in_existing_layer_edits():
    face = new_layer("面部")
    data = auto_response("global", [], Recipe(exposure=.3).to_dict())
    plan = json.loads(data["choices"][0]["message"]["content"])
    plan["layer_edits"] = [edit_for(face, skin_smoothing=15)]
    data["choices"][0]["message"]["content"] = json.dumps(plan)
    with pytest.raises(ValueError, match="不能包含已有图层修改"):
        parse_auto(data, Recipe().to_dict(), [], "whole_image", [face])


def test_direct_document_action_prepares_every_recipe_before_committing(ui):
    editor, _, _, _ = ui
    face, arm, _ = setup_layers(editor)
    original = deepcopy(editor._layers)
    edits = [edit_for(face, skin_smoothing=15), edit_for(arm, sharpness=150)]
    with pytest.raises(ValueError):
        layers.applyLayerEdits(editor, edits, original)
    assert editor._layers == original
