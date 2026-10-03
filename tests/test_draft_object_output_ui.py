"""Draft output owns the main action; cached objects remain explicit sources."""
from copy import deepcopy

from PIL import Image, ImageDraw

from iphoto.document import raster_mask
from iphoto.segmentation.classical import bitmap_mask
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_scene_object_actions_ui import install, reveal


def corrected_mask():
    image = Image.new("L", (300, 200), 0)
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 35, 105, 125), fill=255)
    draw.rectangle((50, 55, 90, 100), fill=0)
    return bitmap_mask(image, "已修正的对象与孔洞")


def test_corrected_range_has_one_main_action_and_exact_mask_is_applied(canvas):  # noqa: F811
    ui = canvas
    install(ui, warnings=True)
    ui.click("sceneSelect_object-1")
    wait_for(lambda: settled(ui.e) and ui.w.property("selectionPreviewReady"))
    mask = corrected_mask()
    ui.e._set_candidate(mask)  # Controlled delivery of a completed correction.
    wait_for(lambda: settled(ui.e) and ui.w.property("selectionPreviewReady"))
    assert ui.e.checkedObjectCount == 1
    assert not ui.find("adjustCheckedObjectsButton").isVisible()
    assert ui.find("selectionToLayerButton").isVisible()
    assert ui.find("objectCombineMenuButton").isVisible()
    before, cursor = deepcopy(ui.e._layers), ui.e._cursor
    wait_for(lambda: ui.find("selectionToLayerButton").property("enabled"))
    ui.click("selectionToLayerButton")
    wait_for(lambda: settled(ui.e))
    assert ui.e._layer()["mask"]["bitmap"] == mask["bitmap"]
    assert ui.e._layer()["mask"]["bitmap"] != ui.e._scene.precise["object-1"]["mask"]["bitmap"]
    assert ui.e._cursor == cursor + 1 and not ui.e.hasSelectionDraft
    ui.e.undo()
    wait_for(lambda: settled(ui.e))
    assert ui.e._layers == before


def test_explicit_object_add_uses_draft_hole_and_can_be_undone(canvas):  # noqa: F811
    ui = canvas
    install(ui)
    mask = corrected_mask()
    ui.e._set_candidate(mask)
    wait_for(lambda: settled(ui.e) and ui.w.property("selectionPreviewReady"))
    assert not ui.find("adjustCheckedObjectsButton").isVisible()
    assert not ui.find("objectCombineMenuButton").isVisible()
    reveal(ui, "sceneCheck_object-2")
    ui.click("sceneCheck_object-2")
    assert ui.e.checkedObjectCount == 1 and not ui.find("adjustCheckedObjectsButton").isVisible()
    assert ui.find("objectCombineMenuButton").isVisible()
    ui.click("objectCombineMenuButton")
    ui.click("addObjectsButton")
    wait_for(lambda: settled(ui.e) and ui.w.property("selectionPreviewReady"))
    pixels = raster_mask(ui.e._candidate, (300, 200))
    assert pixels.getpixel((60, 80)) == 0  # Existing protected hole survives.
    assert pixels.getpixel((220, 80)) == 255  # New object actually added.
    assert ui.e._cursor == 0 and len(ui.e._layers) == 1
    ui.e.undo()
    wait_for(lambda: settled(ui.e))
    assert ui.e._candidate == mask


def test_discarding_draft_restores_direct_batch_action(canvas):  # noqa: F811
    ui = canvas
    install(ui)
    assert ui.find("adjustCheckedObjectsButton").isVisible()
    assert not ui.find("adjustCheckedObjectsButton").property("enabled")
    ui.click("sceneSelect_object-1")
    wait_for(lambda: settled(ui.e) and ui.w.property("selectionPreviewReady"))
    assert not ui.find("adjustCheckedObjectsButton").isVisible()
    ui.click("discardSelectionButton")
    wait_for(lambda: settled(ui.e))
    assert ui.find("adjustCheckedObjectsButton").isVisible()
    assert ui.find("adjustCheckedObjectsButton").property("enabled")
    assert not ui.find("selectionToLayerButton").isVisible()
    ui.click("adjustCheckedObjectsButton")
    wait_for(lambda: settled(ui.e))
    assert not ui.e.hasSelectionDraft and len(ui.e._layers) == 2
    assert ui.e._layer()["mask"]["bitmap"] == ui.e._scene.precise["object-1"]["mask"]["bitmap"]


def test_existing_layer_range_uses_only_save_action_and_keeps_recipe(canvas):  # noqa: F811
    ui = canvas
    install(ui)
    ui.click("sceneCheck_object-1")
    ui.e.setParameter("exposure", .4)
    ui.e.finishGesture()
    wait_for(lambda: settled(ui.e))
    before, cursor, lid = deepcopy(ui.e._layers), ui.e._cursor, ui.e.activeLayerId
    ui.click("layerMaskThumb_" + lid)
    wait_for(lambda: settled(ui.e) and ui.e.hasSelectionDraft)
    mask = corrected_mask()
    ui.e._set_candidate(mask)
    wait_for(lambda: settled(ui.e) and ui.w.property("selectionPreviewReady"))
    assert ui.e.selection.editingLayerMask
    assert not ui.find("adjustCheckedObjectsButton").isVisible()
    assert ui.find("selectionToLayerButton").property("text") == "保存范围修改"
    wait_for(lambda: ui.find("selectionToLayerButton").property("enabled"))
    ui.click("selectionToLayerButton")
    wait_for(lambda: settled(ui.e))
    assert len(ui.e._layers) == 1 and ui.e.activeLayerId == lid
    assert ui.e._layer()["mask"]["bitmap"] == mask["bitmap"]
    assert ui.e.parameters["exposure"] == .4 and ui.e._cursor == cursor + 1
    ui.e.undo()
    wait_for(lambda: settled(ui.e))
    assert ui.e._layers == before
