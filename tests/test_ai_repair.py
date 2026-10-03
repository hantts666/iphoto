"""AI spot repair uses close-up coordinates and one document transaction."""

import base64
from copy import deepcopy
from io import BytesIO
import json
import time

import numpy as np
from PIL import Image, ImageDraw
import pytest
from PySide6.QtCore import QUrl, Qt
from PySide6.QtTest import QTest

from iphoto.ai_protocol import build_payload, parse_auto
from iphoto.ai_repair import (validate_repairs, repair_context, validate_spots,
                              parse_repair_spots, map_repair_spots)
from iphoto.ai_settings import AISettings
from iphoto.controllers import heal
from iphoto.controllers import conversation
from iphoto.document import MAX_LAYERS, new_layer, read_project, render_nodes, raster_mask
from iphoto.engine import Recipe
from iphoto.layer_tree import forest
from test_ai import configure, mock_api, wait_for
from test_ai_layer_edits import setup_layers, click
from test_editor import settled
from test_selection_ui_modes import ui as shared_ui

ui = shared_ui
PARTS = [{"name": "面部修复", "reason": "小瑕疵", "box": [200, 200, 450, 500]},
         {"name": "衣服修复", "reason": "小污点", "box": [550, 550, 800, 850]}]


def response(plan):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan)}}]}


def auto(current, parts=None):
    return response({"action": "repair", "scope": "regions", "summary": "将检查局部瑕疵",
                     "recipe": dict(current), "regions": [], "layer_edits": [], "group": None,
                     "repairs": PARTS[:1] if parts is None else parts})


def spots(*, status="planned", radius=20, summary="已定位小污点"):
    return response({"status": status, "summary": summary,
                     "spots": [{"point": [500, 500], "radius": radius, "reason": "小污点"}]
                     if status == "planned" else []})


def sent(payload):
    return json.loads(payload["messages"][1]["content"][0]["text"])


def repair_reply(current):
    return lambda payload: auto(current) if sent(payload)["mode"] == "auto" else spots()


def mapped(part=0, size=(800, 600)):
    region = validate_repairs([PARTS[part]])[0]
    context = repair_context(region, size)
    result = parse_repair_spots(spots(), context)
    return map_repair_spots(result, context, size, region)


def test_repair_auto_action_cannot_change_other_effects():
    current = Recipe(exposure=.3, skin_smoothing=15).to_dict()
    result = parse_auto(auto(current), current, [])
    assert result["action"] == "repair" and result["status"] == "planned"
    assert result["repairs"][0]["box"] == pytest.approx([v / 999 for v in PARTS[0]["box"]])
    for key, value in [("recipe", Recipe().to_dict()), ("regions", [PARTS[0]]),
                       ("layer_edits", [{}]), ("group", {})]:
        body = json.loads(auto(current)["choices"][0]["message"]["content"])
        body[key] = value
        with pytest.raises(ValueError):
            parse_auto(response(body), current, [])


@pytest.mark.parametrize("box", [[0, 0, 999, 999], [200, 200, 201, 210],
                                  [450, 200, 200, 500], [-1, 200, 450, 500],
                                  [True, 200, 450, 500], [200, 200, float('nan'), 500]])
def test_repair_region_requires_a_bounded_local_part(box):
    with pytest.raises(ValueError):
        validate_repairs([{**PARTS[0], "box": box}])


@pytest.mark.parametrize("size", [(4016, 6016), (6016, 4016), (800, 600)])
def test_local_coordinates_use_the_actual_rounded_crop(size):
    from iphoto.ai_grounding import crop_pixels

    region = validate_repairs([PARTS[0]])[0]
    context = repair_context(region, size)
    result = parse_repair_spots(spots(), context)
    repair = map_repair_spots(result, context, size, region)
    left, top, right, bottom = crop_pixels(context["_image_crop"], size)
    op = repair["heal"]["ops"][0]
    assert op["points"][0] == pytest.approx(
        [(left + 500 / 999 * (right-left)) / size[0],
         (top + 500 / 999 * (bottom-top)) / size[1]])
    effective_radius = min(30 + 2 * 999 / min(right-left, bottom-top), 40, context["radius_bounds"][1])
    assert op["radius"] * min(size) == pytest.approx(effective_radius / 999 * min(right-left, bottom-top))
    assert repair["mask"]["ops"][0] == {**op, "kind": "brush", "mode": "add"}


