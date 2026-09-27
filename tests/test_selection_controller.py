"""SelectionController facade: tool state, task routing, refine routing, apply branches."""

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
    assert sel.brushRadius == pytest.approx(0.025 + 0.005)
    for _ in range(80):
        sel.adjustBrush(-1)
    assert sel.brushRadius == pytest.approx(0.003)
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
