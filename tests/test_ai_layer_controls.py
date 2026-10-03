"""AI comparison and opacity controls preserve real effects and one-step history."""

from copy import deepcopy

import pytest
from PIL import Image
from PySide6.QtCore import QUrl

from iphoto.ai_layer_edits import validate_layer_edits
from iphoto.controllers import layers
from iphoto.document import new_layer, read_project
from test_ai import configure, mock_api, wait_for
from test_ai_layer_edits import response, setup_layers, ui, click
from test_editor import settled


def controls(layer, *, visible=None, opacity=None):
    return {"layer_id": layer["id"], "recipe": None, "visible": visible, "opacity": opacity}


def pixels(editor):
    with Image.open(QUrl(editor._preview).toLocalFile()) as photo:
        return (photo.getpixel((round(photo.width * .3), round(photo.height * .3))),
                photo.getpixel((1, 1)))


def test_chat_hides_existing_layer_without_erasing_effect_and_saves(ui, tmp_path):
    editor, window, find, warnings = ui
    face, _, _ = setup_layers(editor)
    face["locked"] = ["skin_smoothing"]
    editor._commit()
    before = deepcopy(editor._layers)
    before_pixels = pixels(editor)
    with mock_api(response(editor._recipe, [controls(face, visible=False)])) as (url, requests):
        configure(editor.ai, url)
        find("descriptionInput").setProperty("text", "隐藏面部磨皮图层，其他图层不变")
        click(window, find("applyDescriptionButton"))
        wait_for(lambda: not editor.ai.busy and not face["visible"] and settled(editor))
        assert len(requests) == 1 and len(editor._layers) == 4
        assert face["recipe"] == before[1]["recipe"] and face["mask"] == before[1]["mask"]
        assert face["locked"] == ["skin_smoothing"] and face["opacity"] == 1
        assert editor.selection.pickedLayerId == face["id"]
        assert "隐藏" in editor.conversation[-1]["text"]
        hidden_pixels = pixels(editor)
        assert before_pixels[0] != hidden_pixels[0] and before_pixels[1] == hidden_pixels[1]
        hidden = deepcopy(editor._layers)
        project = tmp_path / "hide-and-compare.iphoto"
        editor.saveProject(str(project))
        assert read_project(project)["layers"] == hidden
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before and pixels(editor) == before_pixels
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor._layers == hidden and pixels(editor) == hidden_pixels
    assert not warnings, warnings


def test_show_and_opacity_of_two_layers_are_one_transaction(ui):
    editor, _, _, warnings = ui
    face, arm, _ = setup_layers(editor)
    face["visible"] = arm["visible"] = False
    editor._commit()
    before = deepcopy(editor._layers)
    edits = [controls(face, visible=True, opacity=.5), controls(arm, visible=True, opacity=.25)]
    with mock_api(response(editor._recipe, edits)) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("恢复面部和手臂层，面部透明度50%，手臂25%，保留磨皮参数", "auto")
        wait_for(lambda: not editor.ai.busy and face["visible"] and arm["visible"] and settled(editor))
        assert len(requests) == 1 and face["opacity"] == .5 and arm["opacity"] == .25
        assert face["recipe"] == before[1]["recipe"] and arm["recipe"] == before[2]["recipe"]
        assert "50%" in editor.conversation[-1]["text"] and "25%" in editor.conversation[-1]["text"]
        changed = deepcopy(editor._layers)
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor._layers == changed
    assert not warnings, warnings


@pytest.mark.parametrize("parent_state", ["hidden", "zero_opacity"])
def test_showing_child_explains_parent_that_still_prevents_display(ui, parent_state):
    editor, _, find, warnings = ui
    face, _, _ = setup_layers(editor)
    parent = new_layer("人像组", True, kind="group")
    parent["visible"] = parent_state != "hidden"
    parent["opacity"] = 0 if parent_state == "zero_opacity" else 1
    parent["collapsed"] = True
    editor._layers.append(parent)
    face["parent_id"] = parent["id"]
    face["visible"] = False
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    before_pixels = pixels(editor)
    cursor = editor._cursor
    with mock_api(response(editor._recipe, [controls(face, visible=True)])) as (url, _):
        configure(editor.ai, url)
        editor.sendMessage("显示面部磨皮图层", "auto")
        wait_for(lambda: not editor.ai.busy and face["visible"] and settled(editor))
        text = editor.conversation[-1]["text"]
        assert "父组“人像组”" in text and "仍不可见" in text
        assert "已减轻指定图层" not in text
        assert pixels(editor) == before_pixels
        assert parent["visible"] == (parent_state != "hidden")
        assert find("layerSelect_" + face["id"]).isVisible()
        assert not parent["collapsed"] and editor._cursor == cursor + 1
    assert not warnings, warnings


def test_same_opacity_has_no_undo_step_and_reports_current_state(ui):
    editor, _, _, _ = ui
    face, _, _ = setup_layers(editor)
    cursor = editor._cursor
    with mock_api(response(editor._recipe, [controls(face, opacity=1)])) as (url, _):
        configure(editor.ai, url)
        editor.sendMessage("面部磨皮层透明度设为100%", "auto")
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "answered")
        assert editor._cursor == cursor
        assert "照片未改变" in editor.conversation[-1]["text"]
        assert "100%" in editor.conversation[-1]["text"]


@pytest.mark.parametrize("field,value", [
    ("visible", 1), ("visible", "false"), ("opacity", True),
    ("opacity", 50), ("opacity", -.1), ("opacity", float("nan")),
    ("opacity", float("inf")), ("opacity", "0.5"),
])
def test_invalid_control_value_is_rejected(field, value):
    face = new_layer("面部")
    edit = {**controls(face), field: value}
    with pytest.raises(ValueError):
        validate_layer_edits([edit], [face])


def test_control_only_compatible_response_preserves_recipe_and_opacity():
    face = new_layer("面部")
    face["recipe"]["skin_smoothing"] = 35
    # Real JSON-object provider omitted the unused recipe in a hide request.
    edit = validate_layer_edits([{"layer_id": face["id"], "visible": False}], [face])[0]
    assert edit["recipe"] == face["recipe"] and edit["opacity"] == 1
    assert edit["visible"] is False


def test_empty_control_request_is_not_an_action():
    face = new_layer("面部")
    with pytest.raises(ValueError, match="没有指定"):
        validate_layer_edits([controls(face)], [face])


def test_bad_second_control_never_partially_hides_first(ui):
    editor, _, _, _ = ui
    face, arm, _ = setup_layers(editor)
    before = deepcopy(editor._layers)
    with pytest.raises(ValueError, match="不透明度"):
        layers.applyLayerEdits(editor, [controls(face, visible=False), controls(arm, opacity=50)], before)
    assert editor._layers == before
