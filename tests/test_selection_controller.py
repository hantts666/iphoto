"""SelectionController facade: tool state, task routing, refine routing, apply branches."""

from copy import deepcopy

import pytest
from PIL import Image

from iphoto.controllers import pixel_selections
from iphoto.controllers import selections as selections_mod
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled


@pytest.fixture
def editor(tmp_path, qt_app, ai_store):
    path = tmp_path / "source.png"
    Image.new("RGB", (240, 160), (55, 90, 130)).save(path)
    e = Editor(ai_store=ai_store)
    e.openImage(str(path))
    wait_for(lambda: e.hasImage and settled(e))
    try:
        yield e
    finally:
        e.close()


def caps(**availability):
    return [
        {
            "id": cid,
            "name": cid,
            "available": ok,
            "status": "" if ok else "缺少 " + cid,
            "description": "",
        }
        for cid, ok in availability.items()
    ]


def test_tool_state_choose_tool_and_draft_signals(editor):
    sel = editor.selection
    assert sel.tool == "inspect" and not sel.showMask and sel.navigationTool
    chosen, began, ended = [], [], []
    sel.toolChosen.connect(chosen.append)
    sel.draftBegan.connect(began.append)
    sel.draftEnded.connect(lambda: ended.append(True))

    sel.chooseTool("rect")
    assert sel.tool == "rect" and chosen == ["rect"]
    assert editor.hasSelectionDraft and began == ["selection"] and sel.showMask

    sel.chooseTool("brush")
    assert sel.tool == "brush" and sel.mode == "add"
    sel.toggleMode()
    assert sel.mode == "subtract"
    sel.toggleMode()
    assert sel.mode == "add"
    sel.setMode("replace")
    sel.adjustBrush(1)
    assert sel.brushDiameter == 10
    for _ in range(80):
        sel.adjustBrush(-1)
    assert sel.brushDiameter == 2
    assert sel.brushRadius == pytest.approx(1 / 160)
    for _ in range(200):
        sel.adjustBrush(1)
    assert sel.brushRadius == pytest.approx(0.15)

    # Navigation tools stay available during a draft and never create one.
    sel.chooseTool("hand")
    assert sel.tool == "hand" and sel.navigationTool
    sel.chooseTool("rect")  # blocked while busy-free draft exists: tool switches, no second draft
    assert editor.hasSelectionDraft

    editor.discardSelection()
    wait_for(lambda: settled(editor))
    assert ended == [True] and sel.tool == "inspect" and not sel.showMask


def test_review_mask_loads_current_layer(editor):
    sel = editor.selection
    sel.reviewMask()
    assert editor.hasSelectionDraft and sel.tool == "brush" and sel.mode == "add"
    assert sel.showMask
    editor.discardSelection()
    wait_for(lambda: settled(editor))


@pytest.mark.parametrize("condition", ["invalid", "no_draft", "busy", "regions"])
def test_correct_draft_refuses_conflicts_without_starting_or_rebinding(editor, condition):
    if condition != "no_draft":
        editor.beginSelection("empty")
    if condition == "busy":
        editor._active = {"op": "selection"}
    elif condition == "regions":
        editor._region_candidate = {"layers": []}
    sel = editor.selection
    before = deepcopy((editor._layers, editor._candidate, editor._selection_target_id,
                       editor._generation, editor._cursor, sel.tool, sel.mode, sel.showMask))
    try:
        assert not sel.correctDraft("replace" if condition == "invalid" else "subtract")
        assert deepcopy((editor._layers, editor._candidate, editor._selection_target_id,
                         editor._generation, editor._cursor, sel.tool, sel.mode, sel.showMask)) == before
    finally:
        editor._active = None
        editor._region_candidate = None


