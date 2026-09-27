"""Default chat can decide to edit the current layer or create local layers."""

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.ai_protocol import AUTO_SCHEMA, AUTO_PROMPT, parse_auto
from iphoto.document import read_project
from iphoto.engine import Recipe
from iphoto.workspace import Editor, ROOT
from test_ai import completion, configure, mock_api, wait_for
from test_editor import settled
from test_redesign import region_completion
from test_v14 import scene_response


def auto_response(action="layers", regions=None, recipe=None, summary="已规划人像局部调整"):
    plan = {
        "action": action,
        "summary": summary,
        "recipe": recipe or Recipe().to_dict(),
        "regions": regions if regions is not None else [
            {
                "name": "人像皮肤",
                "reason": "保边磨皮",
                "box": [250, 180, 600, 780],
                "point": [420, 450],
                "recipe": Recipe(skin_smoothing=35).to_dict(),
            }
        ],
    }
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan, ensure_ascii=False)}}]}


def test_auto_protocol_checks_actions_and_local_masks():
    assert AUTO_SCHEMA["properties"]["regions"]["maxItems"] == 4
    assert "exposure=0.25" in AUTO_PROMPT and "sharpness" in AUTO_PROMPT
    result = parse_auto(auto_response(), Recipe().to_dict(), [])
    assert result["action"] == "layers" and result["regions"][0]["recipe"]["skin_smoothing"] == 35
    adjusted = parse_auto(
        auto_response("adjust", [], Recipe(exposure=.4).to_dict(), "整体提亮"),
        Recipe().to_dict(), [],
    )
    assert adjusted["recipe"]["exposure"] == .4
    answered = parse_auto(auto_response("answer", [], Recipe().to_dict(), "建议保留背景细节"), Recipe().to_dict(), [])
    assert answered["status"] == "answered"
    with pytest.raises(ValueError, match="不能包含局部区域"):
        parse_auto(auto_response("adjust"), Recipe().to_dict(), [])
    bad_region = auto_response(regions=[{**json.loads(auto_response()["choices"][0]["message"]["content"])["regions"][0], "recipe": Recipe().to_dict()}])
    with pytest.raises(ValueError, match="没有调整效果"):
        parse_auto(bad_region, Recipe().to_dict(), [])


def test_default_chat_creates_portrait_layer_and_one_undo(qt_app, ai_store, tmp_path, pixel_protocol_stub):
    for font in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font_path = Path("C:/Windows/Fonts") / font
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    image = Image.new("RGB", (600, 420), (55, 96, 77))
    draw = ImageDraw.Draw(image)
    draw.ellipse((155, 70, 350, 340), fill=(196, 138, 114))
    photo = tmp_path / "portrait.png"
    image.save(photo)
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.resize(1440, 930)

    def find(name):
        item = window.findChild(QObject, name)
        assert item is not None, name
        return item

    def click(name):
        item = find(name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(100)

    try:
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and settled(editor))
        with mock_api(auto_response()) as (url, requests):
            configure(editor.ai, url)
            assert find("chatModeBox").property("currentIndex") == 0
            find("descriptionInput").setProperty("text", "给人物面部磨皮，背景保持清晰")
            click("applyDescriptionButton")
            wait_for(lambda: len(editor.layers) == 2 and settled(editor) and window.property("previewReady"))
            assert not editor.hasRegionDraft
            assert editor.activeLayerName == "人像皮肤"
            assert editor.parameters["skin_smoothing"] == 35
            assert editor.selection.pickedLayerId == editor.activeLayerId
            assert len(requests) == 1
            sent = json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
            assert sent["mode"] == "auto" and sent["max_new_layers"] >= 1
            assert "mask_image_note" not in sent
            assert sum(item["type"] == "image_url" for item in requests[0][2]["messages"][1]["content"]) == 1
            assert editor.conversation[-2]["state"] == "applied"
            assert editor.conversation[-2]["layer_name"] == "局部分层"
            assert window.grabWindow().save(str(ROOT / "artifacts/ux-ai-auto-layers.png"))
            project = tmp_path / "auto-portrait.iphoto"
            editor.saveProject(str(project))
            wait_for(lambda: project.exists() and not editor.savingProject)
            assert read_project(project)["layers"][-1]["recipe"]["skin_smoothing"] == 35
            editor.undo()
            wait_for(lambda: settled(editor))
            assert len(editor.layers) == 1
        assert not warnings, warnings
    finally:
        window.close()
        editor.close()
        qt_app.processEvents()


def test_auto_layers_complete_with_real_pixel_worker(qt_app, ai_store, tmp_path):
    from iphoto.segmentation.models import available

    if not available():
        pytest.skip("Optional EfficientSAM weights not installed")
    image = Image.new("RGB", (600, 420), (42, 78, 108))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 45, 250, 320), fill=(220, 151, 114))
    photo = tmp_path / "real-pixel.png"
    image.save(photo)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and settled(editor))
        response = auto_response(regions=[{
            "name": "人物区域", "reason": "局部磨皮", "box": [100, 80, 430, 800],
            "point": [270, 400], "recipe": Recipe(skin_smoothing=30).to_dict(),
        }])
        with mock_api(response) as (url, requests):
            configure(editor.ai, url)
            editor.sendMessage("只给人物区域轻度磨皮", "auto")
            wait_for(lambda: len(editor.layers) == 2 and settled(editor), seconds=75)
            assert not editor.hasRegionDraft
            assert editor.parameters["skin_smoothing"] == 30
            assert editor.selection.pickedLayerId == editor.activeLayerId
            assert len(requests) == 1
            editor.undo()
            wait_for(lambda: settled(editor))
            assert len(editor.layers) == 1
    finally:
        editor.close()


