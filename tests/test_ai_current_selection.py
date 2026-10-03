"""Chat adopts the actual draft, with scope gates and atomic layer history."""
from copy import deepcopy
import base64
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QUrl

from iphoto.ai_protocol import parse_auto
from iphoto.document import empty_mask, new_layer, raster_mask, render_layers
from iphoto.engine import Recipe
from iphoto.segmentation.classical import bitmap_mask
from test_ai import completion, configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def selection_response(recipe=None, scope="current_selection"):
    response = auto_response("adjust", [], recipe or Recipe(exposure=.25).to_dict(), "仅调整已选范围")
    plan = json.loads(response["choices"][0]["message"]["content"])
    plan["scope"] = scope
    response["choices"][0]["message"]["content"] = json.dumps(plan)
    return response


def corrected_mask():
    pixels = Image.new("L", (300, 200), 0)
    draw = ImageDraw.Draw(pixels)
    draw.rectangle((45, 30, 220, 170), fill=255)
    draw.rectangle((100, 65, 140, 115), fill=0)
    mask = bitmap_mask(pixels, "已修正范围，保留孔洞")
    mask["feather"] = .002
    return mask


def draft(ui, source="empty"):
    ui.e.beginSelection(source)
    # A controlled correction result, not a simulated model accuracy claim.
    ui.e._set_candidate(corrected_mask())
    wait_for(lambda: settled(ui.e) and ui.w.property("selectionPreviewReady"))
    ui.w.setProperty("chatOpen", True)
    return deepcopy(ui.e._candidate)


def send(ui, text="只调整已经选好的范围"):
    ui.find("descriptionInput").setProperty("text", text)
    wait_for(lambda: ui.find("applyDescriptionButton").property("enabled"))
    ui.click("applyDescriptionButton")


def done(ui):
    wait_for(lambda: not ui.e.ai.busy and ui.e._pending_request is None and settled(ui.e))


def test_selection_protocol_preserves_bound_locks_and_requires_explicit_scope():
    current = Recipe(exposure=.4, skin_smoothing=25).to_dict()
    result = parse_auto(selection_response(Recipe(exposure=.8, warmth=12).to_dict()), current, ["exposure"], "selection")
    assert result["scope"] == "current_selection"
    assert result["recipe"]["exposure"] == .4 and result["recipe"]["warmth"] == 12


@pytest.mark.parametrize("response", [
    selection_response(scope="current_layer"), selection_response(scope="whole_image"),
    auto_response("global", [], Recipe(exposure=.2).to_dict()),
    auto_response(), auto_response("update_layers", []),
    completion(), selection_response({**Recipe().to_dict(), "sharpness": 101}),
])
def test_selection_protocol_rejects_broader_or_invalid_actions(response):
    with pytest.raises(ValueError):
        parse_auto(response, Recipe().to_dict(), [], "selection")


def test_selection_scope_requires_a_real_draft():
    with pytest.raises(ValueError, match="没有待应用"):
        parse_auto(selection_response(), Recipe().to_dict(), [], "local")


def test_chat_new_range_keeps_exact_hole_and_uses_independent_recipe(canvas):  # noqa: F811
    ui, editor = canvas, canvas.e
    editor.setParameter("exposure", .4)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    before, cursor = deepcopy(editor._layers), editor._cursor
    mask = draft(ui)
    editor._message("assistant", "已选好范围", state="draft")
    draft_message = editor.conversation[-1]["id"]
    with mock_api(selection_response()) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        done(ui)
        assert len(requests) == 1
        content = requests[0][2]["messages"][1]["content"]
        context = json.loads(content[0]["text"])
        assert context["current_scope"] == "selection" and context["selection_output"] == "new_layer"
        assert context["current_recipe"] == Recipe().to_dict() and context["locked"] == []
        assert len(content) == 4
        with Image.open(BytesIO(base64.b64decode(content[3]["image_url"]["url"].split(",", 1)[1]))) as sent_mask:
            assert sent_mask.tobytes() == raster_mask(mask, sent_mask.size).tobytes()
        assert editor._layers[:-1] == before and editor._layer()["mask"] == mask
        assert editor.parameters == Recipe(exposure=.25).to_dict() and editor._layer()["locked"] == []
        assert not editor.hasSelectionDraft and editor._cursor == cursor + 1
        assert editor.conversationMessageState(draft_message) == "confirmed"
        assert editor.conversation[-1]["layer_id"] == editor.activeLayerId
        assert editor.selection.pickedLayerId == editor.activeLayerId
        assert not ui.find("selectionToLayerButton").isVisible()
        # The protected hole and background have exactly their prior pixels.
        output = Image.open(QUrl(editor.previewUrl).toLocalFile()).convert("RGB")
        reference = render_layers(Image.new("RGB", output.size, (74, 123, 142)), before)
        for fraction in ((.02, .02), (.4, .45)):
            x, y = int(output.width*fraction[0]), int(output.height*fraction[1])
            assert output.getpixel((x, y)) == reference.getpixel((x, y))
        x, y = int(output.width*.6), int(output.height*.7)
        assert output.getpixel((x, y)) != reference.getpixel((x, y))
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor._layer()["mask"] == mask


