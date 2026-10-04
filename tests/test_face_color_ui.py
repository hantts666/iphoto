"""New controls use the original UI, validation and transactional layer edits."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from iphoto.document import empty_mask
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_parameter_entry import type_number


def test_selective_colour_input_is_revealed_saved_and_undone_without_resetting_folds(canvas,tmp_path):  # noqa: F811
    ui=canvas;e=ui.e
    ui.find('adjustmentSection_1').setProperty('expanded',True)
    e.selection.pickLayer(e.activeLayerId)
    e.selection.parameterFocusRequested.emit(e.activeLayerId,'hsl_blue_hue')
    wait_for(lambda:ui.find('colorMixerChoice').property('currentIndex')==5)
    field=type_number(ui,'hsl_blue_hue','-64')
    before=deepcopy(e._layers);cursor=e._cursor
    assert e.parameters['hsl_blue_hue']==0 and field.property('editing')
    ui.key(Qt.Key_Return);wait_for(lambda:settled(e))
    assert e.parameters['hsl_blue_hue']==-64 and 'hsl_blue_hue' in e.lockedFields
    assert ui.find('adjustmentSection_1').property('expanded')
    assert e._cursor==cursor+1
    e.selection.parameterFocusRequested.emit(e.activeLayerId,'hsl_orange_lightness')
    wait_for(lambda:ui.find('colorMixerChoice').property('currentIndex')==1)
    assert e.parameters['hsl_blue_hue']==-64 and e.parameters['hsl_orange_hue']==0
    e.saveProject(str(tmp_path/'colors.iphoto'))
    from iphoto.document import read_project
    saved=read_project(tmp_path/'colors.iphoto')
    assert saved['schema_version']=='1.10' and saved['layers']==e._layers
    e.undo();wait_for(lambda:settled(e));assert e._layers==before
    assert not ui.w.property('textFocus')


def test_face_shortcut_reuses_layer_and_rejects_a_stale_face_id(canvas,pixel_protocol_stub):  # noqa: F811
    e=canvas.e
    face={'id':'local-face-1','name':'人脸 1','mask':{**empty_mask(),'label':'人脸 1',
          'ops':[{'kind':'polygon','mode':'add','points':[[.2,.2],[.5,.2],[.5,.5],[.2,.5]]}]},
          'anchor':[.3,.3],'skin_crop':[.1,.1,.6,.6],'mask_target':'face'}
    e._face_hints=[face];e.changed.emit();QTest.qWait(80)
    before=deepcopy(e._layers);cursor=e._cursor
    canvas.click('faceRetouch_smooth');wait_for(lambda:settled(e))
    assert len(e._layers)==len(before)+1 and e._layers[:-1]==before and e.parameters['skin_smoothing']==35
    assert e._cursor==cursor+1
    # The protocol fixture rasterizes a hint, not BiSeNet. Explicitly mark the
    # fixture's target before testing reuse; actual model masks are tested in QA.
    e._layer()['mask']['semantic_target']='face_skin'
    e._layer()['mask']['label']='人脸 1 · 面部皮肤'
    e._commit();before=deepcopy(e._layers);lid=e.activeLayerId;cursor=e._cursor
    e.selection.clearPick();wait_for(lambda:settled(e));QTest.qWait(150)
    canvas.click('faceRetouch_rosy');wait_for(lambda:settled(e))
    assert e.activeLayerId==lid and len(e._layers)==len(before) and e.parameters['skin_smoothing']==35
    assert e.parameters['hsl_orange_saturation']==5 and e._cursor==cursor+1
    e.undo();wait_for(lambda:settled(e));assert e._layers==before
    # Upgrading an old mask is a document edit even if smoothing is unchanged.
    cursor=e._cursor
    e.selection.retouchFace('local-face-1','smooth');wait_for(lambda:settled(e))
    assert e.activeLayerId==lid and e._cursor==cursor+1
    assert e._layer()['mask']['face_binding']=={'face_id':'local-face-1','source_sha256':e._sha}
    assert e.parameters==before[-1]['recipe']
    e.undo();wait_for(lambda:settled(e));assert e._layers==before
    e._face_hints=[]
    e.selection.retouchFace('local-face-1','refine')
    assert e._layers==before
