"""Repair opacity, bounded solver arrays and source-detail composition."""

from copy import deepcopy

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest

from iphoto.document import new_layer, render_layers, render_detail_tile, heal_region_mask, raster_mask
from iphoto.engine import Recipe, render
from iphoto.inpainting import inpaint_image
from iphoto.preview_cache import LayerPreviewCache
from test_ai import wait_for
from test_canvas_ui import canvas as shared_canvas
from test_editor import settled
from test_heal import scratch_image, heal_layer

canvas = shared_canvas


def visual_item(window, name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


@pytest.mark.parametrize("opacity", [0, .25, .5, .8, 1])
def test_healing_strength_uses_one_layer_blend_and_cache_matches(opacity):
    source = scratch_image()
    layer = heal_layer()
    full = render_layers(source, [layer])
    layer["opacity"] = opacity
    expected = Image.composite(full, source, Image.new("L", source.size, round(255 * opacity)))
    cache = LayerPreviewCache()
    assert np.array_equal(render_layers(source, [layer]), expected)
    assert np.array_equal(cache.render(source, [layer]), expected)
    assert np.array_equal(cache.render(source, [layer]), expected)
    layer["visible"] = False
    assert np.array_equal(cache.render(source, [layer]), source)


def test_heal_and_local_tone_effect_share_opacity_once():
    source = scratch_image()
    layer = heal_layer()
    layer["mask"].update(ops=[{"kind": "rect", "mode": "add", "points": [[.45, .2], [.8, .8]]}], feather=.02)
    layer["recipe"] = Recipe(warmth=30, exposure=-.5).to_dict()
    plain_heal = heal_layer()
    healed = render_layers(source, [plain_heal])
    tones = render(healed, Recipe.from_dict(layer["recipe"]))
    combined = Image.composite(tones, healed, raster_mask(layer["mask"], source.size))
    layer["opacity"] = .5
    expected = Image.composite(combined, source, Image.new("L", source.size, 128))
    assert np.array_equal(render_layers(source, [layer]), expected)
    # Old empty adjustment masks must continue to permit their brush strokes.
    assert expected.getpixel((70, 60))[0] > source.getpixel((70, 60))[0]
    assert expected.getpixel((10, 10)) == source.getpixel((10, 10))


def test_group_mask_and_opacity_limit_whole_repair_effect():
    source = scratch_image()
    layer = heal_layer()
    group = new_layer("修复组", kind="group")
    group["mask"]["ops"] = [{"kind": "rect", "mode": "add", "points": [[.2, .2], [.4, .8]]}]
    group["opacity"] = .5
    layer["parent_id"] = group["id"]
    full = render_layers(source, [heal_layer()])
    mask = raster_mask(group["mask"], source.size).point([round(v * .5) for v in range(256)])
    expected = Image.composite(full, source, mask)
    records = [group, layer]
    assert np.array_equal(render_layers(source, records), expected)
    assert np.array_equal(LayerPreviewCache().render(source, records), expected)
    assert expected.getpixel((95, 60)) == source.getpixel((95, 60))


def full_solver(source, region, method, radius):
    rgb = np.asarray(source.convert("RGB"))
    mask = np.where(np.asarray(region.convert("L")) > 8, 255, 0).astype("uint8")
    bgr = cv2.inpaint(rgb[..., ::-1].copy(), mask, radius,
                      cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA)
    result = Image.fromarray(bgr[..., ::-1])
    if source.mode == "RGBA":
        result.putalpha(source.getchannel("A"))
    return result


@pytest.mark.parametrize("method", ["telea", "ns"])
@pytest.mark.parametrize("location", ["center", "edge", "corners", "all"])
def test_bounded_solver_matches_whole_image_with_edges_and_multiple_holes(method, location):
    rng = np.random.default_rng(23)
    source = Image.fromarray(rng.integers(0, 255, (210, 310, 3), dtype="uint8"))
    region = Image.new("L", source.size)
    draw = ImageDraw.Draw(region)
    if location == "center":
        draw.ellipse((130, 85, 155, 108), fill=255)
    elif location == "edge":
        draw.rectangle((0, 70, 18, 100), fill=255)
    elif location == "corners":
        draw.rectangle((1, 1, 12, 12), fill=255)
        draw.ellipse((275, 177, 300, 200), fill=255)
    else:
        draw.rectangle((0, 0, source.width, source.height), fill=255)
    expected = full_solver(source, region, method, 12)
    result = inpaint_image(source, region, {"method": method, "radius": 12})
    assert np.array_equal(result, expected)
    zeros = np.array(region) <= 8
    assert np.array_equal(np.array(result)[zeros], np.array(source)[zeros])


def test_bounded_solver_keeps_alpha_and_thresholded_pixels():
    pixels = np.random.default_rng(34).integers(0, 255, (250, 320, 4), dtype="uint8")
    source = Image.fromarray(pixels, "RGBA")
    region = Image.new("L", source.size)
    ImageDraw.Draw(region).ellipse((125, 95, 139, 109), fill=255)
    region.putpixel((10, 10), 8)
    region.putpixel((11, 10), 9)
    result = inpaint_image(source, region, {"method": "telea", "radius": 5})
    assert np.array_equal(result, full_solver(source, region, "telea", 5))
    assert result.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    assert result.getpixel((10, 10)) == source.getpixel((10, 10))


def test_tiny_hole_never_sends_full_image_to_opencv(monkeypatch):
    source = Image.new("RGB", (4016, 6016), (150, 120, 95))
    region = Image.new("L", source.size)
    ImageDraw.Draw(region).ellipse((1974, 2974, 1994, 2994), fill=255)
    arrays = []
    solver = cv2.inpaint
    def record(image, mask, radius, flags):
        arrays.append((image.shape, mask.shape))
        return solver(image, mask, radius, flags)
    monkeypatch.setattr(cv2, "inpaint", record)
    inpaint_image(source, region, {"method": "telea", "radius": 5})
    assert len(arrays) == 1
    assert arrays[0][0][:2] == arrays[0][1] == (49, 49)


def test_empty_mask_avoids_solver_and_wrong_dimensions_are_rejected(monkeypatch):
    def unavailable(*args):
        raise AssertionError("No hole should call the solver")
    monkeypatch.setattr(cv2, "inpaint", unavailable)
    source = Image.new("RGBA", (140, 90), (20, 40, 60, 100))
    region = Image.new("L", source.size, 8)
    assert np.array_equal(inpaint_image(source, region, {}), source)
    with pytest.raises(ValueError, match="尺寸"):
        inpaint_image(source, Image.new("L", (100, 100)), {})


def test_source_detail_matches_full_repair_with_local_color_mask_and_partial_opacity():
    source = Image.fromarray(np.random.default_rng(3).integers(30, 230, (360, 480, 3), dtype="uint8"))
    layer = new_layer("修复与调色")
    layer["heal"] = {"ops": [{"kind": "heal", "points": [[.4, .4], [.46, .45]], "radius": .015}]}
    layer["mask"].update(feather=.02, ops=[{"kind": "ellipse", "mode": "add", "points": [[.25, .2], [.65, .7]]}])
    layer["recipe"] = Recipe(exposure=.2, warmth=10).to_dict()
    layer["opacity"] = .45
    box = (150, 100, 340, 260)
    expected = render_layers(source, [layer]).crop(box)
    actual = render_detail_tile(source, [layer], box)
    assert np.array_equal(expected, actual)


def test_pixel_size_control_healing_entry_and_tool_memory_with_real_keys(canvas):
    ui = canvas
    e = ui.e
    e.selection.setBrushRadius(.08)
    before = (deepcopy(e._layers), e._generation, e._cursor)
    ui.click("tool_heal")
    assert not e.selection.showMask and not e.hasSelectionDraft
    assert e.selection.brushRadius == .003
    field = ui.find("brushDiameterInput")
    assert field.isVisible() and field.property("editable")
    content = field.property("contentItem")
    maximum_width = QFontMetricsF(content.property("font")).horizontalAdvance(str(e.selection.maxBrushDiameter))
    assert content.property("width") >= maximum_width + 2
    ui.click("brushDiameterInput")
    ui.key(Qt.Key_A, Qt.ControlModifier)
    ui.type("20")
    ui.key(Qt.Key_Return)
    assert e.selection.brushDiameter == 20
    assert e.selection.brushRadius == pytest.approx(20 / (2 * 1600))
    assert (e._layers, e._generation, e._cursor) == before
    ui.click("tool_brush")
    assert e.selection.brushRadius == .08
    e.discardSelection()
    ui.click("tool_heal")
    assert e.selection.brushDiameter == 20 and not e.selection.showMask
    e.selection.adjustBrush(-1)
    assert e.selection.brushDiameter == 18
    e.selection.setBrushDiameter(1)
    assert e.selection.brushDiameter == 2
    assert e.selection.brushRadius == pytest.approx(1 / 1600)
    e.selection.setBrushDiameter(10000)
    assert e.selection.brushRadius == .025


def test_actual_stroke_and_opacity_slider_repair_only_drawn_range(canvas, tmp_path):
    ui = canvas
    source = tmp_path / "scratch.jpg"
    image = Image.new("RGB", (2400, 1600), (165, 135, 105))
    ImageDraw.Draw(image).ellipse((1193, 793, 1207, 807), fill=(35, 25, 15))
    image.save(source, quality=95)
    ui.e.openImage(str(source))
    wait_for(lambda: ui.e.imageName == source.name and settled(ui.e) and ui.w.property("previewReady"))
    name = "layerSelect_" + ui.e.activeLayerId
    wait_for(lambda: visual_item(ui.w, name) is not None)
    row = visual_item(ui.w, name)
    assert row.isVisible()
    QTest.mouseClick(ui.w, Qt.LeftButton, Qt.NoModifier,
                    row.mapToScene(row.boundingRect().center()).toPoint())
    QTest.qWait(40)
    ui.click("tool_heal")
    ui.e.selection.setBrushDiameter(30)
    original_layer = deepcopy(ui.e._layer())
    point = ui.point("photoCanvas", .5, .5)
    QTest.mouseClick(ui.w, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: "heal" in ui.e._layer() and settled(ui.e))
    healed = deepcopy(ui.e._layers)
    assert healed[0] == original_layer and len(healed) == 2
    assert healed[-1]["heal"]["ops"][0]["radius"] == pytest.approx(30 / (2 * 1600))
    assert not ui.find("layerOpacitySlider").isVisible()
    assert ui.find("repairStrengthSlider").isVisible()
    ui.click("repairStrengthSlider")
    wait_for(lambda: settled(ui.e))
    assert ui.e._layer()["opacity"] == .5
    full = render_layers(image, healed)
    actual = render_layers(image, ui.e._layers)
    expected = Image.composite(full, image, Image.new("L", image.size, 128))
    assert np.array_equal(actual, expected)
    mask = np.array(heal_region_mask(healed[-1]["heal"], image.size))
    assert np.array_equal(np.array(actual)[mask == 0], np.array(image)[mask == 0])
    ui.e.undo()
    wait_for(lambda: settled(ui.e))
    assert ui.e._layers == healed