def test_mask_edit_intent_survives_project_reload(editor, tmp_path):
    from iphoto.document import read_project

    editor.setParameter("sharpness", 37)
    editor.finishGesture()
    original = deepcopy(editor._layer())
    editor.selection.reviewMask()
    editor.drawDraft("rect", "subtract", [[0.1, 0.1], [0.6, 0.6]], 0)
    wait_for(lambda: settled(editor))
    revised = deepcopy(editor._candidate)
    project = tmp_path / "mask-correction.iphoto"
    editor.saveProject(str(project))
    assert project.exists()
    assert read_project(project)["selection_target_id"] == original["id"]
    editor.selection.discard()
    wait_for(lambda: settled(editor))
    editor.openProject(str(project))
    wait_for(lambda: editor._pending_project is None and settled(editor))
    assert editor.selection.editingLayerMask
    assert editor.selection.maskEditLayerName == original["name"]
    assert editor._candidate == revised
    editor.selection.applyDefault()
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == 1
    assert editor.activeLayerId == original["id"]
    assert editor._layer()["mask"] == revised
    assert editor._layer()["recipe"] == original["recipe"]
    assert read_project(project)["selection_target_id"] == original["id"]
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layer() == original
    assert editor._payload()["selection_target_id"] == ""


def test_legacy_range_draft_keeps_new_layer_default(editor):
    from iphoto.document import validate_project

    editor.beginSelection("empty")
    editor.draftAction("all")
    payload = deepcopy(editor._payload())
    payload["schema_version"] = "1.7"
    payload.pop("selection_target_id")
    assert validate_project(payload)["selection_target_id"] == ""
    assert not editor.selection.editingLayerMask
    editor.selection.applyDefault()
    assert len(editor.layers) == 2


@pytest.mark.parametrize("target", ["missing", None, [], 3])
def test_project_rejects_invalid_mask_edit_target(editor, target):
    from iphoto.document import validate_project

    editor.selection.reviewMask()
    payload = deepcopy(editor._payload())
    payload["selection_target_id"] = target
    with pytest.raises(ValueError, match="目标图层无效"):
        validate_project(payload)


def test_project_rejects_target_without_draft_or_for_inactive_layer(editor):
    from iphoto.document import validate_project

    editor.addGlobalLayer()
    editor.selection.reviewMask()
    payload = deepcopy(editor._payload())
    payload["selection_draft"] = None
    with pytest.raises(ValueError, match="目标图层无效"):
        validate_project(payload)
    payload = deepcopy(editor._payload())
    payload["selection_target_id"] = editor._layers[0]["id"]
    with pytest.raises(ValueError, match="目标图层无效"):
        validate_project(payload)


def test_mask_review_prevents_switching_layer_or_rebinding_new_range(editor):
    editor.addGlobalLayer()
    other = editor._layers[0]["id"]
    editor.selection.reviewMask()
    selected = editor.activeLayerId
    editor.selection.pickLayer(other)
    assert editor.activeLayerId == selected and editor.selection.pickedLayerId == ""
    editor.selection.discard()
    wait_for(lambda: settled(editor))
    assert editor.selection.pickedLayerId == selected
    editor.beginSelection("empty")
    editor.selection.reviewMask()
    assert not editor.selection.editingLayerMask
    assert editor._selection_target_id == ""


def test_refine_routing_and_methods(editor, monkeypatch):
    sel = editor.selection
    requests = []
    monkeypatch.setattr(
        Editor, "_request", lambda self, op, **data: requests.append((op, data))
    )
    monkeypatch.setattr(
        selections_mod, "capabilities", lambda: caps(grabcut=True, matte=True)
    )
    monkeypatch.setattr(pixel_selections, "available", lambda: True)

    editor._capabilities = caps(matte=True, grabcut=True, u2net=False, pixels=False)
    assert sel.autoRefineMethod == "grabcut"
    methods = {m["id"]: m for m in sel.refineMethods}
    assert methods["matte"]["available"] and not methods["u2net"]["available"]
    assert "缺少" in methods["u2net"]["description"]

    sel.refine("matte", 12)
    assert requests[-1][0] == "matte" and requests[-1][1]["radius"] == 12
    sel.refine("auto", 8)
    assert requests[-1][0] == "selection"
    assert requests[-1][1]["plugin"] == "grabcut"

    editor.beginSelection("empty")
    editor._capabilities = caps(matte=True, grabcut=True, u2net=False, pixels=True)
    assert sel.autoRefineMethod == "sam"
    sel.refine("auto")
    assert requests[-1][0] == "segment"

    editor._capabilities = caps(matte=False, grabcut=False, u2net=False, pixels=False)
    assert sel.autoRefineMethod == ""
    notes = []
    monkeypatch.setattr(Editor, "_notify", lambda self, m, error=False: notes.append((m, error)))
    sel.refine("auto")
    assert notes and notes[-1][1]


