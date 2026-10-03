"""A stable, explicitly viewed stroke can be removed without unrelated edits."""
from copy import deepcopy
from threading import Event

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtTest import QTest

from iphoto.controllers import heal, session
from iphoto.document import empty_mask, read_project, render_layers
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_ai_repair import auto, PARTS, sent, spots
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_independent_repair import paint, publish
from test_parameter_entry import snapshot
from test_preset_focus_ui import contained
from test_selection_controller import editor  # noqa: F401
from iphoto.engine import Recipe


def add(e,count=3):
    for index in range(count):
        assert paint(e,[[.2+index*.2,.25+index*.15]])
        wait_for(lambda:settled(e))
    return e.activeLayerId


def review(e,index=0,ids=None):
    for _ in range(index+1):
        assert e.selection.reviewRepairs(ids or [e.activeLayerId])
    wait_for(lambda:settled(e))
    return e.selection.reviewedRepair


@pytest.mark.parametrize('index',[0,1,2])
def test_remove_exact_reviewed_stroke_preserves_strength_mask_and_one_undo(editor,index):  # noqa: F811
    e=editor;lid=add(e);e.setOpacity(50);e.finishGesture();wait_for(lambda:settled(e))
    info=review(e,index);before=snapshot(e);ops=deepcopy(e._layer()['heal']['ops'])
    assert info['index']==index+1 and info['count']==3 and info['layer_id']==lid
    assert e.selection.deleteReviewedRepair(info['token']);wait_for(lambda:settled(e))
    expected=ops[:index]+ops[index+1:]
    assert e._layer()['heal']['ops']==expected and e._layer()['opacity']==.5
    assert e._layer()['mask']['ops']==[{**op,'kind':'brush','mode':'add'} for op in expected]
    assert e._layers[:-1]==before[0][:-1] and e.activeLayerId==lid
    assert e._cursor==before[2]+1 and e._generation==before[3]+1
    assert not e.selection.reviewedRepair
    now=snapshot(e);assert not e.selection.deleteReviewedRepair(info['token']) and snapshot(e)==now
    e.undo();wait_for(lambda:settled(e));assert e._layers==before[0] and e._cursor==before[2]
    e.redo();wait_for(lambda:settled(e));assert e._layer()['heal']['ops']==expected


@pytest.mark.parametrize('kind',['owned','sole','legacy','mixed','fill','custom','grouped'])
def test_last_stroke_preserves_nonrepair_content_and_required_layer(editor,kind):  # noqa: F811
    e=editor;lid=add(e,1)
    if kind=='sole':e._layers=[e._layer()];publish(e)
    elif kind=='legacy':e._layer()['mask']=empty_mask(True);publish(e)
    elif kind=='mixed':e.setParameter('exposure',.2);e.finishGesture()
    elif kind=='fill':e._layer()['inpaint']={'method':'telea','radius':5};publish(e)
    elif kind=='custom':
        e._layer()['mask']={**empty_mask(),'ops':[{'kind':'rect','mode':'add','points':[[.1,.1],[.9,.9]]}]};publish(e)
    elif kind=='grouped':e.groupLayer();wait_for(lambda:settled(e));e.selection.pickLayer(lid)
    info=review(e);before=snapshot(e);target=deepcopy(e._layer())
    removes=kind in ('owned','grouped')
    assert info['removes_layer']==removes
    assert e.selection.deleteReviewedRepair(info['token']);wait_for(lambda:settled(e))
    if removes:
        assert e._layers==[layer for layer in before[0] if layer['id']!=lid]
        assert any(layer['kind']=='adjustment' for layer in e._layers)
    else:
        expected=deepcopy(target);expected.pop('heal')
        if kind=='sole':expected['mask']={**empty_mask(),'label':'修复笔画'}
        assert e._layer()==expected and e.activeLayerId==lid
        assert e._layers[:-1]==before[0][:-1]
    assert e._cursor==before[2]+1 and e._generation==before[3]+1
    e.undo();wait_for(lambda:settled(e));assert e._layers==before[0] and e.activeLayerId==lid


@pytest.mark.parametrize('stale',['missing_token','new_review','new_stroke','other_layer','same_name',
                                'clear_pick','draft','regions','busy','photo','reopen'])
