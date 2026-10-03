"""Actual result inspection controls, live mask geometry and document protection."""
from copy import deepcopy

from PIL import Image, ImageDraw
from PySide6.QtCore import QUrl
from PySide6.QtTest import QSignalSpy, QTest
import pytest

from iphoto.controllers import adjustment_review
from iphoto.controllers.layers import addLocalLayers
from iphoto.document import empty_mask, new_layer, raster_mask, read_project, render_layers, validate_conversation
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_preset_focus_ui import contained


def done(editor):
    wait_for(lambda: not editor.ai.busy and not editor._pending_request and settled(editor))


def snapshot(editor):
    return deepcopy((editor._layers, editor._history, editor._cursor))


def center(viewport):
    return ((viewport._size[0]/2-viewport.imageX)/viewport.imageWidth,
            (viewport._size[1]/2-viewport.imageY)/viewport.imageHeight)


def local(editor, mask=None):
    layer=new_layer('局部效果')
    layer['recipe']=Recipe(warmth=20).to_dict()
    if mask is None:
        alpha=Image.new('L',(600,400));draw=ImageDraw.Draw(alpha)
        draw.rectangle((75,70,225,330),fill=255)
        draw.ellipse((120,150,185,235),fill=0)  # Empty interior must not be the inspection center.
        draw.rectangle((420,110,515,295),fill=255)
        draw.rectangle((8,8,9,9),fill=255)  # Insignificant speckle is not a third inspection stop.
        mask={**empty_mask(),'bitmap':encode_bitmap(alpha),'label':'两块范围与保护孔洞'}
    layer['mask']=mask
    prepared=addLocalLayers(editor,[layer]);done(editor)
    return prepared[0]