def test_precise_masks_use_ai_edges_without_reidentifying_the_target(editor, monkeypatch):
    from PIL import ImageDraw
    from iphoto.document import empty_mask
    from iphoto.masks import encode_bitmap
    from iphoto.controllers import matting

    pixels = Image.new("L", (240, 160))
    ImageDraw.Draw(pixels).rectangle((70, 30, 170, 130), fill=255)
    mask = {**empty_mask(), "label": "已确认皮肤",
            "bitmap": encode_bitmap(pixels, sampling="alpha", preserve_resolution=True)}
    editor._set_candidate(mask)
    wait_for(lambda: settled(editor))
    editor._pixel_points = [[.5, .5, 1], [.1, .1, 0]]
    editor._capabilities = caps(details=True, pixels=True, matte=True, grabcut=True)
    requests = []
    monkeypatch.setattr(Editor, "_request", lambda self, op, **data: requests.append((op, data)))
    assert editor.selection.autoRefineMethod == "details"
    editor.selection.refine("auto", 12)
    op, request = requests.pop()
    assert op == "matte" and request["method"] == "neural" and request["radius"] == 12
    assert request["mask"] == mask and request["points"] == editor.pixelPoints
    assert request["points"] is not editor._pixel_points
    before_layers = deepcopy(editor._layers)
    quality = {"backend": "ViTMatte-S · ONNX", "partial_pixels": 100, "elapsed_ms": 200}
    matting.complete(editor, {"mask": mask, "quality": quality}, points=request["points"])
    assert editor.pixelPoints == request["points"] and editor._pixel_hint == mask
    assert editor._layers == before_layers and "AI 原图边缘" in editor.selectionQuality
    wait_for(lambda: settled(editor))
    # A region draft uses its own mask, never another draft's point constraints.
    editor._region_candidate = {"layers": [{"mask": mask}]}
    editor._region_index = 0
    assert editor.selection.autoRefineMethod == "details"
    editor.selection.refine("auto")
    assert requests[-1][1]["points"] == [] and requests[-1][1]["mask"] == mask
    editor._region_candidate = None
    monkeypatch.setattr(pixel_selections, "select_hint", lambda *args: requests.append(("segment", {})))
    editor.selection.refine("sam")
    assert requests[-1][0] == "segment"


def test_apply_branches(editor):
    sel = editor.selection
    editor.beginSelection("empty")
    editor.draftAction("all")
    wait_for(lambda: settled(editor))
    sel.apply("replace_mask")
    wait_for(lambda: settled(editor))
    assert not editor.hasSelectionDraft
    assert editor._layer()["mask"]["base"] == "full"

    editor.beginSelection("empty")
    editor.draftAction("all")
    wait_for(lambda: settled(editor))
    count = len(editor.layers)
    sel.apply("new_layer")
    wait_for(lambda: settled(editor))
    assert len(editor.layers) == count + 1 and not editor.hasSelectionDraft


def test_task_state_machine_and_cancel(editor):
    sel = editor.selection
    assert sel.taskKind == "none" and not sel.taskCancellable
    try:
        editor._active = {"op": "segment", "priority": "low"}
        assert sel.taskKind == "none" and not sel.taskCancellable

        editor._active = {"op": "segment"}
        assert sel.taskKind == "pixel" and sel.taskCancellable
        sel.cancelTask()
        assert editor._active["cancelled"] is True

        editor._active = None
        editor._matte_active = {"op": "matte"}
        assert sel.taskKind == "matte"
        sel.cancelTask()
        assert editor._matte_active is None and not editor.matteBusy

        editor._active = {"op": "selection"}
        assert sel.taskKind == "refine" and not sel.taskCancellable

        editor._active = {"op": "render"}
        assert sel.taskKind == "none"
    finally:
        editor._active = None
        editor.changed.emit()