def test_stale_or_blocked_target_does_not_delete_a_different_stroke(editor,stale,tmp_path):  # noqa: F811
    e=editor;lid=add(e,2);info=review(e);token=info['token']
    if stale=='missing_token':token=''
    elif stale=='new_review':review(e)
    elif stale=='new_stroke':assert paint(e,[[.8,.8]]);wait_for(lambda:settled(e))
    elif stale=='other_layer':e.addGlobalLayer();wait_for(lambda:settled(e))
    elif stale=='same_name':
        name=e.activeLayerName;e.deleteLayer();e.addGlobalLayer();e.renameLayer(name)
        wait_for(lambda:settled(e));add(e,2);assert e.activeLayerId!=lid
    elif stale=='clear_pick':e.selection.clearPick()
    elif stale=='draft':e.beginSelection('current');wait_for(lambda:settled(e))
    elif stale=='regions':e._region_candidate={};e.changed.emit()
    elif stale=='busy':e._active={'op':'selection'}
    elif stale=='photo':
        source=tmp_path/'another.png';Image.new('RGB',(240,160),'orange').save(source)
        e.openImage(str(source));wait_for(lambda:e.imageName==source.name and settled(e))
    elif stale=='reopen':
        target=tmp_path/'same.iphoto';e.saveProject(str(target));e.openProject(str(target))
        wait_for(lambda:not e._pending_project and settled(e))
    before=snapshot(e);messages=deepcopy(e.conversation)
    try:
        assert not e.selection.deleteReviewedRepair(token)
        assert snapshot(e)==before and e.conversation==messages
    finally:
        if stale=='busy':e._active=None
        if stale=='regions':e._region_candidate=None


def test_remove_does_not_require_opencv_and_preserves_pending_parameter_step(editor,monkeypatch):  # noqa: F811
    e=editor;add(e,2);info=review(e);original=deepcopy(e._layers);cursor=e._cursor
    e.setOpacity(50);half=deepcopy(e._layers)
    monkeypatch.setattr(heal,'available',lambda:False)
    assert e.selection.deleteReviewedRepair(info['token']);wait_for(lambda:settled(e))
    assert e._cursor==cursor+2 and e._layer()['opacity']==.5
    e.undo();wait_for(lambda:settled(e));assert e._layers==half
    e.undo();wait_for(lambda:settled(e));assert e._layers==original


def test_remove_during_ai_wait_cannot_consume_pending_request(editor):  # noqa: F811
    e=editor;lid=add(e,2);info=review(e);ops=deepcopy(e._layer()['heal']['ops'])
    with mock_api(auto_response('global',[],Recipe(exposure=.25).to_dict()),delay=.3) as (url,requests):
        configure(e.ai,url);assert e.sendMessage('整张照片提亮，保留修复','auto')
        wait_for(lambda:e.ai.busy)
        before=snapshot(e);pending=e._pending_request;messages=deepcopy(e.conversation)
        assert not e.selection.deleteReviewedRepair(info['token'])
        assert snapshot(e)==before and e._pending_request==pending and e.conversation==messages and e.ai.busy
        wait_for(lambda:not e.ai.busy and settled(e))
        assert len(requests)==1 and e.conversation[-1]['state']=='applied'
        assert next(layer for layer in e._layers if layer['id']==lid)['heal']['ops']==ops


def test_async_save_keeps_old_strokes_and_new_deletion_remains_dirty(editor,tmp_path,monkeypatch):  # noqa: F811
    e=editor;add(e,2);info=review(e);before=deepcopy(e._layers)
    writer=session.write_project;started=Event();release=Event();target=tmp_path/'old-strokes.iphoto'
    def delayed(path,payload,overwrite=False):
        started.set();assert release.wait(3)
        return writer(path,payload,overwrite=overwrite)
    monkeypatch.setattr(session,'write_project',delayed)
    try:
        assert e.saveProjectAsync(str(target));wait_for(lambda:started.is_set())
        assert e.savingProject and e.selection.deleteReviewedRepair(info['token'])
        wait_for(lambda:settled(e))
    finally:release.set()
    wait_for(lambda:not e.savingProject)
    assert read_project(target)['layers']==before and e.dirty and e.activeRepairInfo['count']==1


def test_render_notifications_reuse_target_check_and_content_changes_recheck(editor):  # noqa: F811
    e=editor;add(e,2)
    class Counted(list):
        calls=0
        def __eq__(self,other):
            type(self).calls+=1
            return super().__eq__(other)
    ops=Counted(e._layer()['heal']['ops']);e._layer()['heal']['ops']=ops
    review(e);baseline=Counted.calls
    for _ in range(15):assert e.selection.reviewedRepair['index']==1
    assert Counted.calls==baseline
    e.changed.emit()
    for _ in range(15):assert e.selection.reviewedRepair['index']==1
    assert Counted.calls==baseline
    e.setOpacity(50);baseline=Counted.calls
    for _ in range(15):assert e.selection.reviewedRepair['index']==1
    assert Counted.calls==baseline+1