@pytest.mark.parametrize("change", [{"point": [-1, 500]}, {"point": [True, 500]},
                                     {"point": [0, 500]}, {"radius": True},
                                     {"radius": 0}, {"radius": 41},
                                     {"radius": float('nan')}, {"reason": ""}, {"extra": 1}])
def test_spots_reject_outside_coordinates_and_oversized_brushes(change):
    with pytest.raises(ValueError):
        validate_spots([{**{"point": [500, 500], "radius": 20, "reason": "污点"}, **change}])


def test_spots_respect_rectangle_aspect_ratio_allowed_part_and_area():
    context = {"crop_size": [2000, 100], "allowed_box": [0, 100, 999, 900],
               "radius_bounds": [2, 30]}
    assert validate_spots([{"point": [3, 500], "radius": 20, "reason": "边缘内"}], context)
    with pytest.raises(ValueError, match="超出"):
        validate_spots([{"point": [500, 101], "radius": 20, "reason": "边缘外"}], context)
    with pytest.raises(ValueError, match="重叠"):
        validate_spots([{"point": [500, 500], "radius": 20, "reason": "污点"}] * 2)
    with pytest.raises(ValueError, match="面积"):
        validate_spots([{"point": [x, y], "radius": 40, "reason": "污点"}
                        for x in (200, 400, 600, 800) for y in (300, 700)])


def test_unsupported_result_must_not_contain_hidden_strokes():
    body = json.loads(spots()["choices"][0]["message"]["content"])
    body["status"] = "unsupported"
    with pytest.raises(ValueError):
        parse_repair_spots(response(body))
    assert parse_repair_spots(spots(status="unsupported"))["status"] == "unsupported"


def test_closeup_payload_contains_one_image_and_only_local_geometry():
    context = repair_context(validate_repairs([PARTS[0]])[0], (4016, 6016))
    context.update(selection_image="mask", recent_conversation=[{"text": "old"}])
    payload = build_payload(AISettings(provider="openai"), "修复污点", Recipe().to_dict(), [],
                            "data:image/jpeg;base64,closeup", "repair", context)
    assert set(sent(payload)) == {"mode", "request", "coordinate_system", "crop_size", "allowed_box", "radius_bounds"}
    assert len(payload["messages"][1]["content"]) == 2
    assert payload["response_format"]["json_schema"]["schema"]["properties"]["spots"]["maxItems"] == 8


def test_small_repair_strokes_share_the_document_minimum_and_round_trip(ui, tmp_path):
    editor, _, _, _ = ui
    failures = []
    editor.notification.connect(lambda message, error: failures.append(message) if error else None)
    region = validate_repairs([PARTS[0]])[0]
    context = repair_context(region, (300, 200))
    part = map_repair_spots(parse_repair_spots(spots(radius=context["radius_bounds"][0]), context),
                            context, (300, 200), region)
    radius = .001
    part["heal"]["ops"][0]["radius"] = radius
    part["mask"]["ops"][0]["radius"] = radius
    assert .001 <= radius < .003
    heal.applyAIRepairs(editor, [part], deepcopy(editor._layers))
    wait_for(lambda: settled(editor))
    assert not failures, failures
    project = tmp_path / "small-repair.iphoto"
    editor.saveProject(str(project))
    assert read_project(project)["layers"][-1]["heal"]["ops"][0]["radius"] == radius