def test_chat_bound_range_saves_mask_and_recipe_once_inside_group_at_capacity(canvas):  # noqa: F811
    ui, editor = canvas, canvas.e
    lid = editor.activeLayerId
    editor.setParameter("exposure", .4)
    editor.finishGesture()
    editor.groupLayer()
    wait_for(lambda: settled(editor))
    editor.selectLayer(lid)
    wait_for(lambda: settled(editor))
    editor._layers.extend(new_layer(f"已有层 {i}", True) for i in range(30))
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    before, cursor = deepcopy(editor._layers), editor._cursor
    ui.click("layerMaskThumb_" + lid)
    wait_for(lambda: editor.hasSelectionDraft and settled(editor))
    editor._set_candidate(corrected_mask())
    wait_for(lambda: settled(editor))
    mask = deepcopy(editor._candidate)
    ui.w.setProperty("chatOpen", True)
    recipe = Recipe(exposure=.8, warmth=12).to_dict()
    with mock_api(selection_response(recipe)) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        done(ui)
        assert len(editor._layers) == 32 and editor.activeLayerId == lid
        assert editor._layer()["mask"] == mask and editor._cursor == cursor + 1
        assert editor.parameters["exposure"] == .4 and editor.parameters["warmth"] == 12
        assert editor._layer()["locked"] == ["exposure"] and editor.activeParentId
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        assert context["selection_output"] == "replace_mask" and context["selection_layer_id"] == lid
        assert context["max_new_layers"] == 0 and context["locked"] == ["exposure"]
        assert all(layer == old for layer, old in zip(editor._layers, before) if layer["id"] != lid)
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before


def test_cancel_from_task_bar_preserves_range_and_draft_undo(canvas):  # noqa: F811
    ui, editor = canvas, canvas.e
    mask = draft(ui)
    before, cursor = deepcopy(editor._layers), editor._cursor
    history, draft_cursor = deepcopy(editor._draft_history), editor._draft_cursor
    with mock_api(selection_response(), delay=4) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        wait_for(lambda: editor.ai.busy and bool(requests))
        assert ui.find("aiRequestProgress").isVisible()
        assert "等待" in ui.find("aiRequestProgressText").property("text")
        assert not ui.find("selectionToLayerButton").property("enabled")
        ui.click("chatCollapseButton")
        assert ui.find("cancelAiRequest").isVisible()
        ui.click("cancelAiRequest")
        done(ui)
        assert editor._candidate == mask and editor._layers == before and editor._cursor == cursor
        assert editor._draft_history == history and editor._draft_cursor == draft_cursor
        assert editor.conversation[-1]["state"] == "failed" and "取消" in editor.status
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._candidate == empty_mask() and editor._layers == before


@pytest.mark.parametrize("failure", ["bad_scope", "no_effect", "http_error"])
def test_bad_response_keeps_draft_and_can_retry_from_chat(canvas, failure):  # noqa: F811
    ui, editor = canvas, canvas.e
    mask = draft(ui)
    before, cursor = deepcopy(editor._layers), editor._cursor
    response = selection_response(scope="whole_image") if failure == "bad_scope" else selection_response(Recipe().to_dict())
    with mock_api(response, status=401 if failure == "http_error" else 200) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        done(ui)
        assert len(requests) == (2 if failure == "bad_scope" else 1)
        assert editor._candidate == mask and editor._layers == before and editor._cursor == cursor
        assert editor.conversation[-1]["state"] == "failed"
    with mock_api(selection_response()) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        done(ui)
        assert editor._layer()["mask"] == mask and editor._cursor == cursor + 1