def actual_strokes(ui):
    ui.e.selection.pickLayer(ui.e.activeLayerId)
    ui.click('tool_heal');ui.click('photoCanvas',.4,.4);wait_for(lambda:settled(ui.e))
    ui.click('photoCanvas',.6,.6);wait_for(lambda:settled(ui.e))
    return deepcopy(ui.e._layers)


def test_actual_footer_delete_second_stroke_native_save_and_keyboard_undo(canvas,tmp_path):  # noqa: F811
    ui=canvas;original=actual_strokes(ui);lid=ui.e.activeLayerId
    button=ui.find('deleteReviewedRepairButton');assert button.isVisible() and not button.property('enabled')
    assert not ui.find('layerOpacitySlider').isVisible() and ui.find('repairStrengthSlider').isVisible()
    ui.click('reviewLayerRepairsFooterButton');ui.click('reviewLayerRepairsFooterButton')
    wait_for(lambda:ui.w.property('detailReady') and settled(ui.e))
    before=snapshot(ui.e)
    assert '当前第2笔' in ui.find('repairStrengthCaption').property('text')
    assert button.property('text')=='删除第2笔'
    ui.click('deleteReviewedRepairButton');wait_for(lambda:ui.w.property('detailReady') and settled(ui.e))
    assert ui.e._layers[:-1]==original[:-1] and ui.e._layer()['heal']['ops']==original[-1]['heal']['ops'][:1]
    assert ui.e._cursor==before[2]+1 and ui.e._generation==before[3]+1 and ui.e.activeLayerId==lid
    with Image.open(ui.e._path) as source, Image.open(QUrl(ui.e.detailUrl).toLocalFile()) as tile:
        assert tile.tobytes()==render_layers(source,ui.e._layers).crop(ui.e._detail_box).tobytes()
    target=tmp_path/'remaining.iphoto';ui.e.saveProjectAsync(str(target))
    wait_for(lambda:not ui.e.savingProject and ui.e.projectPath==str(target))
    assert read_project(target)['layers']==ui.e._layers
    ui.key(Qt.Key_Z,Qt.ControlModifier);wait_for(lambda:settled(ui.e));assert ui.e._layers==original
    ui.key(Qt.Key_Z,Qt.ControlModifier|Qt.ShiftModifier);wait_for(lambda:settled(ui.e))
    assert ui.e.activeRepairInfo['count']==1