def test_two_closeups_create_root_layers_atomically_with_undo_save_and_real_pixels(ui, tmp_path):
    editor, window, find, warnings = ui
    photo = tmp_path / "marked.png"
    data = np.zeros((600, 800, 3), dtype=np.uint8)
    data[:, :, 0] = np.arange(800, dtype=np.uint16)[None, :] // 5 + 50
    data[:, :, 1] = 130
    data[:, :, 2] = 105
    image = Image.fromarray(data)
    for part in range(2):
        x, y = mapped(part)["heal"]["ops"][0]["points"][0]
        x, y = x * 800, y * 600
        ImageDraw.Draw(image).ellipse((x-3, y-3, x+3, y+3), fill=(240, 20, 30))
    image.save(photo)
    editor.openImage(str(photo))
    wait_for(lambda: settled(editor))
    face, arm, _ = setup_layers(editor)
    face["locked"] = ["skin_smoothing"]
    arm["visible"] = False
    editor._commit()
    editor._change()
    wait_for(lambda: settled(editor))
    editor.selection.pickLayer(face["id"])
    wait_for(lambda: settled(editor))
    before = deepcopy(editor._layers)
    selected, cursor, generation = editor.activeLayerId, editor._cursor, editor._generation
    before_pixels = np.array(render_nodes(image, forest(before)))
    current = dict(editor._recipe)
    with mock_api(lambda p: auto(current, PARTS) if sent(p)["mode"] == "auto" else spots()) as (url, requests):
        configure(editor.ai, url)
        find("descriptionInput").setProperty("text", "只修复面部和衣服的小污点，保留所有原参数")
        click(window, find("applyDescriptionButton"))
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None and settled(editor))
        assert len(requests) == 3 and [sent(r[2])["mode"] for r in requests] == ["auto", "repair", "repair"]
        assert sent(requests[0][2])["repair_available"]
        assert len(editor._layers) == len(before) + 2 and editor._layers[:-2] == before
        assert editor._cursor == cursor + 1 and editor._generation == generation + 1
        for layer, request in zip(editor._layers[-2:], requests[1:]):
            assert not layer["parent_id"] and not any(layer["recipe"].values())
            assert layer["heal"] and layer["mask"]["base"] == "empty"
            encoded = request[2]["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
            with Image.open(BytesIO(base64.b64decode(encoded))) as crop:
                assert crop.width < 800 and crop.height < 600
        assert editor.selection.pickedLayerId == editor.activeLayerId
        assert editor.conversation[-1]["state"] == "applied" and "2处" in editor.conversation[-1]["text"]
        changed = deepcopy(editor._layers)
        after_pixels = np.array(render_nodes(image, forest(changed)))
        allowed = np.maximum.reduce([np.array(raster_mask(l["mask"], image.size)) for l in changed[-2:]])
        assert np.any(after_pixels != before_pixels)
        assert np.array_equal(after_pixels[allowed == 0], before_pixels[allowed == 0])
        project = tmp_path / "repaired.iphoto"
        editor.saveProject(str(project))
        assert read_project(project)["layers"] == changed
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before and editor.activeLayerId == selected
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor._layers == changed
        editor.openProject(str(project))
        wait_for(lambda: editor._pending_project is None and settled(editor))
        assert editor._layers == changed
    assert not warnings, warnings


def test_large_photo_closeup_uses_native_source_pixels(ui, tmp_path):
    from iphoto.ai_grounding import crop_pixels

    editor, _, _, _ = ui
    size = (2400, 3600)
    photo = tmp_path / "native.png"
    image = Image.new("RGB", size, (140, 110, 95))
    image.save(photo)
    editor.openImage(str(photo))
    wait_for(lambda: settled(editor))
    with Image.open(QUrl(editor._original).toLocalFile()) as proxy:
        assert proxy.size != size
    parts = [{"name": "面颊修复", "reason": "小红点", "box": [480, 330, 560, 410]}]
    context = repair_context(validate_repairs(parts)[0], size)
    current = dict(editor._recipe)
    with mock_api(lambda p: auto(current, parts) if sent(p)["mode"] == "auto" else spots(status="unsupported")) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("检查小瑕疵", "auto")
        wait_for(lambda: not editor.busy and editor._pending_request is None and settled(editor))
        assert len(requests) == 2
        body = requests[1][2]
        encoded = body["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
        with Image.open(BytesIO(base64.b64decode(encoded))) as crop:
            assert crop.size == tuple(context["crop_size"])
            assert np.allclose(np.array(crop).mean(axis=(0, 1)), [140, 110, 95], atol=3)
        assert sent(body)["crop_size"] == context["crop_size"]
        assert list(image.crop(crop_pixels(context["_image_crop"], size)).size) == context["crop_size"]


@pytest.mark.parametrize("cancel", ["button", "escape"])
def test_preparing_crop_has_status_cancel_and_discards_late_reply(ui, monkeypatch, cancel):
    editor, window, find, _ = ui
    original_request = editor._request
    captured = []

    def request(op, **data):
        if op == "repair_crop":
            captured.append(data)
            return None
        return original_request(op, **data)

    monkeypatch.setattr(editor, "_request", request)
    before, cursor, generation = deepcopy(editor._layers), editor._cursor, editor._generation
    window.resize(1080, 700)
    window.setProperty("chatOpen", False)
    with mock_api(auto(editor._recipe)) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("修复小瑕疵", "auto")
        wait_for(lambda: editor.aiRepairPreparing)
        assert editor.busy and not editor.ai.busy
        assert editor.selection.taskKind == "ai" and editor.selection.taskCancellable
        assert find("aiRequestProgress").isVisible() and find("cancelAiRequest").isVisible()
        assert "原图" in find("aiRequestProgressText").property("text")
        if cancel == "button":
            click(window, find("cancelAiRequest"))
        else:
            QTest.keyClick(window, Qt.Key_Escape)
        assert not editor.aiRepairPreparing and editor._pending_request is None
        conversation._repair_crop_ready(editor, {"path": "late.png", "crop_size": [1, 1]},
                                        captured[0]["context"], generation)
        assert len(requests) == 1 and editor.conversation[-1]["state"] == "failed"
        assert editor._layers == before and editor._cursor == cursor and editor._generation == generation


def test_actual_padded_circle_is_checked_again_before_mapping():
    region = validate_repairs([PARTS[0]])[0]
    context = repair_context(region, (4016, 6016))
    x = context["allowed_box"][0] + 21
    result = {"spots": [{"point": [x, 500], "radius": 20, "reason": "边缘小点"}]}
    assert validate_spots(result["spots"], context)
    with pytest.raises(ValueError, match="超出"):
        map_repair_spots(result, context, (4016, 6016), region)


def test_padding_removes_visible_spot_rim_and_preserves_pixels_outside_brush():
    size = (4016, 6016)
    region = validate_repairs([{"name": "红点修复", "reason": "红点", "box": [480, 330, 560, 410]}])[0]
    context = repair_context(region, size)
    repair = map_repair_spots(parse_repair_spots(spots(radius=12), context), context, size, region)
    op = repair["heal"]["ops"][0]
    x, y = [round(c*s) for c, s in zip(op["points"][0], size)]
    box = (x-30, y-30, x+31, y+31)
    patch = Image.new("RGB", (61, 61), (140, 110, 95))
    ImageDraw.Draw(patch).ellipse((23, 23, 37, 37), fill=(168, 47, 39))
    layer = {**new_layer("红点修复"), "mask": repair["mask"], "heal": repair["heal"]}
    after = np.array(render_nodes(patch, forest([layer]), canvas_size=size, canvas_box=box))
    assert not np.any(after[:, :, 0].astype(int)-np.maximum(after[:, :, 1], after[:, :, 2]).astype(int) > 70)
    mask = np.array(raster_mask(layer["mask"], size).crop(box))
    assert np.array_equal(after[mask == 0], np.array(patch)[mask == 0])


@pytest.mark.parametrize("failure", ["unsupported", "invalid", "cancel"])
def test_incomplete_closeup_keeps_existing_document_and_history(ui, failure):
    editor, _, _, _ = ui
    setup_layers(editor)
    before = deepcopy(editor._layers)
    cursor, generation = editor._cursor, editor._generation
    current = dict(editor._recipe)

    def reply(payload):
        if sent(payload)["mode"] == "auto":
            return auto(current)
        if failure == "cancel":
            time.sleep(.5)
        return spots(status="unsupported", summary="不能可靠区分瑕疵") if failure == "unsupported" else spots(radius=500)

    with mock_api(reply) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("仅修复小瑕疵", "auto")
        if failure == "cancel":
            wait_for(lambda: len(requests) == 2)
            assert editor.ai.busy and "瑕疵检查" in editor.ai.requestProgress
            editor.ai.cancel()
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None and settled(editor))
        assert editor._layers == before and editor._cursor == cursor and editor._generation == generation
        assert len(requests) == (3 if failure == "invalid" else 2)
        assert editor.conversation[-1]["state"] == ("unsupported" if failure == "unsupported" else "failed")
        if failure == "cancel":
            assert "取消" in editor.conversation[-1]["text"]


def test_invalid_second_part_never_applies_the_first(ui):
    editor, _, _, _ = ui
    current = dict(editor._recipe)
    before, cursor, generation = deepcopy(editor._layers), editor._cursor, editor._generation
    index = 0

    def reply(payload):
        nonlocal index
        if sent(payload)["mode"] == "auto":
            return auto(current, PARTS)
        index += 1
        return spots(radius=20 if index == 1 else 500)

    with mock_api(reply) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("修复两处小污点", "auto")
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None and settled(editor))
        assert len(requests) == 4 and editor.conversation[-1]["state"] == "failed"
        assert editor._layers == before and editor._cursor == cursor and editor._generation == generation


