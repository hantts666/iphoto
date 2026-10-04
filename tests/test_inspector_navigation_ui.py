"""Mode changes preserve independent user positions in the full window."""
from copy import deepcopy

import pytest
from PIL import Image
from PySide6.QtTest import QTest

from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_scene_object_actions_ui import reveal
from test_v14 import catalog


def populated(ui):
    data = catalog()
    original = data['objects']
    data['objects'] = [{**deepcopy(original[i % len(original)]), 'id':f'object-{i+1}',
                       'name':f'元素 {i+1}'} for i in range(12)]
    ui.e._scene.set(data);ui.e.changed.emit();QTest.qWait(80)


@pytest.mark.parametrize('chat', [False, True])
def test_range_and_adjustment_positions_survive_real_row_toggles_without_document_edits(canvas,chat):  # noqa: F811
    ui=canvas;ui.w.setProperty('chatOpen',chat);populated(ui)
    reveal(ui,'sceneSelect_object-12')
    range_flick=ui.find('propertyScroll').property('contentItem')
    range_y=range_flick.property('contentY');assert range_y>0
    lid=ui.e.activeLayerId
    before=deepcopy((ui.e._layers,ui.e._candidate,ui.e._cursor,ui.e._generation))
    ui.click('layerSelect_'+lid);QTest.qWait(80)
    ui.e.selection.parameterFocusRequested.emit(lid,'hsl_blue_hue')
    wait_for(lambda:ui.find('adjustmentSection_4').property('expanded'))
    QTest.qWait(120)
    adjustment_flick=ui.find('propertyScroll').property('contentItem')
    adjustment_y=adjustment_flick.property('contentY');assert adjustment_y>0
    ui.click('layerSelect_'+lid);QTest.qWait(120)
    assert ui.e.selection.pickedLayerId==''
    assert ui.find('propertyScroll').property('contentItem').property('contentY')==pytest.approx(range_y,abs=1)
    ui.click('layerSelect_'+lid);QTest.qWait(120)
    assert ui.e.selection.pickedLayerId==lid
    assert ui.find('propertyScroll').property('contentItem').property('contentY')==pytest.approx(adjustment_y,abs=1)
    # Parameter/render signals do not reset either independent user position.
    ui.e.changed.emit();QTest.qWait(80)
    assert adjustment_flick.property('contentY')==pytest.approx(adjustment_y,abs=1)
    assert deepcopy((ui.e._layers,ui.e._candidate,ui.e._cursor,ui.e._generation))==before


def test_new_photo_resets_range_and_adjustment_positions(canvas,tmp_path):  # noqa: F811
    ui=canvas;populated(ui);reveal(ui,'sceneSelect_object-12')
    assert ui.find('propertyScroll').property('contentItem').property('contentY')>0
    ui.click('layerSelect_'+ui.e.activeLayerId);QTest.qWait(80)
    ui.e.selection.parameterFocusRequested.emit(ui.e.activeLayerId,'hsl_blue_hue');QTest.qWait(150)
    assert ui.find('propertyScroll').property('contentItem').property('contentY')>0
    photo=tmp_path/'new-photo.png';Image.new('RGB',(900,600),(80,130,100)).save(photo)
    ui.e.openImage(str(photo));wait_for(lambda:settled(ui.e) and ui.w.property('previewReady'))
    QTest.qWait(120)
    assert ui.e.selection.pickedLayerId==''
    assert ui.find('propertyScroll').property('contentItem').property('contentY')==0
    ui.click('layerSelect_'+ui.e.activeLayerId);QTest.qWait(120)
    assert ui.find('propertyScroll').property('contentItem').property('contentY')==0
