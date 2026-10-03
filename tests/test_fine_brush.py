"""Source-pixel precision across controls, repair storage and real workers."""
from copy import deepcopy
import hashlib

import numpy as np
from PIL import Image, ImageDraw
import pytest
from PySide6.QtCore import Qt
from PySide6.QtQml import QQmlProperty
from PySide6.QtTest import QTest

from iphoto.ai_repair import repair_context, validate_repairs
from iphoto.document import (MIN_STROKE_RADIUS, empty_mask, heal_region_mask,
                             new_layer, read_project, render_detail_tile,
                             render_layers, validate_layers, validate_mask)
from test_ai import configure, mock_api, wait_for
from test_ai_repair import auto, sent, spots
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_independent_repair import paint
from test_parameter_entry import snapshot
from test_selection_controller import editor  # noqa: F401


@pytest.mark.parametrize('tool', ['brush', 'heal'])
@pytest.mark.parametrize('size', [(160, 240), (1600, 2400), (4016, 6016), (6016, 4016)])
def test_pixel_minimum_and_small_steps_do_not_grow_with_source(editor, tmp_path, tool, size):  # noqa: F811
    e = editor
    path = tmp_path / 'different-size.jpg'
    Image.new('RGB', size, (140, 110, 90)).save(path)
    e.openImage(str(path)); wait_for(lambda: settled(e))
    e.selection.chooseTool(tool)
    before = snapshot(e)
    e.selection.setBrushDiameter(1)
    assert e.selection.minBrushDiameter == e.selection.brushDiameter == 2
    assert e.selection.brushRadius * min(size) == pytest.approx(1)
    step = 1 if tool == 'heal' else 2
    e.selection.adjustBrush(1)
    assert e.selection.brushDiameter == 2 + step
    e.selection.adjustBrush(-1)
    assert e.selection.brushDiameter == 2
    for value in (float('nan'), float('inf'), -float('inf'), True):
        e.selection.setBrushRadius(value)
        assert e.selection.brushDiameter == 2
    assert snapshot(e) == before


@pytest.mark.parametrize('tool', ['brush', 'heal'])
def test_tiny_photograph_keeps_finite_bounded_brush(editor, tmp_path, tool):  # noqa: F811
    e = editor
    path = tmp_path / 'tiny.png'; Image.new('RGB', (16, 12)).save(path)
    e.openImage(str(path)); wait_for(lambda: settled(e))
    e.selection.chooseTool(tool)
    for value in (0, 1, 10000):
        e.selection.setBrushDiameter(value)
        assert MIN_STROKE_RADIUS <= e.selection.brushRadius <= (.025 if tool == 'heal' else .15)
        assert e.selection.minBrushDiameter <= e.selection.brushDiameter <= e.selection.maxBrushDiameter


@pytest.mark.parametrize('radius', [0, -1, MIN_STROKE_RADIUS / 2, .201, True, float('nan'), float('inf')])
def test_finer_protocol_still_rejects_invalid_mask_and_heal_without_mutation(editor, radius):  # noqa: F811
    op = {'kind': 'heal', 'points': [[.5, .5]], 'radius': radius}
    mask = {**empty_mask(), 'ops': [{**op, 'kind': 'brush', 'mode': 'add'}]}
    layer = new_layer(); layer['heal'] = {'ops': [op]}
    with pytest.raises(ValueError): validate_mask(mask)
    with pytest.raises(ValueError): validate_layers([layer])
    before = snapshot(editor)
    assert not paint(editor, radius=radius) and snapshot(editor) == before


@pytest.mark.parametrize('tool', ['brush', 'heal'])
def test_actual_spin_arrows_and_canvas_brackets_share_small_pixel_steps(canvas, tool):  # noqa: F811
    ui = canvas
    ui.click('tool_' + tool); wait_for(lambda: settled(ui.e))
    before = deepcopy((ui.e._layers, ui.e._candidate, ui.e._generation, ui.e._cursor))
    ui.click('brushDiameterInput'); ui.key(Qt.Key_A, Qt.ControlModifier); ui.type('2'); ui.key(Qt.Key_Return)
    assert ui.e.selection.brushDiameter == 2
    field = ui.find('brushDiameterInput')
    step = 1 if tool == 'heal' else 2
    for indicator, expected in [('up.indicator', 2 + step), ('down.indicator', 2)]:
        item = QQmlProperty(field, indicator).read()
        point = item.mapToScene(item.boundingRect().center()).toPoint()
        assert 0 <= point.x() < ui.w.width() and 0 <= point.y() < ui.w.height()
        QTest.mouseClick(ui.w, Qt.LeftButton, Qt.NoModifier, point); QTest.qWait(40)
        assert ui.e.selection.brushDiameter == expected
    ui.find('photoCanvas').forceActiveFocus()
    ui.key(Qt.Key_BracketRight); assert ui.e.selection.brushDiameter == 2 + step
    ui.key(Qt.Key_BracketLeft); assert ui.e.selection.brushDiameter == 2
    ui.n.setZoom(1.); ui.n.centerOn(.5, .5)
    QTest.mouseMove(ui.w, ui.point('canvasSurface')); QTest.qWait(80)
    footprint = ui.find('brushFootprint')
    assert footprint.isVisible() and footprint.width() == pytest.approx(2)
    assert deepcopy((ui.e._layers, ui.e._candidate, ui.e._generation, ui.e._cursor)) == before


