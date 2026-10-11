"""Generated pixels remain inspectable even when every tone parameter is zero."""
from copy import deepcopy
from io import BytesIO
import base64
from PIL import Image, ImageDraw
from PySide6.QtTest import QTest
import pytest

from iphoto.controllers import adjustment_review
from iphoto.controllers.layers import addLocalLayers
from iphoto.document import new_layer
from iphoto.pixel_patch import encode_patch
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def content(editor, rgba):
    pixels = Image.new('RGBA' if rgba else 'RGB',(480,320),(80,40,20,0) if rgba else (80,40,20))
    if rgba:
        draw = ImageDraw.Draw(pixels)
        draw.rectangle((155,30,400,300),fill=(90,50,25,128))
        draw.rectangle((160,30,395,300),fill=(90,50,25,255))
    layer = new_layer('AI像素内容', True)
    if not rgba:
        layer['mask']['base'] = 'empty'
        layer['mask']['ops'] = [{'kind':'rect','mode':'add','points':[[.2,.2],[.5,.8]]}]
    layer['pixel_patch'] = encode_patch(pixels,[2400,1600],[0,0,2400,1600],preserve_alpha=rgba)
    result = addLocalLayers(editor,[layer],allow_pixel_patch=True)[0]
    wait_for(lambda:settled(editor))
    return result


@pytest.mark.parametrize('rgba',[False,True])
def test_zero_recipe_content_can_be_reviewed_at_its_own_resolution(canvas,rgba):  # noqa: F811
    ui,editor = canvas,canvas.e
    layer = content(editor,rgba)
    before = deepcopy((editor._layers,editor._history,editor._cursor))
    button = ui.find('reviewLayerAdjustmentButton')
    assert editor.canReviewAdjustment and button.isVisible()
    ui.click('reviewLayerAdjustmentButton')
    wait_for(lambda:settled(editor))
    assert editor.viewport.zoom == pytest.approx(.2)
    assert '480×320' in editor.status and '像素' in editor.status
    assert not editor.selection.showMask and editor.selection.pickedLayerId == layer['id']
    assert deepcopy((editor._layers,editor._history,editor._cursor)) == before


def decoded(url):
    with Image.open(BytesIO(base64.b64decode(url.partition(',')[2]))) as image:
        return image.copy()