def test_disconnected_live_ranges_cycle_at_100_percent_and_preserve_pixels_history(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    layer=local(editor);editor.selection.toggleShowMask();ui.w.setProperty('compare',True)
    button=ui.find('reviewLayerAdjustmentButton')
    assert button.isVisible() and button.property('enabled')
    point=button.mapToScene(button.boundingRect().center())
    assert 0<point.x()<ui.w.width() and 0<point.y()<ui.w.height()
    before=snapshot(editor);centers=[]
    for _ in range(3):
        ui.click('reviewLayerAdjustmentButton');done(editor)
        assert editor.viewport.zoom==1 and not ui.w.property('compare') and not editor.selection.showMask, editor.status
        try:
            wait_for(lambda:ui.w.property('detailReady'))
        except AssertionError:
            view=ui.find('canvasViewport');detail=ui.find('detailImage')
            raise AssertionError({'status':editor.status,'zoom':editor.viewport.zoom,'center':center(editor.viewport),
                                  'url':editor.detailUrl,'box':editor._detail_box,'view':editor.viewport.visibleRect,
                                  'wanted':view.property('detailWanted'),'covers':view.property('detailCoversViewport'),
                                  'frameCurrent':detail.property('frameCurrent'),'visible':detail.isVisible()}) from None
        centers.append(center(editor.viewport))
        alpha=raster_mask(layer['mask'],(2400,1600))
        x,y=centers[-1]
        assert alpha.getpixel((round(x*2400),round(y*1600)))==255
        assert editor.viewport.zoom==1 and not editor.viewport.fitMode
        assert not editor.selection.showMask and not ui.w.property('compare') and editor.selection.tool=='inspect'
        assert snapshot(editor)==before and '局部效果' in editor.status
        with Image.open(QUrl(editor.detailUrl).toLocalFile()) as tile,Image.open(editor._path) as source:
            assert tile.tobytes()==render_layers(source,editor._layers).crop(editor._detail_box).tobytes()
    assert centers[0]==centers[2] and centers[0]!=centers[1]
    assert '1/2' in editor.status


def test_ai_message_cycles_each_created_layer_and_persists_live_references(canvas,tmp_path,pixel_protocol_stub):  # noqa: F811
    ui,editor=canvas,canvas.e
    ui.w.setProperty('chatOpen',True)
    before=deepcopy(editor._layers)
    regions=[{'name':'左区域','reason':'轻提亮','box':[100,150,320,650],'point':[210,400],'recipe':Recipe(exposure=.2).to_dict()},
             {'name':'右区域','reason':'暖一点','box':[650,200,850,700],'point':[750,450],'recipe':Recipe(warmth=12).to_dict()}]
    with mock_api(lambda _:auto_response(regions=regions)) as (url,requests):
        configure(editor.ai,url)
        ui.find('descriptionInput').setProperty('text','分别调整左右区域')
        ui.click('applyDescriptionButton');done(editor)
        message=editor.conversation[-1];ids=[layer['id'] for layer in editor._layers[-2:]]
        assert message['adjustment_layer_ids']==ids
        name='reviewMessageAdjustments_'+message['id']
        wait_for(lambda:ui.find(name).isVisible())
        original=snapshot(editor)
        for lid in ids+ids[:1]:
            ui.click(name);done(editor)
            assert editor.activeLayerId==editor.selection.pickedLayerId==lid and editor.viewport.zoom==1
            assert snapshot(editor)==original and len(requests)==1
        project=tmp_path/'review.iphoto';editor.saveProject(str(project))
        assert read_project(project)['conversation'][-1]['adjustment_layer_ids']==ids
        editor.undo();done(editor)
        assert editor._layers==before and not editor.conversationAdjustmentLayers(message['id'])
        assert not ui.find(name).isVisible()
        editor.redo();done(editor)
        assert editor.conversationAdjustmentLayers(message['id'])==ids
        editor.openProject(str(project));wait_for(lambda:not editor._pending_project and settled(editor))
        assert editor.conversationAdjustmentLayers(message['id'])==ids


def test_reopened_result_reveals_its_strength_once_and_keeps_manual_scroll(canvas,tmp_path):  # noqa: F811
    ui,editor=canvas,canvas.e
    local(editor)
    project=tmp_path/'reopened.iphoto';editor.saveProject(str(project));editor.openProject(str(project))
    wait_for(lambda:not editor._pending_project and settled(editor))
    ui.w.setProperty('chatOpen',True);QTest.qWait(80)
    ui.click('layerSelect_'+editor.activeLayerId);done(editor)
    assert ui.find('reviewLayerAdjustmentButton').isVisible() and ui.find('reviewLayerAdjustmentButton').property('enabled')
    spy=QSignalSpy(editor.selection.parameterFocusRequested);before=snapshot(editor)
    ui.click('reviewLayerAdjustmentButton');done(editor)
    scroll=ui.find('propertyScroll');row=ui.find('parameter_warmth')
    wait_for(lambda:spy.count()==1 and contained(row,scroll))
    assert spy.at(0)[1]=='warmth' and row.isVisible() and snapshot(editor)==before
    flick=scroll.property('contentItem');flick.setProperty('contentY',0);QTest.qWait(80)
    position=flick.property('contentY')
    ui.click('reviewLayerAdjustmentButton');done(editor);QTest.qWait(100)
    assert spy.count()==1 and flick.property('contentY')==position and snapshot(editor)==before


def test_parent_clip_hidden_state_and_changed_mask_control_current_navigation(canvas):  # noqa: F811
    editor=canvas.e;layer=local(editor)
    editor.groupLayer();done(editor);group=editor._layer()
    group.update(visible=False,opacity=.4,collapsed=True,
                 mask={**empty_mask(),'ops':[{'kind':'rect','mode':'add','points':[[.65,.2],[.9,.8]]}]})
    editor._commit();editor._change();done(editor)
    history,cursor=deepcopy(editor._history),editor._cursor
    assert editor.selection.reviewAdjustments([layer['id']]);done(editor)
    assert center(editor.viewport)[0]>.65 and '未显示' in editor.status
    assert not group['visible'] and group['opacity']==.4 and not group['collapsed']
    assert editor._history==history and editor._cursor==cursor
    current=next(l for l in editor._layers if l['id']==layer['id'])
    current['mask']={**empty_mask(),'ops':[{'kind':'rect','mode':'add','points':[[.71,.3],[.77,.4]]}]}
    editor._change();done(editor)
    assert editor.selection.reviewAdjustments([layer['id']])
    x,y=center(editor.viewport)
    assert .71<x<.77 and .3<y<.4 and '1/1' in editor.status
    assert '下一处' not in editor.status


@pytest.mark.parametrize('blocked',['selection','regions','busy','missing','group','global','zero','empty'])
def test_unavailable_review_preserves_tool_view_and_document(canvas,blocked):  # noqa: F811
    editor=canvas.e;layer=local(editor);ids=[layer['id']]
    if blocked=='selection':editor.beginSelection('empty')
    elif blocked=='regions':editor._region_candidate={}
    elif blocked=='missing':ids=['missing']
    elif blocked=='group':
        editor.groupLayer();done(editor);ids=[editor.activeLayerId]
    elif blocked=='global':layer['mask']={**empty_mask(),'base':'full'}
    elif blocked=='zero':layer['recipe']=Recipe().to_dict()
    elif blocked=='empty':layer['mask']=empty_mask()
    if blocked=='busy':editor._active={'op':'selection'}
    before=(snapshot(editor),center(editor.viewport),editor.viewport.zoom,editor.selection.tool,editor.selection.showMask)
    try:
        assert not editor.selection.reviewAdjustments(ids)
        assert (snapshot(editor),center(editor.viewport),editor.viewport.zoom,editor.selection.tool,editor.selection.showMask)==before
    finally:
        editor._region_candidate=None
        if blocked=='busy':editor._active=None


def test_old_message_reference_deleted_layer_and_availability_never_decode_masks(canvas,monkeypatch):  # noqa: F811
    editor=canvas.e;layer=local(editor)
    message=editor._message('assistant','已调整',state='applied')
    answered=editor._message('assistant','建议',state='answered')
    monkeypatch.setattr(adjustment_review,'raster_mask_cached',lambda *args:pytest.fail('Availability must not decode large masks'))
    assert editor.canReviewAdjustment and editor.conversationAdjustmentLayers(message['id'])==[layer['id']]
    assert not editor.conversationAdjustmentLayers(answered['id'])
    other=local(editor);other['name']=layer['name']
    editor.runLayerAction(layer['id'],'delete');done(editor)
    assert not editor.conversationAdjustmentLayers(message['id'])


@pytest.mark.parametrize('bad',[[],['a']*2,['a']*5,[True],[''],['x'*65],None])
def test_saved_adjustment_references_are_bounded_and_unambiguous(bad):
    with pytest.raises(ValueError,match='图层引用'):
        validate_conversation([{'id':'message','role':'assistant','adjustment_layer_ids':bad}])
