"""Exact manual values commit once and cannot escape their original context."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt

from iphoto.document import read_project
from iphoto.engine import RANGES
from test_ai import configure, mock_api, wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_preset_focus_ui import contained
from test_selection_controller import editor  # noqa: F401


def snapshot(e):
    return deepcopy((e._layers, e._history, e._cursor, e._generation, e.dirty, e.lockedFields))


def apply(e, key, text, **binding):
    return e.selection.applyParameterText(binding.get('layer', e.activeLayerId),
                                         binding.get('photo', e.originalUrl),
                                         binding.get('generation', e.documentGeneration), key, text)


@pytest.mark.parametrize('key', list(RANGES))
def test_all_supported_values_use_same_lock_history_and_undo_contract(editor, key):  # noqa: F811
    before = deepcopy(editor._layers)
    cursor, generation = editor._cursor, editor._generation
    value = -.37 if key == 'exposure' else RANGES[key][1]
    assert apply(editor, key, f' {value} ')['ok']
    wait_for(lambda: settled(editor))
    assert editor.parameters[key] == value and key in editor.lockedFields
    assert editor._cursor == cursor+1 and editor._generation == generation+1
    unchanged = snapshot(editor)
    assert apply(editor, key, str(value))['ok'] and snapshot(editor) == unchanged
    editor.undo();wait_for(lambda: settled(editor))
    assert editor._layers == before and editor._cursor == cursor


@pytest.mark.parametrize('key,text', [
    ('skin_smoothing','101'), ('skin_smoothing','-1'), ('skin_smoothing','1.5'),
    ('exposure','2.001'), ('exposure','-2.001'), ('exposure',''),
    ('warmth','nan'), ('warmth','inf'), ('warmth','1e1'), ('warmth','1_0'),
    ('warmth','--1'), ('warmth','12px'), ('warmth','1'*33), ('warmth',None),
])
def test_invalid_numbers_do_not_clamp_lock_render_or_commit(editor, key, text):  # noqa: F811
    before, status = snapshot(editor), editor.status
    outcome = apply(editor, key, text)
    assert not outcome['ok'] and outcome['message']
    assert snapshot(editor) == before and editor.status == status


@pytest.mark.parametrize('kind', ['layer','photo','generation','bool_generation','unknown_key','busy','draft','group'])
def test_stale_or_blocked_submission_never_writes(editor, monkeypatch, kind):  # noqa: F811
    kwargs, key = {}, 'exposure'
    if kind == 'layer':kwargs['layer'] = 'missing-layer'
    elif kind == 'photo':kwargs['photo'] = 'file:///other-photo.png'
    elif kind == 'generation':kwargs['generation'] = editor.documentGeneration-1
    elif kind == 'bool_generation':kwargs['generation'] = True
    elif kind == 'unknown_key':key = 'unknown'
    elif kind == 'busy':monkeypatch.setattr(editor, '_can_edit', lambda: False)
    elif kind == 'draft':
        editor.beginSelection('empty');wait_for(lambda: settled(editor))
    elif kind == 'group':
        editor.groupLayer();wait_for(lambda: settled(editor))
    before = snapshot(editor)
    assert not apply(editor, key, '.37', **kwargs)['ok']
    assert snapshot(editor) == before


def reveal(ui, key):
    ui.e.selection.pickLayer(ui.e.activeLayerId)
    ui.e.selection.parameterFocusRequested.emit(ui.e.activeLayerId, key)
    field = ui.find('parameterValue_'+key)
    wait_for(lambda: contained(field, ui.find('propertyScroll')))
    text_height = field.height()-field.property('topPadding')-field.property('bottomPadding')
    assert field.property('contentHeight') <= text_height
    return field


def type_number(ui, key, text):
    field = reveal(ui, key)
    ui.click('parameterValue_'+key)
    assert field.property('activeFocus') and ui.w.property('textFocus')
    ui.key(Qt.Key_A, Qt.ControlModifier)
    ui.type(text)
    return field


@pytest.mark.parametrize('mode', ['enter','blur','save','save_as'])
def test_actual_input_is_local_until_single_commit_and_saves_latest_value(canvas, tmp_path, mode):  # noqa: F811
    ui = canvas
    reveal(ui, 'exposure')
    before = snapshot(ui.e)
    field = type_number(ui, 'exposure', '-.37')
    assert snapshot(ui.e) == before
    cursor, generation = ui.e._cursor, ui.e._generation
    if mode == 'enter':ui.key(Qt.Key_Return)
    elif mode == 'blur':ui.click('photoCanvas')
    else:
        ui.key(Qt.Key_S, Qt.ControlModifier | (Qt.ShiftModifier if mode == 'save_as' else Qt.NoModifier))
        dialog = ui.find('projectSaveDialog')
        wait_for(lambda: dialog.property('opened'))
        target = tmp_path/'input-save.iphoto'
        dialog.setProperty('filePath', str(target));ui.click('projectSaveConfirmButton')
        wait_for(lambda: target.exists() and not ui.e.savingProject)
        assert read_project(target)['layers'][0]['recipe']['exposure'] == -.37
    wait_for(lambda: settled(ui.e))
    assert ui.e.parameters['exposure'] == -.37 and field.property('text') == '-0.37'
    assert ui.e._cursor == cursor+1 and ui.e._generation == generation+1
    assert 'exposure' in ui.e.lockedFields
    ui.click('photoCanvas');ui.key(Qt.Key_Z, Qt.ControlModifier)
    wait_for(lambda: settled(ui.e))
    assert ui.e._layers == before[0] and ui.e._cursor == cursor


def test_invalid_enter_and_save_preserve_document_then_escape_discards_input(canvas):  # noqa: F811
    ui = canvas
    reveal(ui, 'skin_smoothing')
    before = snapshot(ui.e)
    field = type_number(ui, 'skin_smoothing', '101')
    ui.key(Qt.Key_Return)
    assert field.property('errorMessage') and field.property('text') == '101'
    assert snapshot(ui.e) == before
    ui.key(Qt.Key_S, Qt.ControlModifier)
    assert not ui.find('projectSaveDialog').property('opened') and snapshot(ui.e) == before
    ui.key(Qt.Key_Escape)
    assert field.property('text') == '0' and not field.property('errorMessage')
    assert snapshot(ui.e) == before and not ui.w.property('textFocus')


def test_actual_layer_switch_cancels_pending_text_without_touching_other_layer(canvas):  # noqa: F811
    ui = canvas
    first = ui.e.activeLayerId
    ui.e.addGlobalLayer();wait_for(lambda: settled(ui.e))
    second = ui.e.activeLayerId
    field = type_number(ui, 'exposure', '.37')
    before = deepcopy((ui.e._layers, ui.e._history, ui.e._cursor))
    ui.click('layerSelect_'+first);wait_for(lambda: settled(ui.e))
    assert ui.e.activeLayerId == first and first != second
    assert (ui.e._layers, ui.e._history, ui.e._cursor) == before
    assert field.property('text') == '0.00' and not field.property('editing')


def test_later_parameter_edit_discards_pending_text_instead_of_overwriting_new_result(canvas):  # noqa: F811
    ui = canvas
    field = type_number(ui, 'exposure', '.37')
    ui.e.setParameter('exposure', .6);ui.e.finishGesture()
    wait_for(lambda: settled(ui.e))
    before = snapshot(ui.e)
    ui.key(Qt.Key_Return)
    assert field.property('text') == '0.60' and snapshot(ui.e) == before


def test_ai_wait_disables_and_discards_uncommitted_numeric_text(canvas):  # noqa: F811
    ui = canvas
    field = type_number(ui, 'exposure', '.37')
    before = deepcopy((ui.e._layers, ui.e._history, ui.e._cursor, ui.e._generation))
    with mock_api(delay=.15) as (url, requests):
        configure(ui.e.ai, url)
        assert ui.e.sendMessage('只给建议', 'advice')
        wait_for(lambda: ui.e.ai.busy and not field.property('enabled'))
        assert field.property('text') == '0.00' and not field.property('editing')
        wait_for(lambda: not ui.e.ai.busy)
        assert len(requests) == 1
        assert (ui.e._layers, ui.e._history, ui.e._cursor, ui.e._generation) == before