def test_auto_answer_preserves_range_and_preview_modes_are_disabled(canvas):  # noqa: F811
    ui, editor = canvas, canvas.e
    mask = draft(ui)
    before, cursor = deepcopy(editor._layers), editor._cursor
    with mock_api(auto_response("answer", [], Recipe().to_dict(), "建议轻微提亮此范围")) as (url, requests):
        configure(editor.ai, url)
        ui.find("descriptionInput").setProperty("text", "这个范围该怎么调")
        for mode in (1, 2):
            ui.find("chatModeBox").setProperty("currentIndex", mode)
            wait_for(lambda: not ui.find("applyDescriptionButton").property("enabled"))
            assert not editor.sendMessage("请预览", "regions" if mode == 2 else "advice")
        assert not requests
        ui.find("chatModeBox").setProperty("currentIndex", 0)
        send(ui, "仅给这个范围一些建议")
        done(ui)
        assert editor.conversation[-1]["state"] == "answered"
        assert editor._candidate == mask and editor._layers == before and editor._cursor == cursor


@pytest.mark.parametrize("context", ["hidden_group", "hidden_layer"])
def test_new_range_creates_visible_root_layer_from_hidden_target(canvas, context):  # noqa: F811
    ui, editor = canvas, canvas.e
    if context == "hidden_group":
        editor.groupLayer()
        wait_for(lambda: settled(editor))
    editor.toggleLayer(editor.activeLayerId)
    wait_for(lambda: settled(editor))
    before, cursor = deepcopy(editor._layers), editor._cursor
    mask = draft(ui)
    with mock_api(selection_response()) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        done(ui)
        assert editor._layers[:-1] == before and editor._layer()["mask"] == mask
        assert editor._layer()["parent_id"] == "" and editor._layer()["visible"]
        assert editor._cursor == cursor + 1
        context = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
        assert context["current_display_enabled"] is True and context["selection_output"] == "new_layer"


def test_bound_hidden_layer_is_not_reported_as_applied(canvas):  # noqa: F811
    ui, editor = canvas, canvas.e
    editor.toggleLayer(editor.activeLayerId)
    wait_for(lambda: settled(editor))
    before, cursor = deepcopy(editor._layers), editor._cursor
    mask = draft(ui, "current")
    with mock_api(selection_response()) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        done(ui)
        assert len(requests) == 2 and editor.conversation[-1]["state"] == "failed"
        assert editor._candidate == mask and editor._layers == before and editor._cursor == cursor


@pytest.mark.parametrize("stale", ["mask", "locks"])
def test_range_or_lock_changes_reject_reply_even_without_generation_change(canvas, stale):  # noqa: F811
    ui, editor = canvas, canvas.e
    mask = draft(ui)
    cursor = editor._cursor
    with mock_api(selection_response(), delay=.4) as (url, requests):
        configure(editor.ai, url)
        send(ui)
        wait_for(lambda: editor.ai.busy and bool(requests))
        if stale == "mask":
            editor._candidate["feather"] = .01
        else:
            editor._locked.add("warmth")
        done(ui)
        assert len(editor._layers) == 1 and editor._cursor == cursor
        assert editor.hasSelectionDraft and editor.conversation[-1]["state"] == "failed"
        if stale == "mask":
            assert editor._candidate["bitmap"] == mask["bitmap"] and editor._candidate["feather"] == .01
        else:
            assert editor._candidate == mask and "warmth" in editor._locked


def test_new_range_at_capacity_and_empty_range_make_no_request(canvas):  # noqa: F811
    editor = canvas.e
    with mock_api(selection_response()) as (url, requests):
        configure(editor.ai, url)
        editor.beginSelection()
        wait_for(lambda: settled(editor))
        assert not editor.sendMessage("提亮", "auto") and not requests
        editor._layers.extend(new_layer(f"已有层 {i}", True) for i in range(31))
        editor._set_candidate(corrected_mask())
        wait_for(lambda: settled(editor))
        mask, before = deepcopy(editor._candidate), deepcopy(editor._layers)
        assert not editor.sendMessage("提亮", "auto") and not requests
        assert editor._layers == before and editor._candidate == mask
        assert "图层已满" in editor.status
