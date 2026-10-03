"""Hidden parameters are saved edits; whole-photo edits need a visible target."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest
from PIL import Image
from PySide6.QtCore import QUrl

from iphoto.ai_protocol import parse_auto
from iphoto.document import new_layer
from iphoto.engine import Recipe
from iphoto.layer_tree import display_states
from test_ai import completion, configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_ai_layer_edits import edit_for, response, setup_layers, ui, click
from test_editor import settled


def preview(editor):
    with Image.open(QUrl(editor._preview).toLocalFile()) as image:
        return image.tobytes()


@pytest.mark.parametrize("reason", ["hidden", "zero_opacity"])
def test_whole_chat_repairs_inactive_adjust_and_preserves_hidden_effect(ui, reason):
    editor, window, find, warnings = ui
    _, _, whole = setup_layers(editor)
    whole["visible"] = reason != "hidden"
    whole["opacity"] = 0 if reason == "zero_opacity" else 1
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    original = deepcopy(editor._layers)
    before_pixels = preview(editor)
    bad = auto_response("adjust", [], Recipe(exposure=.6, warmth=15).to_dict(), "整张照片已提亮")
    good = auto_response("global", [], Recipe(exposure=.2).to_dict(), "已用新全图层提亮，保留之前隐藏的效果")

    def reply(payload):
        return good if "上次回复未通过程序校验" in payload["messages"][0]["content"] else bad

    with mock_api(reply) as (url, requests):
        configure(editor.ai, url)
        find("descriptionInput").setProperty("text", "整张照片提亮，其他效果保持原来的状态")
        click(window, find("applyDescriptionButton"))
        wait_for(lambda: not editor.ai.busy and len(editor._layers) == 5 and settled(editor))
        assert len(requests) == 2
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        assert context["current_scope"] == "whole_image"
        assert context["current_display_enabled"] is False
        offered = next(l for l in context["existing_layers"] if l["id"] == whole["id"])
        assert offered["display"]["enabled"] is False
        assert editor._layers[:-1] == original
        assert editor._layer()["visible"] and editor._layer()["opacity"] == 1
        assert editor._recipe["exposure"] == .2 and editor._recipe["warmth"] == 0
        assert preview(editor) != before_pixels
        assert len([m for m in editor.conversation if m["role"] == "user"]) == 1
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == original and preview(editor) == before_pixels
    assert not warnings, warnings


@pytest.mark.parametrize("scope", ["current_layer", "whole_image", "legacy"])
def test_inactive_current_layer_cannot_use_adjust_to_claim_visible_change(scope):
    current = Recipe(exposure=.3).to_dict()
    data = completion(Recipe(exposure=.5).to_dict()) if scope == "legacy" else auto_response("adjust", [], Recipe(exposure=.5).to_dict())
    if scope != "legacy":
        body = json.loads(data["choices"][0]["message"]["content"])
        body["scope"] = scope
        data["choices"][0]["message"]["content"] = json.dumps(body)
    with pytest.raises(ValueError, match="不可见|隐藏"):
        parse_auto(data, current, [], "whole_image", [], current_display_enabled=False)


@pytest.mark.parametrize("reason", ["hidden", "zero_opacity", "hidden_parent", "zero_parent"])
def test_named_parameter_edit_keeps_inactive_state_and_explains_no_photo_change(ui, reason):
    editor, _, _, warnings = ui
    face, _, _ = setup_layers(editor)
    if "parent" in reason:
        parent = new_layer("人像组", True, kind="group")
        parent["visible"] = reason != "hidden_parent"
        parent["opacity"] = 0 if reason == "zero_parent" else .5
        editor._layers.append(parent)
        face["parent_id"] = parent["id"]
    else:
        face["visible"] = reason != "hidden"
        face["opacity"] = 0 if reason == "zero_opacity" else .5
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    original = deepcopy(editor._layers)
    before_pixels = preview(editor)
    with mock_api(response(editor._recipe, [edit_for(face, skin_smoothing=15)])) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("面部磨皮再轻一点，保持显示状态，之后再看效果", "auto")
        wait_for(lambda: not editor.ai.busy and face["recipe"]["skin_smoothing"] == 15 and settled(editor))
        assert len(requests) == 1 and len(editor._layers) == len(original)
        assert {k: v for k, v in face.items() if k != "recipe"} == {k: v for k, v in original[1].items() if k != "recipe"}
        assert preview(editor) == before_pixels
        text = editor.conversation[-1]["text"]
        assert "不可见" in text and "已减轻指定图层" not in text
        assert ("父组“人像组”" in text) == ("parent" in reason)
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        offered = next(l for l in context["existing_layers"] if l["id"] == face["id"])
        assert offered["display"]["enabled"] is False
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == original
    assert not warnings, warnings


@pytest.mark.parametrize("mode", ["edit", "advice", "local"])
def test_explicit_current_layer_edit_and_advice_report_stored_hidden_parameters(ui, mode):
    editor, _, _, warnings = ui
    _, _, whole = setup_layers(editor)
    whole["visible"] = False
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    before_pixels = preview(editor)
    if mode == "local":
        editor.ai.settings = replace(editor.ai.settings, enabled=False)
        assert editor.sendMessage("提亮一点", "edit")
        wait_for(lambda: settled(editor) and editor.conversation[-1]["role"] == "assistant")
    else:
        with mock_api(completion(Recipe(exposure=.5, warmth=15).to_dict())) as (url, _):
            configure(editor.ai, url)
            editor.sendMessage("调整当前层曝光，保持隐藏", mode)
            wait_for(lambda: not editor.ai.busy and settled(editor))
            if mode == "advice":
                assert editor.conversation[-1]["state"] == "proposed"
                editor.applyAdvice(editor.conversation[-1]["id"])
                wait_for(lambda: settled(editor))
    assert not whole["visible"] and preview(editor) == before_pixels
    assert "不可见" in editor.conversation[-1]["text"]
    assert not warnings, warnings


def test_nested_display_controls_preserve_fractional_opacity_and_ignore_folding():
    outer = new_layer("外组", True, kind="group")
    inner = new_layer("内组", True, outer["id"], "group")
    leaf = new_layer("面部", True, inner["id"])
    outer["opacity"], inner["opacity"], leaf["opacity"] = .5, .5, .5
    outer["collapsed"] = True
    state = display_states([leaf, inner, outer])[leaf["id"]]
    assert state["enabled"] and state["opacity"] == .125 and not state["blockers"]
    inner["opacity"] = 0
    state = display_states([leaf, inner, outer])[leaf["id"]]
    assert not state["enabled"] and state["blockers"] == [inner["id"]]
