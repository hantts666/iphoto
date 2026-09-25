from copy import deepcopy
import json

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QUrl

from iphoto.document import (new_layer, empty_mask, raster_mask, render_layers,
                             validate_project, validate_mask, read_project, write_project)
from iphoto.engine import Recipe, RANGES, render
from iphoto.app import Editor
from iphoto.ai_tasks import parse_selection
from test_ai import completion, configure, mock_api, wait_for
from test_editor import settled


def photo(path, size=(480, 320)):
    rng = np.random.default_rng(4)
    image = Image.fromarray(rng.integers(40, 210, (size[1], size[0], 4), dtype=np.uint8), "RGBA")
    image.save(path)
    return image


def region(layer, feather=.015):
    layer["mask"] = empty_mask()
    layer["mask"].update(feather=feather, label="主体")
    layer["mask"]["ops"] = [{"kind": "ellipse", "mode": "add", "points": [[.25,.2],[.7,.75]]}]
    return layer


@pytest.mark.parametrize("tool", list(RANGES))
def test_every_tool_preserves_outside_selection_and_alpha(tool, tmp_path):
    original = photo(tmp_path / "source.png", (180,120))
    layer = region(new_layer())
    layer["recipe"][tool] = RANGES[tool][1]
    result = np.asarray(render_layers(original, [layer]))
    before = np.asarray(original)
    hard = deepcopy(layer["mask"]); hard["feather"] = 0
    outside = np.asarray(raster_mask(hard, original.size)) == 0
    assert np.array_equal(result[outside], before[outside])
    assert np.array_equal(result[:,:,3], before[:,:,3])
    assert np.any(result[~outside,:3] != before[~outside,:3]), tool


def test_masks_inversion_subtraction_and_layer_order(tmp_path):
    image = photo(tmp_path / "photo.png", (120,80))
    layer = region(new_layer(), 0)
    layer["recipe"]["exposure"] = 1
    other = new_layer("对比", True); other["recipe"]["contrast"] = 60
    assert not np.array_equal(np.asarray(render_layers(image,[layer,other])), np.asarray(render_layers(image,[other,layer])))
    layer["visible"] = False
    assert np.array_equal(np.asarray(render_layers(image,[layer])), np.asarray(image))
    layer["visible"] = True; layer["opacity"] = 0
    assert np.array_equal(np.asarray(render_layers(image,[layer])), np.asarray(image))
    hard = raster_mask(layer["mask"],image.size)
    layer["mask"]["inverted"] = True
    inverted = raster_mask(layer["mask"],image.size)
    assert np.all(np.asarray(hard,dtype=int)+np.asarray(inverted,dtype=int)==255)
    layer["mask"]["inverted"] = False
    layer["mask"]["ops"].append({"kind":"brush","mode":"subtract","points":[[.5,.5]],"radius":.1})
    assert raster_mask(layer["mask"],image.size).getpixel((60,40)) == 0


def selection_completion(polygons=None):
    result = {"status":"selected", "summary":"选中中央主体，边缘请检查。", "polygons": polygons if polygons is not None else [[[200,200],[800,200],[750,800],[200,750]]]}
    return {"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(result)}}]}


@pytest.mark.parametrize("points", [[], [[0,0],[1,1]], [[0,0],[1,1],[2,2]], [[0,0],[1000,0],[0,999]], [[0,0],[True,0],[0,999]], [[0,0],[float('nan'),0],[0,999]]])
def test_invalid_ai_selection_never_becomes_mask(points):
    with pytest.raises(ValueError): parse_selection(selection_completion([points]))


def test_project_atomic_save_migration_and_invalid_layers(tmp_path):
    legacy = {"schema_version":"0.1", "engine_version":"0.1.1-lut65", "source":"source.png", "source_sha256":"a"*64, "recipe":{"exposure":.2}, "locked":["exposure"]}
    doc = validate_project(legacy)
    assert len(doc["layers"]) == 1 and doc["layers"][0]["mask"]["base"] == "full"
    path = tmp_path / "save.iphoto"
    write_project(path,doc)
    before = path.read_bytes()
    with pytest.raises(FileExistsError): write_project(path,doc)
    assert path.read_bytes() == before
    doc["layers"][0]["name"] = "修改过的层"
    write_project(path,doc,overwrite=True)
    assert read_project(path)["layers"][0]["name"] == "修改过的层"
    assert not list(tmp_path.glob("*.tmp"))
    bad = deepcopy(doc); bad["layers"].append(deepcopy(bad["layers"][0]))
    with pytest.raises(ValueError): validate_project(bad)
    bad = deepcopy(doc); bad["layers"][0]["opacity"] = float("nan")
    with pytest.raises(ValueError): validate_project(bad)


def test_full_size_export_history_and_project_roundtrip(tmp_path, qt_app, ai_store):
    image = photo(tmp_path / "original.png",(1900,1100))
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(tmp_path / "original.png")); wait_for(lambda: editor.hasImage and settled(editor))
        editor.addLayer(); editor.renameLayer("人物提亮")
        assert editor.selectionLabel == "空选区"
        editor.drawSelection("ellipse","add",[[.2,.2],[.7,.8]],.02)
        editor.setFeather(2)
        editor.setParameter("exposure",1)
        editor.setParameter("sharpness",50)
        editor.finishGesture()
        state = deepcopy(editor._layers)
        wait_for(lambda: settled(editor))
        editor.undo(); assert editor.parameters["sharpness"] == 0
        editor.redo(); assert editor._layers == state
        output = tmp_path / "edited.png"
        editor.exportImage(str(output)); wait_for(lambda: settled(editor))
        exported = np.asarray(Image.open(output)); original = np.asarray(image)
        hard = deepcopy(state[-1]["mask"]); hard["feather"] = 0
        outside = np.asarray(raster_mask(hard,image.size)) == 0
        assert exported.shape == original.shape
        assert np.array_equal(exported[outside],original[outside])
        assert not np.array_equal(exported[~outside],original[~outside])
        project = tmp_path / "work.iphoto"
        editor.saveProject(str(project)); assert project.exists()
        editor.deleteLayer(); assert len(editor.layers) == 1
        editor.openProject(str(project)); wait_for(lambda: settled(editor))
        assert editor._layers == state
        editor.renameLayer("新名称"); editor.saveProject(str(project))
        assert read_project(project)["layers"][-1]["name"] == "新名称"
    finally: editor.close()


