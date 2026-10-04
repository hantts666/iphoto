"""The complete window can preview and apply a channel alpha at both sizes."""
from copy import deepcopy
from PySide6.QtCore import Qt

from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_channel_matting import scene
from test_editor import settled


def test_channel_dialog_controls_publish_only_after_apply(canvas,tmp_path):  # noqa: F811
    ui=canvas
    image,_,mask,_=scene();path=tmp_path/'channels.png';image.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    ui.e._set_candidate(mask);wait_for(lambda:settled(ui.e))
    original=deepcopy(ui.e._layers);before=deepcopy(ui.e._candidate)
    ui.e.channelMask.open()
    wait_for(lambda:ui.e.channelMask.opened and not ui.e.channelMask.loading and ui.e.channelMask.previewUrl)
    wait_for(lambda:ui.find('channelMaskDialog').property('opened'))
    assert ui.e._layers==original and ui.e._candidate==before
    ui.click('channelUseAiBox');wait_for(lambda:not ui.e.channelMask.loading)
    assert ui.e.channelMask.options['ai'] is False
    ui.click('channelWhiteBox');ui.key(Qt.Key_A,Qt.ControlModifier);ui.type('210');ui.key(Qt.Key_Return)
    wait_for(lambda:ui.e.channelMask.options['white']==210 and not ui.e.channelMask.loading)
    wait_for(lambda:ui.find('channelApplyButton').property('enabled'))
    ui.click('channelApplyButton')
    wait_for(lambda:not ui.e.busy and settled(ui.e) and ui.e._candidate!=before,seconds=30)
    assert ui.e._layers==original and not ui.e.channelMask.opened
    final=deepcopy(ui.e._candidate)
    ui.e.undo();assert ui.e._candidate==before
    ui.e.redo();assert ui.e._candidate==final


def test_escape_from_channel_popup_cancels_owned_calculation(canvas,tmp_path):  # noqa: F811
    ui=canvas
    image,_,mask,_=scene();path=tmp_path/'channels.png';image.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    ui.e._set_candidate(mask);wait_for(lambda:settled(ui.e))
    before=deepcopy((ui.e._candidate,ui.e._layers,ui.e._draft_history,ui.e._generation))
    ui.e.channelMask.open();wait_for(lambda:not ui.e.channelMask.loading and ui.e.channelMask.previewUrl)
    wait_for(lambda:ui.find('channelMaskDialog').property('opened'))
    ui.click('channelApplyButton');wait_for(lambda:ui.e.matteBusy)
    ui.key(Qt.Key_Escape);wait_for(lambda:not ui.e.matteBusy and not ui.e.channelMask.opened)
    assert before==(ui.e._candidate,ui.e._layers,ui.e._draft_history,ui.e._generation)
