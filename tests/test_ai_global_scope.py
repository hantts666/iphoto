"""Whole-image chat edits must preserve existing local work and escape groups."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QUrl

from iphoto.ai_protocol import parse_auto
from iphoto.document import MAX_LAYERS, empty_mask, new_layer, raster_mask, read_project
from iphoto.controllers.conversation import _current_scope
from iphoto.masks import encode_bitmap
from iphoto.engine import Recipe
from test_ai import completion, configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_editor import settled
from test_selection_ui_modes import ui, click


def local_layer(editor):
    editor.drawDraft("rect", "replace", [[0.25, 0.2], [0.7, 0.8]], 0)
    wait_for(lambda: settled(editor))
    editor.selection.apply("new_layer")
    editor.renameLayer("面部磨皮")
    editor.setParameter("skin_smoothing", 30)
    editor.setParameter("exposure", .15)
    editor.finishGesture()
    wait_for(lambda: settled(editor))


def test_global_chat_button_preserves_local_layers_and_one_undo(ui, tmp_path):
    editor, window, find, warnings = ui
    local_layer(editor)
    before = deepcopy(editor._layers)
    local_id = editor.activeLayerId
    before_pixel = Image.open(QUrl(editor._preview).toLocalFile()).getpixel((0, 0))
    with mock_api(auto_response("global", [], Recipe(exposure=.25).to_dict(), "已建立全图提亮层，保留面部磨皮")) as (url, requests):
        configure(editor.ai, url)
        find("descriptionInput").setProperty("text", "整张照片提亮一点，保留磨皮")
        click(window, find("applyDescriptionButton"))
        wait_for(lambda: len(editor.layers) == len(before) + 1 and settled(editor))
        assert len(requests) == 1
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        assert context["current_scope"] == "local"
        assert editor._layers[:-1] == before
        full = editor._layer()
        assert full["parent_id"] == "" and full["recipe"]["exposure"] == .25
        assert full["recipe"]["skin_smoothing"] == 0 and full["locked"] == []
        assert raster_mask(full["mask"], (300, 200)).getextrema() == (255, 255)
        assert Image.open(QUrl(editor._preview).toLocalFile()).getpixel((0, 0)) != before_pixel
        assert editor.selection.pickedLayerId == full["id"]
        assert find("parameter_exposure").property("visible")
        assert editor.conversation[-1]["layer_id"] == full["id"]
        assert not editor.hasRegionDraft and not editor.hasSelectionDraft
        project = tmp_path / "whole-photo.iphoto"
        editor.saveProject(str(project))
        saved = read_project(project)
        assert saved["layers"][:-1] == before
        assert saved["layers"][-1] == full
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before and editor.activeLayerId == local_id
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor._layers[-1] == full
    assert not warnings, warnings


def test_full_adjust_on_local_layer_is_repaired_before_any_edit(ui):
    editor, _, _, warnings = ui
    local_layer(editor)
    before = deepcopy(editor._layers)
    bad = auto_response("adjust", [], Recipe(exposure=.4).to_dict(), "全图已提亮")
    body = json.loads(bad["choices"][0]["message"]["content"])
    body["scope"] = "whole_image"
    bad["choices"][0]["message"]["content"] = json.dumps(body)
    good = auto_response("global", [], Recipe(exposure=.25).to_dict(), "已通过全图层提亮")

    def reply(payload):
        return good if "上次回复未通过程序校验" in payload["messages"][0]["content"] else bad

    with mock_api(reply) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("整张照片提亮，保留磨皮", "auto")
        wait_for(lambda: len(editor.layers) == len(before) + 1 and settled(editor))
        assert len(requests) == 2
        assert "global" in requests[1][2]["messages"][0]["content"]
        assert editor._layers[:-1] == before
        assert len([m for m in editor.conversation if m["role"] == "user"]) == 1
    assert not warnings, warnings


def test_legacy_scope_free_local_reply_cannot_erase_skin_smoothing(ui):
    editor, _, _, warnings = ui
    local_layer(editor)
    editor.unlock("skin_smoothing")
    before = deepcopy(editor._layers)
    with mock_api(completion(Recipe(exposure=.4).to_dict(), summary="全图已提亮，磨皮保留")) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("整张照片提亮，保留磨皮", "auto")
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "failed")
        assert len(requests) == 2
        assert editor._layers == before
        assert editor._recipe["skin_smoothing"] == 30
    assert not warnings, warnings


def test_global_layer_escapes_current_clipped_group(ui):
    editor, _, _, warnings = ui
    local_layer(editor)
    editor.groupLayer()
    assert editor.activeIsGroup
    editor.drawDraft("rect", "replace", [[0.25, 0.2], [0.7, 0.8]], 0)
    editor.selection.apply("replace_mask")
    wait_for(lambda: settled(editor))
    before = deepcopy(editor._layers)
    corner = Image.open(QUrl(editor._preview).toLocalFile()).getpixel((0, 0))
    with mock_api(auto_response("global", [], Recipe(exposure=.25).to_dict(), "统一提亮整张照片")) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("整张照片提亮", "auto")
        wait_for(lambda: len(editor.layers) == len(before) + 1 and settled(editor))
        assert editor._layers[:-1] == before
        assert editor.activeParentId == "" and not editor.activeIsGroup
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        assert context["current_scope"] == "group"
        assert Image.open(QUrl(editor._preview).toLocalFile()).getpixel((0, 0)) != corner
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before
    assert not warnings, warnings


def test_full_capacity_does_not_fall_back_to_local_adjustment(ui):
    editor, _, _, warnings = ui
    local_layer(editor)
    editor._layers.extend(new_layer("占位", True) for _ in range(MAX_LAYERS - len(editor._layers)))
    editor._commit()
    editor.changed.emit()
    before = deepcopy(editor._layers)
    with mock_api(auto_response("global", [], Recipe(exposure=.25).to_dict(), "统一提亮")) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("整张照片提亮", "auto")
        wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "failed")
        assert editor._layers == before
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        assert context["max_new_layers"] == 0
        assert editor._recipe["skin_smoothing"] == 30
    assert not warnings, warnings


def test_scope_uses_mask_pixels_not_the_mask_label(ui):
    editor, _, _, warnings = ui
    local_layer(editor)
    editor._layer()["mask"]["label"] = "全图"
    editor.changed.emit()
    recipe = {**editor._recipe, "warmth": 12}
    with mock_api(auto_response("adjust", [], recipe, "仅调整肤色，全图其他区域保留")) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("肤色暖一点", "auto")
        wait_for(lambda: not editor.ai.busy and editor.parameters["warmth"] == 12)
        payload = requests[0][2]["messages"][1]["content"]
        context = json.loads(payload[0]["text"])
        assert context["current_scope"] == "local"
        assert len(payload) == 4  # Original photo plus the real local mask.
        assert len(editor.layers) == 2 and editor._recipe["skin_smoothing"] == 30
    assert not warnings, warnings


def test_global_recipe_has_its_own_parameters_and_ignores_local_locks():
    current = Recipe(exposure=.15, skin_smoothing=30).to_dict()
    result = parse_auto(auto_response("global", [], Recipe(exposure=.25).to_dict()), current, ["exposure", "skin_smoothing"], "local")
    assert result["recipe"]["exposure"] == .25
    assert result["recipe"]["skin_smoothing"] == 0


def test_full_scope_does_not_lose_tiny_protected_bitmap_holes():
    pixels = Image.new("L", (1024, 1024), 255)
    pixels.putpixel((1, 1), 0)
    mask = {**empty_mask(True), "bitmap": encode_bitmap(pixels)}
    assert raster_mask(mask, (256, 256)).getextrema() == (255, 255)
    owner = SimpleNamespace(activeIsGroup=False, activeParentId="", _layer=lambda: {"mask": mask})
    owner.hasSelectionDraft = False
    assert _current_scope(owner) == "local"


@pytest.mark.parametrize("recipe", [Recipe().to_dict(), Recipe(skin_smoothing=30).to_dict(), {**Recipe().to_dict(), "exposure": 25}])
def test_global_rejects_empty_skin_and_out_of_range_recipe(recipe):
    with pytest.raises(ValueError):
        parse_auto(auto_response("global", [], recipe), Recipe().to_dict(), [], "local")


@pytest.mark.parametrize("action,scope", [("global", "current_layer"), ("layers", "whole_image"), ("answer", "current_layer"), ("adjust", "regions")])
def test_action_scope_mismatch_is_rejected(action, scope):
    response = auto_response(action)
    body = json.loads(response["choices"][0]["message"]["content"])
    body["scope"] = scope
    response["choices"][0]["message"]["content"] = json.dumps(body)
    with pytest.raises(ValueError):
        parse_auto(response, Recipe().to_dict(), [], "local")
