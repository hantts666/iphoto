"""Real Qt repair-result navigation, current strokes and document protection."""
from copy import deepcopy

from PIL import Image
from PySide6.QtCore import QUrl
import pytest

from iphoto.document import read_project, render_layers, validate_conversation
from iphoto.viewport import Viewport
from test_ai import configure, mock_api, wait_for
from test_ai_repair import auto, PARTS, sent, spots
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def done(editor):
    wait_for(lambda: not editor.ai.busy and not editor._pending_request and settled(editor))


def document(editor):
    return deepcopy((editor._layers, editor._history, editor._cursor))


def center(viewport):
    return ((viewport._size[0]/2-viewport.imageX)/viewport.imageWidth,
            (viewport._size[1]/2-viewport.imageY)/viewport.imageHeight)


def add_strokes(editor):
    editor.drawHeal([[.2,.25]], .003)
    done(editor)
    editor.drawHeal([[.8,.7]], .003)
    done(editor)


def test_ai_message_reviews_all_live_parts_at_native_size_and_survives_save_undo(canvas,tmp_path):  # noqa: F811
    ui,editor=canvas,canvas.e
    ui.w.setProperty('chatOpen',True)
    before=deepcopy(editor._layers)
    current=editor.parameters
    with mock_api(lambda p:auto(current,PARTS) if sent(p)['mode']=='auto' else spots()) as (url,requests):
        configure(editor.ai,url)
        ui.find('descriptionInput').setProperty('text','分别修复面部和衣服小污点')
        ui.click('applyDescriptionButton')
        done(editor)
        message=editor.conversation[-1]
        ids=[layer['id'] for layer in editor._layers[-2:]]
        assert message['repair_layer_ids']==ids and len(requests)==3
        name='reviewMessageRepairs_'+message['id']
        wait_for(lambda:ui.find(name).isVisible())
        snapshot=document(editor)
        for lid in ids+ids[:1]:
            ui.click(name)
            done(editor)
            wait_for(lambda:ui.w.property('detailReady'))
            layer=next(layer for layer in editor._layers if layer['id']==lid)
            assert editor.activeLayerId==editor.selection.pickedLayerId==lid
            assert center(editor.viewport)==pytest.approx(layer['heal']['ops'][0]['points'][0])
            assert editor.viewport.zoom==1 and not editor.viewport.fitMode
            assert not editor.selection.showMask and editor.selection.tool=='inspect'
            assert document(editor)==snapshot and len(requests)==3
            with Image.open(QUrl(editor.detailUrl).toLocalFile()) as tile:
                source=Image.open(ui.e._path)
                assert tile.tobytes()==render_layers(source,editor._layers).crop(editor._detail_box).tobytes()
        caption=ui.find('repairStrengthCaption')
        assert caption.isVisible() and '100%' in caption.property('text')
        editor.undo()
        done(editor)
        assert editor._layers==before and editor.conversationRepairLayers(message['id'])==[]
        assert not ui.find(name).isVisible()
        editor.redo()
        done(editor)
        assert editor.conversationRepairLayers(message['id'])==ids
        project=tmp_path/'repair-review.iphoto'
        editor.saveProject(str(project))
        assert read_project(project)['conversation'][-1]['repair_layer_ids']==ids
        editor.openProject(str(project))
        wait_for(lambda:not editor._pending_project and settled(editor))
        assert editor.conversationRepairLayers(message['id'])==ids