def test_supported_part_is_applied_and_unlocated_part_is_explicit(ui):
    editor, _, _, _ = ui
    before = deepcopy(editor._layers)
    current = dict(editor._recipe)
    index = 0

    def reply(payload):
        nonlocal index
        if sent(payload)["mode"] == "auto":
            return auto(current, PARTS)
        index += 1
        return spots() if index == 1 else spots(status="unsupported", summary="阴影不应当作污点")

    with mock_api(reply) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("修复两处小污点", "auto")
        wait_for(lambda: not editor.ai.busy and editor._pending_request is None and settled(editor))
        assert len(requests) == 3 and editor._layers[:-1] == before
        assert editor.conversation[-1]["state"] == "applied"
        assert "衣服修复：阴影不应当作污点" in editor.conversation[-1]["text"]


@pytest.mark.parametrize("blocked", ["capacity", "unavailable"])
def test_repair_capability_and_capacity_are_checked_before_closeup(ui, monkeypatch, blocked):
    editor, _, _, _ = ui
    if blocked == "capacity":
        editor._layers.extend(new_layer("已有层", True) for _ in range(MAX_LAYERS-len(editor._layers)))
        editor._commit()
    else:
        monkeypatch.setattr(conversation, "repair_available", lambda: False)
    before, cursor, generation = deepcopy(editor._layers), editor._cursor, editor._generation
    with mock_api(auto(editor._recipe)) as (url, requests):
        configure(editor.ai, url)
        editor.sendMessage("修复小瑕疵", "auto")
        wait_for(lambda: not editor.busy and editor._pending_request is None and settled(editor))
        assert len(requests) == 1 and editor.conversation[-1]["state"] == "failed"
        assert editor._layers == before and editor._cursor == cursor and editor._generation == generation


@pytest.mark.parametrize("problem", ["second_mask", "overlap", "capacity", "changed_name", "changed_lock", "changed_heal"])
def test_transaction_validates_all_parts_and_the_entire_expected_document(ui, problem):
    editor, _, _, _ = ui
    if problem == "capacity":
        editor._layers.extend(new_layer("已有层", True) for _ in range(MAX_LAYERS - len(editor._layers)))
        editor._commit()
    expected = deepcopy(editor._layers)
    parts = [mapped(0, (300, 200)), mapped(1, (300, 200))]
    if problem == "second_mask":
        parts[1]["mask"]["base"] = "full"
    elif problem == "overlap":
        parts[1] = deepcopy(parts[0])
    elif problem == "changed_name":
        editor._layers[0]["name"] = "期间改名"
    elif problem == "changed_lock":
        editor._locked.add("exposure")
    elif problem == "changed_heal":
        editor._layers[0]["heal"] = mapped(0, (300, 200))["heal"]
    editor._sync_layer()
    before, cursor, generation = deepcopy(editor._layers), editor._cursor, editor._generation
    with pytest.raises(ValueError):
        heal.applyAIRepairs(editor, parts, expected)
    assert editor._layers == before and editor._cursor == cursor and editor._generation == generation
