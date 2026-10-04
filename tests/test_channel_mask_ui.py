"""The complete window can preview and apply a channel alpha at both sizes."""
from copy import deepcopy
from PySide6.QtCore import Qt,QMetaObject

from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_channel_matting import scene
from test_editor import settled
from test_scene_object_actions_ui import reveal


def test_channel_dialog_controls_publish_only_after_apply(canvas,tmp_path):  # noqa: F811
    ui=canvas
    image,_,mask,_=scene();path=tmp_path/'channels.png';image.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    ui.e._set_candidate(mask);wait_for(lambda:settled(ui.e))
    original=deepcopy(ui.e._layers);before=deepcopy(ui.e._candidate)
    ui.click('jumpToRefineButton')
    assert ui.find('refineAdvancedButton').property('text')=='高级…'
    assert ui.find('channelMaskButton').isVisible()
    reveal(ui,'channelMaskButton');ui.click('channelMaskButton')
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


def test_calculation_channel_can_be_selected_and_exports_native_gray(canvas,tmp_path):  # noqa: F811
    import numpy as np
    from iphoto.document import raster_mask
    from test_matte_review import calculation_scene
    ui=canvas;image,truth,mask=calculation_scene();path=tmp_path/'color-difference.png';image.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    ui.e._set_candidate(mask);wait_for(lambda:settled(ui.e))
    original=deepcopy(ui.e._layers)
    ui.e.channelMask.open();wait_for(lambda:not ui.e.channelMask.loading and ui.e.channelMask.previewUrl)
    wait_for(lambda:ui.find('channelMaskDialog').property('opened'))
    ui.click('channelChoiceBox');ui.key(Qt.Key_Home)
    for _ in range(5):ui.key(Qt.Key_Down)
    ui.key(Qt.Key_Return)
    wait_for(lambda:ui.e.channelMask.options['channel']=='red_green' and not ui.e.channelMask.loading)
    ui.click('channelUseAiBox');wait_for(lambda:not ui.e.channelMask.loading)
    ui.click('channelInteriorBox');wait_for(lambda:not ui.e.channelMask.loading)
    for field,value in [('channelBlackBox','88'),('channelWhiteBox','168')]:
        ui.click(field);ui.key(Qt.Key_A,Qt.ControlModifier);ui.type(value);ui.key(Qt.Key_Return)
        wait_for(lambda:not ui.e.channelMask.loading)
    wait_for(lambda:ui.find('channelApplyButton').property('enabled'))
    ui.click('channelApplyButton');wait_for(lambda:not ui.e.busy and settled(ui.e) and not ui.e.channelMask.opened)
    pixels=np.asarray(raster_mask(ui.e._candidate,image.size))/255
    assert np.abs(pixels-truth).mean()<.003 and ui.e._layers==original
    assert '红−绿' in ui.e.selectionQuality
    assert '处理边缘细化结果失败' not in ui.e.status


def test_main_menu_can_create_channel_range_without_prior_selection(canvas,tmp_path):  # noqa: F811
    import numpy as np
    from iphoto.document import raster_mask
    ui=canvas;image,truth,_,_=scene();path=tmp_path/'whole-photo.png';image.save(path)
    ui.e.openImage(str(path));wait_for(lambda:ui.e.hasImage and settled(ui.e))
    assert not ui.e.hasSelectionDraft
    original=deepcopy(ui.e._layers);cursor=ui.e._cursor
    assert QMetaObject.invokeMethod(ui.find('selectionMenu'),'open',Qt.DirectConnection)
    wait_for(lambda:ui.find('channelMaskMenuAction').isVisible())
    ui.click('channelMaskMenuAction')
    wait_for(lambda:ui.e.channelMask.opened and not ui.e.channelMask.loading and ui.e.channelMask.previewUrl)
    wait_for(lambda:ui.find('channelMaskDialog').property('opened'))
    assert ui.e.channelMask.options['whole'] and ui.e.channelMask.options['ai'] is False
    assert ui.find('channelApplyButton').property('text')=='生成选区'
    assert not ui.find('channelUseAiBox').isVisible()
    ui.click('channelChoiceBox');ui.key(Qt.Key_Home);ui.key(Qt.Key_Return)
    wait_for(lambda:ui.e.channelMask.options['channel']=='red' and not ui.e.channelMask.loading)
    for field,value in [('channelBlackBox','35'),('channelWhiteBox','210')]:
        ui.click(field);ui.key(Qt.Key_A,Qt.ControlModifier);ui.type(value);ui.key(Qt.Key_Return)
        wait_for(lambda:not ui.e.channelMask.loading)
    before=deepcopy(ui.e._candidate)
    wait_for(lambda:ui.find('channelMaskDialog').property('previewReady') and ui.find('channelApplyButton').property('enabled'))
    ui.click('channelApplyButton')
    wait_for(lambda:not ui.e.busy and settled(ui.e) and not ui.e.channelMask.opened)
    alpha=np.asarray(raster_mask(ui.e._candidate,image.size))/255
    assert np.abs(alpha-truth).max()<.004
    assert ui.e._layers==original and ui.e._cursor==cursor
    final=deepcopy(ui.e._candidate)
    ui.e.undo();assert ui.e._candidate==before
    ui.e.redo();assert ui.e._candidate==final