def test_actual_pressed_button_cannot_delete_newly_reviewed_stroke(canvas):  # noqa: F811
    ui=canvas;actual_strokes(ui);ui.click('reviewLayerRepairsFooterButton')
    button=ui.find('deleteReviewedRepairButton');point=ui.point(button.objectName())
    QTest.mousePress(ui.w,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(20)
    old=button.property('repairToken');assert old==ui.e.selection.reviewedRepair['token']
    assert ui.e.selection.reviewRepairs([ui.e.activeLayerId]);QTest.qWait(30)
    assert ui.e.selection.reviewedRepair['token']!=old and ui.e.selection.reviewedRepair['index']==2
    before=snapshot(ui.e)
    QTest.mouseRelease(ui.w,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(80)
    assert snapshot(ui.e)==before
    ui.click('deleteReviewedRepairButton');wait_for(lambda:settled(ui.e))
    assert ui.e.activeRepairInfo['count']==1


def test_actual_delete_checks_invalid_number_and_commits_valid_value_before_deletion(canvas):  # noqa: F811
    ui=canvas;original=actual_strokes(ui);ui.click('reviewLayerRepairsFooterButton')
    ui.e.selection.parameterFocusRequested.emit(ui.e.activeLayerId,'exposure')
    wait_for(lambda:contained(ui.find('parameterValue_exposure'),ui.find('propertyScroll')))
    ui.click('parameterValue_exposure');ui.key(Qt.Key_A,Qt.ControlModifier);ui.type('3')
    before=snapshot(ui.e);ui.click('deleteReviewedRepairButton')
    field=ui.find('parameterValue_exposure')
    assert snapshot(ui.e)==before and field.property('text')=='3' and field.hasActiveFocus()
    assert field.property('errorMessage')
    ui.key(Qt.Key_A,Qt.ControlModifier);ui.type('.2')
    ui.click('deleteReviewedRepairButton');wait_for(lambda:settled(ui.e))
    assert ui.e._layer()['recipe']['exposure']==.2 and ui.e.activeRepairInfo['count']==1
    assert ui.e._layer()['mask']==original[-1]['mask']
    assert ui.e._cursor==before[2]+2
    assert ui.find('layerOpacitySlider').isVisible() and not ui.find('repairStrengthSlider').isVisible()
    assert ui.find('layerOpacityLabel').property('text')=='图层强度'
    for name in ('deleteReviewedRepairButton','reviewLayerRepairsFooterButton','addLayerMaskButton','eraseLayerMaskButton'):
        item=ui.find(name);top=item.mapToScene(QPointF()).y()
        assert item.isVisible() and top>=0 and top+item.height()<=ui.w.height()
    ui.click('canvasSurface')
    ui.key(Qt.Key_Z,Qt.ControlModifier);wait_for(lambda:settled(ui.e))
    assert ui.e.activeRepairInfo['count']==2 and ui.e.parameters['exposure']==.2
    ui.key(Qt.Key_Z,Qt.ControlModifier);wait_for(lambda:settled(ui.e));assert ui.e._layers==original


def test_actual_pressed_delete_stays_under_pointer_when_number_commits(canvas):  # noqa: F811
    ui=canvas;original=actual_strokes(ui);ui.click('reviewLayerRepairsFooterButton')
    ui.e.selection.parameterFocusRequested.emit(ui.e.activeLayerId,'exposure')
    wait_for(lambda:contained(ui.find('parameterValue_exposure'),ui.find('propertyScroll')))
    ui.click('parameterValue_exposure');ui.key(Qt.Key_A,Qt.ControlModifier);ui.type('.2')
    field=ui.find('parameterValue_exposure');button=ui.find('deleteReviewedRepairButton')
    point=ui.point('deleteReviewedRepairButton')
    QTest.mousePress(ui.w,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(30)
    try:
        if field.hasActiveFocus():ui.key(Qt.Key_Return)
        assert ui.e.parameters['exposure']==.2 and ui.e.activeRepairInfo['count']==2
        assert button.contains(button.mapFromScene(QPointF(point))), (point,ui.point(button.objectName()))
    finally:
        QTest.mouseRelease(ui.w,Qt.LeftButton,Qt.NoModifier,point)
    wait_for(lambda:settled(ui.e))
    assert ui.e.activeRepairInfo['count']==1 and ui.e._layer()['mask']==original[-1]['mask']


def test_actual_ai_reply_continues_review_after_deleting_one_result(canvas):  # noqa: F811
    ui=canvas;ui.w.setProperty('chatOpen',True);initial=deepcopy(ui.e._layers)
    with mock_api(lambda payload:auto(ui.e.parameters,PARTS) if sent(payload)['mode']=='auto' else spots()) as (url,requests):
        configure(ui.e.ai,url);ui.find('descriptionInput').setProperty('text','分别修复面部和衣服的小污点')
        ui.click('applyDescriptionButton');wait_for(lambda:not ui.e.ai.busy and ui.e.conversation[-1]['state']=='applied' and settled(ui.e))
        message=ui.e.conversation[-1];ids=message['repair_layer_ids'];button='reviewMessageRepairs_'+message['id']
        wait_for(lambda:ui.find(button).isVisible());ui.click(button)
        assert ui.e.selection.reviewedRepair['layer_id']==ids[0]
        before=snapshot(ui.e);count=ui.e.conversationCount
        ui.click('deleteReviewedRepairButton');wait_for(lambda:settled(ui.e))
        assert ui.e._layers[:len(initial)]==initial and ui.e.conversationRepairLayers(message['id'])==ids[1:]
        assert ui.e._cursor==before[2]+1 and ui.e.conversationCount==count and len(requests)==3
        ui.click(button);assert ui.e.selection.reviewedRepair['layer_id']==ids[1]
        assert ui.e.selection.reviewedRepair['index']==1 and ui.e.selection.reviewedRepair['count']==1
        ui.key(Qt.Key_Z,Qt.ControlModifier);wait_for(lambda:settled(ui.e))
        assert ui.e._layers==before[0] and ui.e.conversationRepairLayers(message['id'])==ids