@pytest.mark.parametrize('mode', ['RGB', 'RGBA'])
def test_small_repair_render_cache_detail_and_alpha_preserve_every_outside_pixel(mode):
    from iphoto.preview_cache import LayerPreviewCache
    image = Image.new(mode, (1800, 1200), (150, 110, 90) if mode == 'RGB' else (150, 110, 90, 95))
    ImageDraw.Draw(image).point((900, 600), fill=(250, 20, 30) if mode == 'RGB' else (250, 20, 30, 95))
    layer = new_layer(); op = {'kind': 'heal', 'points': [[.5, .5]], 'radius': 1 / 1200}
    layer['heal'] = {'ops': [op]}
    layer['mask']['ops'] = [{**op, 'kind': 'brush', 'mode': 'add'}]
    layer = validate_layers([layer])[0]
    actual = render_layers(image, [layer]); original = np.array(image); pixels = np.array(actual)
    mask = np.array(heal_region_mask(layer['heal'], image.size)) > 0
    assert np.array_equal(pixels[~mask], original[~mask])
    assert not np.array_equal(pixels[mask], original[mask])
    assert np.linalg.norm(pixels[600, 900, :3].astype(float) - [150, 110, 90]) < 8
    if mode == 'RGBA': assert np.array_equal(pixels[:, :, 3], original[:, :, 3])
    assert LayerPreviewCache().render(image, [layer]).tobytes() == actual.tobytes()
    box = (860, 560, 940, 640)
    assert render_detail_tile(image, [layer], box).tobytes() == actual.crop(box).tobytes()


def test_real_ai_closeup_can_create_fine_stroke_export_save_reopen_remove_and_undo(editor, tmp_path):  # noqa: F811
    e = editor
    path = tmp_path / 'fine-spot.jpg'
    Image.new('RGB', (4016, 6016), (150, 110, 90)).save(path)
    e.openImage(str(path)); wait_for(lambda: settled(e))
    source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    before = deepcopy(e._layers); cursor = e._cursor
    part = {'name': '细小污点修复', 'reason': '检查明确的小污点', 'box': [450, 450, 550, 550]}
    context = repair_context(validate_repairs([part])[0], (4016, 6016))
    assert context['radius_bounds'][0] == 1
    def reply(payload):
        return auto(e.parameters, [part]) if sent(payload)['mode'] == 'auto' else spots(radius=1)
    with mock_api(reply) as (url, requests):
        configure(e.ai, url); assert e.sendMessage('只修复这个小污点，保留所有其他调整', 'auto')
        wait_for(lambda: not e.ai.busy and e._pending_request is None and settled(e), seconds=90)
        assert len(requests) == 2 and [sent(r[2])['mode'] for r in requests] == ['auto', 'repair']
        assert e.conversation[-1]['state'] == 'applied'
    op = e._layer()['heal']['ops'][0]
    assert MIN_STROKE_RADIUS <= op['radius'] < .001
    assert e._layers[:-1] == before and e._cursor == cursor + 1
    assert e._layer()['mask']['ops'] == [{**op, 'kind': 'brush', 'mode': 'add'}]
    healed = deepcopy(e._layers); lid = e.activeLayerId
    project = tmp_path / 'fine.iphoto'; e.saveProject(str(project))
    assert read_project(project)['layers'] == healed
    e.openProject(str(project)); wait_for(lambda: not e._pending_project and settled(e))
    assert e._layers == healed and e.activeLayerId == lid
    target = tmp_path / 'fine.png'; e.exportImage(str(target))
    wait_for(lambda: target.exists() and not e.busy and settled(e), seconds=90)
    with Image.open(path) as source, Image.open(target) as result:
        assert result.size == source.size and result.tobytes() == render_layers(source.convert('RGB'), healed).tobytes()
    e.selection.pickLayer(lid); assert e.selection.reviewRepairs([lid])
    assert e.selection.deleteReviewedRepair(e.selection.reviewedRepair['token'])
    wait_for(lambda: settled(e)); assert e._layers == before
    e.undo(); wait_for(lambda: settled(e)); assert e._layers == healed
    e.redo(); wait_for(lambda: settled(e)); assert e._layers == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_sha