def test_advice_selection_errors_and_conversation_recovery(tmp_path, qt_app, ai_store, pixel_protocol_stub):
    photo(tmp_path / "original.png")
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(tmp_path / "original.png")); wait_for(lambda: editor.hasImage and settled(editor))
        original_recipe = dict(editor.parameters)
        with mock_api() as (url,requests):
            configure(editor.ai,url)
            editor.sendMessage("给我修图建议","advice")
            wait_for(lambda: not editor.busy)
            assert editor.parameters == original_recipe
            assert editor.conversation[-1]["state"] == "proposed"
            editor.applyAdvice(editor.conversation[-1]["id"])
            wait_for(lambda: settled(editor))
            assert editor.parameters["exposure"] == .25
            editor.undo(); wait_for(lambda: settled(editor))
            assert len(editor.conversation) == 3  # history doesn't erase the conversation
        editor.addLayer(); editor.renameLayer("主体")
        with mock_api(selection_completion()) as (url,requests):
            configure(editor.ai,url)
            before = deepcopy(editor._layer()["mask"])
            editor.sendMessage("选中主体","selection"); wait_for(lambda: not editor.busy)
            assert editor.hasSelectionDraft
            assert editor._layer()["mask"] == before
            request = requests[0][2]
            context = json.loads(request["messages"][1]["content"][0]["text"])
            assert context["mode"] == "selection" and "recent_conversation" not in context
            assert len(request["messages"][1]["content"]) == 2
            editor.saveProject(str(tmp_path / "draft.iphoto"))
            assert read_project(tmp_path / "draft.iphoto")["selection_draft"]
            editor.acceptSelection()
            assert not editor.hasSelectionDraft and editor._layer()["mask"]["bitmap"]
            editor.undo(); assert editor._layer()["mask"] == before
            editor.redo()
            editor.sendMessage("选中主体","selection"); wait_for(lambda: not editor.busy)
            before = deepcopy(editor._layer()["mask"])
            editor.discardSelection(); assert editor._layer()["mask"] == before
        with mock_api(status=401) as (url,_):
            configure(editor.ai,url)
            editor.sendMessage("再给点建议","advice"); wait_for(lambda: not editor.busy)
            assert editor.conversation[-1]["role"] == "error"
        editor._save_recovery()
        recovery = read_project(editor._recovery_path)
        assert recovery["conversation"] == editor.conversation
        assert "sk-test" not in editor._recovery_path.read_text(encoding="utf-8")
        editor.exportConversation(str(tmp_path / "chat.md"))
        assert "给我修图建议" in (tmp_path / "chat.md").read_text(encoding="utf-8")
        history = deepcopy(editor.conversation)
    finally: editor.close()
    restarted = Editor(ai_store=ai_store)
    try:
        assert restarted.canRecover
        restarted.recoverLatest(); wait_for(lambda: restarted.hasImage and settled(restarted))
        assert restarted.conversation == history
        assert restarted.projectPath == ""  # recovery prompts for a real project location
    finally: restarted.close()


def test_source_mismatch_and_selection_failure_preserve_document(tmp_path, qt_app, ai_store):
    path = tmp_path / "source.png"; photo(path)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        with mock_api(selection_completion([[[0,0],[1000,1],[0,999]]])) as (url,_):
            configure(editor.ai,url)
            before = deepcopy(editor._layers)
            editor.sendMessage("选主体","selection"); wait_for(lambda: not editor.busy)
            assert editor._layers == before and not editor.hasSelectionDraft
            assert editor.conversation[-1]["role"] == "error"
        project = tmp_path / "project.iphoto"; editor.saveProject(str(project))
        saved_conversation = deepcopy(editor.conversation)
        Image.new("RGB",(100,100),"red").save(path)
        editor.openProject(str(project)); wait_for(lambda: settled(editor))
        assert "源图片已变化" in editor.status
        assert editor.conversation == saved_conversation  # failed open retains the current workspace
        assert editor.parameters == Recipe().to_dict()
    finally: editor.close()


def test_queued_export_blocks_document_mutation(tmp_path, qt_app, ai_store):
    path = tmp_path / "photo.png"; photo(path)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        editor.setParameter("exposure",.4); editor.finishGesture()
        editor._timer.stop()
        editor._schedule_render()
        assert editor.rendering
        editor.exportImage(str(tmp_path / "export.png"))
        assert editor.busy  # export is queued behind the active render
        before = deepcopy(editor._layers)
        editor.addLayer()
        editor.setParameter("contrast",30)
        assert editor._layers == before
        wait_for(lambda: settled(editor))
        assert (tmp_path / "export.png").exists()
    finally: editor.close()
