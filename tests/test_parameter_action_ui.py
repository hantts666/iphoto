"""Save/export cannot silently discard invalid values when taking input focus."""
from copy import deepcopy
import hashlib

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.document import read_project, render_layers
from iphoto.engine import load_source
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_parameter_entry import snapshot, type_number


ACTIONS = ['save_button', 'export_button', 'save_key', 'save_as_key', 'export_key',
           'save_menu', 'save_as_menu', 'export_menu']


def action(ui, name):
    if name.endswith('_button'):
        ui.click('exportButton' if name.startswith('export') else 'saveProjectButton')
    elif name.endswith('_key'):
        ui.key(Qt.Key_E if name.startswith('export') else Qt.Key_S,
               Qt.ControlModifier | (Qt.ShiftModifier if name=='save_as_key' else Qt.NoModifier))
    else:
        stack = [ui.w.contentItem()]
        while stack:
            item = stack.pop()
            if item.property('text') == '文件' and 'MenuBarItem' in item.metaObject().className():
                point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
                QTest.mouseClick(ui.w, Qt.LeftButton, Qt.NoModifier, point);QTest.qWait(40)
                break
            stack.extend(item.childItems())
        else:raise AssertionError('visible File menu entry')
        ui.click({'save_menu':'saveProjectMenuAction', 'save_as_menu':'saveAsProjectMenuAction',
                  'export_menu':'exportMenuAction'}[name])


@pytest.mark.parametrize('name', ACTIONS)
def test_invalid_action_preserves_input_then_corrected_value_is_saved_or_exported(canvas, tmp_path, name):  # noqa: F811
    ui = canvas
    field = type_number(ui, 'exposure', '3')
    before = snapshot(ui.e)
    source = load_source(ui.e._path)
    action(ui, name)
    assert not ui.find('projectSaveDialog').property('opened')
    assert not ui.find('exportDialog').property('opened')
    assert field.property('text')=='3' and field.property('errorMessage') and field.property('editing')
    assert field.property('activeFocus') and not ui.w.property('menuActive')
    assert snapshot(ui.e)==before
    ui.key(Qt.Key_A, Qt.ControlModifier);ui.type('.37')
    assert snapshot(ui.e)==before and not field.property('errorMessage')
    action(ui, name)
    wait_for(lambda: settled(ui.e))
    assert ui.e.parameters['exposure']==.37 and field.property('text')=='0.37'
    assert ui.e._cursor==before[2]+1 and ui.e._generation==before[3]+1
    assert 'exposure' in ui.e.lockedFields
    applied = snapshot(ui.e)
    if name.startswith('export'):
        dialog = ui.find('exportDialog');assert dialog.property('opened')
        target = tmp_path/'parameter-output.png';assert not target.exists()
        dialog.setProperty('formatIndex',1);dialog.setProperty('filePath',str(target))
        ui.click('exportConfirmButton')
        wait_for(lambda: not dialog.property('pending') and ui.e._export_request is None)
        assert target.exists() and not dialog.property('opened')
        with Image.open(target) as result:
            assert result.size==source.image.size
            assert result.tobytes()==render_layers(source.image,ui.e._layers).tobytes()
        assert snapshot(ui.e)==applied
    else:
        dialog = ui.find('projectSaveDialog');assert dialog.property('opened')
        target = tmp_path/'parameter-save.iphoto';assert not target.exists()
        dialog.setProperty('filePath',str(target));ui.click('projectSaveConfirmButton')
        wait_for(lambda: not ui.e.savingProject)
        assert ui.e.projectPath==str(target) and not dialog.property('opened')
        assert read_project(target)['layers']==ui.e._layers
        assert (ui.e._layers,ui.e._history,ui.e._cursor,ui.e._generation,ui.e.lockedFields)==(
            applied[0],applied[1],applied[2],applied[3],applied[5])
    assert hashlib.sha256(source.path.read_bytes()).hexdigest()==source.digest
    ui.click('canvasSurface');ui.key(Qt.Key_Z,Qt.ControlModifier)
    wait_for(lambda: settled(ui.e))
    assert ui.e._layers==before[0] and ui.e._cursor==before[2]


def test_invalid_blur_keeps_visible_text_and_later_export_returns_to_error(canvas):  # noqa: F811
    ui = canvas
    field = type_number(ui,'exposure','3')
    before = snapshot(ui.e)
    ui.click('canvasSurface')
    assert not field.property('activeFocus') and field.property('text')=='3'
    assert field.property('editing') and field.property('errorMessage') and snapshot(ui.e)==before
    ui.click('tool_hand')
    assert ui.e.selection.tool=='hand' and field.property('text')=='3' and snapshot(ui.e)==before
    ui.key(Qt.Key_E,Qt.ControlModifier)
    assert not ui.find('exportDialog').property('opened') and field.property('activeFocus')
    assert field.property('text')=='3' and field.property('errorMessage') and snapshot(ui.e)==before
    ui.key(Qt.Key_Escape)
    assert field.property('text')=='0.00' and not field.property('editing') and not field.property('errorMessage')
    assert snapshot(ui.e)==before
    ui.key(Qt.Key_E,Qt.ControlModifier)
    assert ui.find('exportDialog').property('opened') and snapshot(ui.e)==before


def test_changed_layer_discards_invalid_blurred_text_instead_of_blocking_new_context(canvas):  # noqa: F811
    ui = canvas
    first = ui.e.activeLayerId
    ui.e.addGlobalLayer();wait_for(lambda: settled(ui.e))
    field = type_number(ui,'exposure','3');ui.click('canvasSurface')
    before = deepcopy((ui.e._layers,ui.e._history,ui.e._cursor))
    ui.click('layerSelect_'+first);wait_for(lambda: settled(ui.e))
    assert ui.e.activeLayerId==first and field.property('text')=='0.00'
    assert not field.property('editing') and not field.property('errorMessage')
    assert (ui.e._layers,ui.e._history,ui.e._cursor)==before
    ui.key(Qt.Key_E,Qt.ControlModifier)
    assert ui.find('exportDialog').property('opened')


def test_changed_photo_discards_invalid_blurred_text_and_can_export_new_photo(canvas, tmp_path):  # noqa: F811
    ui = canvas
    field = type_number(ui,'exposure','3');ui.click('canvasSurface')
    target = tmp_path/'another-photo.png';Image.new('RGB',(700,500),(31,83,112)).save(target)
    ui.e.openImage(str(target));wait_for(lambda: ui.e._path==str(target) and settled(ui.e))
    assert field.property('text')=='0.00' and not field.property('editing') and not field.property('errorMessage')
    before = snapshot(ui.e)
    ui.key(Qt.Key_E,Qt.ControlModifier)
    assert ui.find('exportDialog').property('opened') and snapshot(ui.e)==before