def test_layer_review_cycles_manual_strokes_hides_overlay_and_keeps_one_undo_per_stroke(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    initial=deepcopy(editor._layers)
    add_strokes(editor)
    editor.selection.pickLayer(editor.activeLayerId)
    editor.selection.chooseTool('heal')
    editor.selection.toggleShowMask()
    ui.w.setProperty('compare',True)
    before=document(editor)
    for point in ([.2,.25],[.8,.7],[.2,.25]):
        ui.click('reviewLayerRepairsButton')
        done(editor)
        assert center(editor.viewport)==pytest.approx(point)
        assert not ui.w.property('compare') and not editor.selection.showMask
        assert editor.selection.tool=='inspect' and document(editor)==before
    editor.setOpacity(50)
    editor.finishGesture()
    done(editor)
    assert '50%' in ui.find('repairStrengthCaption').property('text')
    editor.undo()
    done(editor)
    assert '100%' in ui.find('repairStrengthCaption').property('text')
    editor.undo()
    done(editor)
    assert editor.activeRepairInfo['count']==1
    assert editor.selection.reviewRepairs([editor.activeLayerId])
    assert center(editor.viewport)==pytest.approx([.2,.25])
    editor.undo()
    done(editor)
    assert editor._layers==initial and editor.activeRepairInfo=={'count':0}
    assert not ui.find('reviewLayerRepairsButton').isVisible()


def test_review_preserves_hidden_group_and_reports_effective_strength(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    add_strokes(editor)
    lid=editor.activeLayerId
    editor.groupLayer()
    done(editor)
    group=editor._layer()
    group['opacity']=.4
    group['visible']=False
    group['collapsed']=True
    editor._commit()
    editor._change()
    done(editor)
    history,cursor=deepcopy(editor._history),editor._cursor
    assert editor.selection.reviewRepairs([lid])
    done(editor)
    assert not group['visible'] and group['opacity']==.4 and not group['collapsed']
    assert editor._history==history and editor._cursor==cursor
    assert editor.activeRepairInfo['effective_strength']==40
    assert not editor.activeRepairInfo['displayed'] and '未显示' in editor.status
    assert '效果未显示' in ui.find('repairStrengthCaption').property('text')


@pytest.mark.parametrize('blocked',['selection','regions','busy','missing','group'])
def test_blocked_review_leaves_document_tool_and_viewport_unchanged(canvas,blocked):  # noqa: F811
    editor=canvas.e
    add_strokes(editor)
    ids=[editor.activeLayerId]
    if blocked=='selection':editor.beginSelection('empty')
    elif blocked=='regions':editor._region_candidate={}
    elif blocked=='missing':ids=['missing']
    elif blocked=='group':
        editor.groupLayer()
        done(editor)
        ids=[editor.activeLayerId]
    if blocked=='busy':editor._active={'op':'selection'}
    before=(document(editor),center(editor.viewport),editor.viewport.zoom,editor.selection.tool,editor.selection.showMask)
    try:
        assert not editor.selection.reviewRepairs(ids)
        assert (document(editor),center(editor.viewport),editor.viewport.zoom,editor.selection.tool,editor.selection.showMask)==before
    finally:
        editor._region_candidate=None
        if blocked=='busy':editor._active=None


def test_old_result_is_resolved_by_id_and_deleted_results_never_target_same_named_layer(canvas):  # noqa: F811
    editor=canvas.e
    add_strokes(editor)
    lid=editor.activeLayerId
    message=editor._message('assistant','已在2处执行局部修复，建立独立图层：修复。',state='applied')
    unrelated=editor._message('assistant','已调整曝光',state='applied')
    assert editor.conversationRepairLayers(message['id'])==[lid]
    assert not editor.conversationRepairLayers(unrelated['id'])
    editor.addGlobalLayer()
    done(editor)
    editor.renameLayer(editor._layers[0]['name'])
    editor.drawHeal([[.5,.5]], .003)
    done(editor)
    editor.runLayerAction(lid,'delete')
    done(editor)
    assert not editor.conversationRepairLayers(message['id'])


@pytest.mark.parametrize('bad',[[],['a']*2,['a']*4,[True],[''],['x'*65]])
def test_saved_repair_references_are_bounded_and_unambiguous(bad):
    with pytest.raises(ValueError,match='图层引用'):
        validate_conversation([{'id':'test','role':'assistant','repair_layer_ids':bad}])


@pytest.mark.parametrize('dpr',[1,2])
def test_review_bounds_fit_long_strokes_and_do_not_magnify_above_source_pixels(qt_app,dpr):
    viewport=Viewport()
    viewport.setSource(4016,6016)
    viewport.resize(600,260,dpr)
    viewport.focusRegion(.1,.2,.9,.8)
    left,top,width,height=viewport.visibleRect
    assert left<=.1 and top<=.2 and left+width>=.9 and top+height>=.8
    assert 0<viewport.zoom<=1
    viewport.focusRegion(.49,.49,.51,.51)
    assert viewport.zoom==1 and center(viewport)==pytest.approx([.5,.5])
    before=(viewport.zoom,center(viewport))
    viewport.focusRegion(0,0,float('nan'),1)
    assert (viewport.zoom,center(viewport))==before