def test_content_thumbnail_shows_pixels_refreshes_and_opens_effect_without_mask_draft(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    layer=content(editor,True)
    name='layerContentThumb_'+layer['id']
    first=ui.find(name).property('source').toString()
    image=decoded(first)
    assert image.mode=='RGBA' and image.size==(48,32)
    assert image.getpixel((0,0))[3]==0 and image.getpixel((20,10))==(90,50,25,255)
    before=deepcopy((editor._layers,editor._history,editor._cursor))
    ui.click(name)
    assert editor.viewport.zoom==pytest.approx(.2) and '查看局部效果' in editor.status
    assert not editor.hasSelectionDraft and not editor.selection.showMask
    assert deepcopy((editor._layers,editor._history,editor._cursor))==before
    replacement=Image.new('RGBA',(480,320),(20,150,220,128))
    layer['pixel_patch']=encode_patch(replacement,[2400,1600],[0,0,2400,1600],preserve_alpha=True)
    editor._commit();editor._change();wait_for(lambda:settled(editor))
    wait_for(lambda:ui.find(name).property('source').toString()!=first)
    actual=decoded(ui.find(name).property('source').toString()).getpixel((20,10))
    assert actual[3]==128 and all(abs(a-b)<=1 for a,b in zip(actual[:3],(20,150,220)))
    editor.undo();wait_for(lambda:settled(editor))
    assert ui.find(name).property('source').toString()==first


@pytest.mark.parametrize('native',[False,True])
def test_background_menu_changes_actual_transparent_display_without_edit_or_render(canvas,monkeypatch,native):  # noqa: F811
    ui,editor=canvas,canvas.e
    assert not ui.find('transparencyBackgroundButton').isVisible()
    content(editor,True)
    ui.click('fitCanvasButton')
    wait_for(lambda:ui.w.property('previewReady') and settled(editor))
    if native:
        editor.viewport.setZoom(1.)
        editor.viewport.centerOn(.1,.1)
        wait_for(lambda:ui.w.property('detailReady') and settled(editor))
    QTest.qWait(180)
    before=deepcopy((editor._layers,editor._history,editor._cursor,editor.dirty,editor._generation,editor._serial))
    requests=[]
    request=editor._request
    monkeypatch.setattr(editor,'_request',lambda *args,**kwargs:(requests.append(args),request(*args,**kwargs))[1])
    pixels=[]
    for mode in ('white','black','checker'):
        ui.click('transparencyBackgroundButton')
        ui.click('transparency'+mode.title()+'MenuItem')
        QTest.qWait(80)
        assert ui.find('canvasViewport').property('transparencyBackground')==mode
        point=ui.point('photoCanvas',.1,.1)
        frame=ui.w.grabWindow();dpr=frame.devicePixelRatio()
        pixels.append(frame.pixelColor(round(point.x()*dpr),round(point.y()*dpr)).getRgb()[:3])
        assert deepcopy((editor._layers,editor._history,editor._cursor,editor.dirty,editor._generation,editor._serial))==before
    assert pixels[0]==(255,255,255) and pixels[1]==(0,0,0)
    assert pixels[2] in ((74,77,82),(56,60,65))
    assert not requests


def test_content_review_message_survives_save_reopen_undo_and_redo(canvas,tmp_path):  # noqa: F811
    ui,editor=canvas,canvas.e
    layer=content(editor,True)
    message=editor._message('assistant','已生成人物前景',state='applied')
    ui.w.setProperty('chatOpen',True)
    name='reviewMessageAdjustments_'+message['id']
    wait_for(lambda:ui.find(name).isVisible())
    QTest.qWait(150)  # Let opening the chat settle its layout before pointer delivery.
    assert editor.conversationAdjustmentLayers(message['id'])==[layer['id']]
    ui.click(name)
    assert editor.viewport.zoom==pytest.approx(.2)
    project=tmp_path/'generated.iphoto';editor.saveProject(str(project))
    editor.openProject(str(project));wait_for(lambda:not editor._pending_project and settled(editor))
    assert editor.conversationAdjustmentLayers(message['id'])==[layer['id']]
    ui.click(name)
    assert editor.viewport.zoom==pytest.approx(.2)
    # A reopened document starts a new undo timeline. Exercise deletion within it.
    editor.runLayerAction(layer['id'],'delete');wait_for(lambda:settled(editor))
    assert not ui.find(name).isVisible()
    editor.undo();wait_for(lambda:settled(editor))
    assert ui.find(name).isVisible()
    editor.redo();wait_for(lambda:settled(editor))
    assert not ui.find(name).isVisible()


def test_rgba_navigation_follows_live_alpha_parent_clip_and_content_version(canvas):  # noqa: F811
    editor=canvas.e
    layer=content(editor,True)
    first=adjustment_review.targets(editor,[layer['id']])
    assert len(first)==4 and first[0][1][0]<first[1][1][0]
    editor.groupLayer();wait_for(lambda:settled(editor))
    parent=editor._layer()
    parent.update(visible=False,mask={'base':'empty','ops':[{'kind':'rect','mode':'add','points':[[.6,.2],[.8,.8]]}],
                                    'inverted':False,'feather':0,'label':'父组范围'})
    editor._commit();editor._change();wait_for(lambda:settled(editor))
    clipped=adjustment_review.targets(editor,[layer['id']])
    assert len(clipped)==4 and all(.59<point[0]<.81 and .19<point[1]<.81 for _,point,_ in clipped)
    assert editor.selection.reviewAdjustments([layer['id']]) and '未显示' in editor.status
    layer=next(item for item in editor._layers if item['id']==layer['id'])
    pixels=Image.new('RGBA',(480,320),(0,0,0,0))
    # All-transparent replacement still needs an inspection path.
    layer['pixel_patch']=encode_patch(pixels,[2400,1600],[0,0,2400,1600],preserve_alpha=True)
    editor._change();wait_for(lambda:settled(editor))
    transparent=adjustment_review.targets(editor,[layer['id']])
    assert len(transparent)==1 and transparent[0][2]!=clipped[0][2]
    assert editor.selection.reviewAdjustments([layer['id']]) and '1/1' in editor.status


def test_almost_invisible_speckles_do_not_send_review_into_empty_canvas(canvas):  # noqa: F811
    editor=canvas.e;layer=content(editor,True)
    pixels=Image.new('RGBA',(480,320),(60,30,20,1))
    draw=ImageDraw.Draw(pixels)
    draw.rectangle((155,100,400,300),fill=(90,50,25,255))
    draw.rectangle((0,0,2,2),fill=(90,50,25,8))
    layer['pixel_patch']=encode_patch(pixels,[2400,1600],[0,0,2400,1600],preserve_alpha=True)
    before=deepcopy(layer['pixel_patch'])
    targets=adjustment_review.targets(editor,[layer['id']])
    assert len(targets)==4
    assert all(.31<point[0]<.85 and .45<point[1]<.7 for _,point,_ in targets)
    assert layer['pixel_patch']==before


def test_inspection_zoom_respects_mapped_patch_and_final_canvas(canvas):  # noqa: F811
    editor=canvas.e
    layer=content(editor,False)
    patch=layer['pixel_patch']
    patch['canvas_size']=[1200,800];patch['box']=[120,80,600,400]
    assert adjustment_review.content_scale(editor,layer)==(.5,(480,320))
    patch['box']=[120,80,300,200]
    assert adjustment_review.content_scale(editor,layer)==(1.,(480,320))
    targets=adjustment_review.targets(editor,[layer['id']])
    assert len(targets)==1 and .2<targets[0][1][0]<.25 and .2<targets[0][1][1]<.25