def test_mask_view_controls_show_mask(editor):
    sel = editor.selection
    editor.beginSelection("empty")
    assert sel.showMask
    sel.setMaskView("adjustment")
    assert editor.maskView == "adjustment" and not sel.showMask
    sel.setMaskView("grayscale")
    assert sel.showMask
    sel.setMaskView("bogus")
    assert editor.maskView == "grayscale" and sel.showMask
    sel.toggleShowMask()
    assert not sel.showMask
    sel.toggleShowMask()
    assert sel.showMask
    editor.discardSelection()
    wait_for(lambda: settled(editor))
    assert not sel.showMask


def test_row_select_stacks_modes(editor, monkeypatch):
    from iphoto.controllers import pixel_selections
    calls = []
    monkeypatch.setattr(
        pixel_selections,
        "select_objects",
        lambda self, ids, mode: calls.append((ids, mode)),
    )
    sel = editor.selection
    sel.rowSelect("object-1", "replace")
    assert calls == [(["object-1"], "replace")]
    sel.rowSelect("object-2", "add")
    # add/subtract auto-start a staging range so stacking always has a base
    assert editor.hasSelectionDraft
    assert calls[-1] == (["object-2"], "add")
    sel.rowSelect("object-2", "bogus")
    assert len(calls) == 2
    editor.discardSelection()


def test_layer_parameter_edits_target_layer_and_undo(editor):
    e = editor
    e.addLayer()
    target = e.activeLayerId
    e.selectLayer(e._layers[0]["id"])
    sel = e.selection
    sel.setLayerParameter(target, "exposure", 0.8)
    sel.finishLayerGesture(target)
    wait_for(lambda: settled(e))
    layer = next(l for l in e._layers if l["id"] == target)
    assert layer["recipe"]["exposure"] == 0.8
    assert "exposure" in layer["locked"]
    assert e._recipe["exposure"] == 0  # active layer untouched
    e.undo()
    wait_for(lambda: settled(e))
    layer = next(l for l in e._layers if l["id"] == target)
    assert layer["recipe"]["exposure"] == 0


def test_layer_parameter_rejects_groups_and_bad_keys(editor):
    e = editor
    e.groupLayer()
    gid = e.activeLayerId
    e.selection.setLayerParameter(gid, "exposure", 1.0)
    assert e.selection.layerRecipe(gid) == {}
    e.selectLayer(e._layers[0]["id"])
    e.selection.setLayerParameter(e.activeLayerId, "nope", 1.0)
    assert "nope" not in e._recipe


def test_focusing_current_child_reveals_ancestors_without_render_or_undo(editor):
    from iphoto.document import new_layer

    e = editor
    target = e._layer()
    outer = new_layer("外组", True, kind="group")
    inner = new_layer("内组", True, outer["id"], "group")
    outer["collapsed"] = inner["collapsed"] = True
    target["parent_id"] = inner["id"]
    e._layers.extend([outer, inner])
    e._commit()
    e._change()
    wait_for(lambda: settled(e))
    before = (e._cursor, e._generation, e._serial)
    content = [{k: v for k, v in layer.items() if k != "collapsed"} for layer in deepcopy(e._layers)]
    e.selection.pickLayer(target["id"])
    assert not outer["collapsed"] and not inner["collapsed"]
    assert e.selection.pickedLayerId == target["id"]
    assert target["id"] in {row["id"] for row in e.layers}
    assert (e._cursor, e._generation, e._serial) == before
    assert not e._timer.isActive()
    assert [{k: v for k, v in layer.items() if k != "collapsed"} for layer in e._layers] == content