def test_legacy_select_it_yourself_reply_retries_as_auto_layers(qt_app, ai_store, tmp_path, pixel_protocol_stub):
    photo = tmp_path / "legacy.png"
    Image.new("RGB", (600, 420), (154, 115, 95)).save(photo)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and settled(editor))

        def reply(payload):
            mode = json.loads(payload["messages"][1]["content"][0]["text"])["mode"]
            return (
                completion(status="unsupported", summary="请先建立人物选区和新图层，再磨皮")
                if mode == "auto" else region_completion()
            )

        with mock_api(reply) as (url, requests):
            configure(editor.ai, url)
            editor.sendMessage("人物面部磨皮，背景保持清晰", "auto")
            wait_for(lambda: len(editor.layers) == 3 and settled(editor))
            assert [json.loads(r[2]["messages"][1]["content"][0]["text"])["mode"] for r in requests] == ["auto", "regions"]
            assert not editor.hasRegionDraft
            assert len([m for m in editor.conversation if m["role"] == "user"]) == 1
            editor.undo()
            wait_for(lambda: settled(editor))
            assert len(editor.layers) == 1
    finally:
        editor.close()


def test_auto_chat_global_edit_and_answer_do_not_create_layers(qt_app, ai_store, tmp_path):
    photo = tmp_path / "global.png"
    Image.new("RGB", (240, 160), (82, 104, 121)).save(photo)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and settled(editor))
        with mock_api(auto_response("adjust", [], Recipe(exposure=.4).to_dict(), "整体略微提亮")) as (url, _):
            configure(editor.ai, url)
            editor.sendMessage("整张照片稍微提亮", "auto")
            wait_for(lambda: not editor.ai.busy and editor.parameters["exposure"] == .4)
        assert len(editor.layers) == 1
        before_layers = len(editor.layers)
        before_recipe = dict(editor.parameters)
        with mock_api(auto_response("answer", [], before_recipe, "这张照片适合保留暗部层次")) as (url, _):
            configure(editor.ai, url)
            editor.sendMessage("这张照片的光线有什么问题？", "auto")
            wait_for(lambda: not editor.ai.busy and editor.conversation[-1]["state"] == "answered")
        assert len(editor.layers) == before_layers
        assert editor.parameters == before_recipe
    finally:
        editor.close()


def test_invalid_ai_layer_recipe_is_replanned_once(qt_app, ai_store, tmp_path, pixel_protocol_stub):
    photo = tmp_path / "retry-portrait.png"
    Image.new("RGB", (600, 420), (154, 115, 95)).save(photo)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and settled(editor))
        bad = auto_response()
        body = json.loads(bad["choices"][0]["message"]["content"])
        body["regions"][0]["recipe"]["sharpness"] = 150
        bad["choices"][0]["message"]["content"] = json.dumps(body)

        def reply(payload):
            prompt = payload["messages"][0]["content"]
            return auto_response() if "上次回复未通过程序校验" in prompt else bad

        with mock_api(reply) as (url, requests):
            configure(editor.ai, url)
            assert editor.sendMessage("给人物磨皮", "auto")
            wait_for(lambda: len(editor.layers) == 2 and settled(editor))
            assert len(requests) == 2
            assert "sharpness" in requests[1][2]["messages"][0]["content"]
            assert len([m for m in editor.conversation if m["role"] == "user"]) == 1
            assert editor.parameters["skin_smoothing"] == 35
    finally:
        editor.close()


def test_invalid_ai_layer_recipe_stops_after_one_repair(qt_app, ai_store, tmp_path):
    photo = tmp_path / "invalid-portrait.png"
    Image.new("RGB", (400, 300), (154, 115, 95)).save(photo)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and settled(editor))
        invalid = auto_response()
        body = json.loads(invalid["choices"][0]["message"]["content"])
        body["regions"][0]["recipe"]["sharpness"] = 150
        invalid["choices"][0]["message"]["content"] = json.dumps(body)
        with mock_api(invalid) as (url, requests):
            configure(editor.ai, url)
            assert editor.sendMessage("给人物磨皮", "auto")
            wait_for(lambda: editor.conversation[-1]["role"] == "error")
            assert len(requests) == 2
            assert len(editor.layers) == 1
            assert not editor.hasRegionDraft
    finally:
        editor.close()


def test_first_scene_analysis_repairs_invalid_catalog_reply(qt_app, ai_store, tmp_path):
    photo = tmp_path / "scene-first-run.png"
    Image.new("RGB", (600, 420), (72, 115, 94)).save(photo)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and settled(editor))
        invalid = {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]}

        def reply(payload):
            prompt = payload["messages"][0]["content"]
            return scene_response() if "上次回复未通过程序校验" in prompt else invalid

        with mock_api(reply) as (url, requests):
            configure(editor.ai, url)
            editor.analyzeScene(True)
            wait_for(lambda: len(editor.sceneObjects) == 3 and not editor.ai.busy)
            assert len(requests) == 2
            assert not editor.ai.isError
            assert len([m for m in editor.conversation if m["role"] == "user"]) == 1
    finally:
        editor.close()
