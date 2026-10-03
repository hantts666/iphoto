"""User brush strokes own repair strength, scope, history and current binding."""
from copy import deepcopy
import json
from threading import Event

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.controllers import heal, session
from iphoto.document import MAX_LAYERS, new_layer, read_project
from test_ai import configure, mock_api, wait_for
from test_ai_layer_edits import setup_layers, response, edit_for
from test_ai_current_selection import selection_response
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_parameter_entry import snapshot
from test_preset_focus_ui import contained
from test_selection_controller import editor  # noqa: F401


def paint(e, points=None, radius=.01, **binding):
    return e.selection.paintRepair(binding.get('layer',e.activeLayerId),
                                   binding.get('photo',e.originalUrl),
                                   binding.get('generation',e.documentGeneration),
                                   [[.5,.5]] if points is None else points, radius)


def publish(e):
    e._commit();e._change();wait_for(lambda:settled(e))


def fill_capacity(e):
    e._layers.extend(new_layer('已有层',True) for _ in range(MAX_LAYERS-len(e._layers)))
    publish(e)


@pytest.mark.parametrize('kind',['blank','tone','inpaint','mixed','group','grouped_repair',
                               'hidden_repair','zero_repair','custom_repair_range','sixty_strokes'])
def test_first_brush_stroke_preserves_existing_effect_and_owns_one_root_transaction(editor,kind):  # noqa: F811
    e=editor
    if kind=='tone':e.setParameter('exposure',.4);e.finishGesture()
    elif kind=='inpaint':e._layer()['inpaint']={'method':'telea','radius':5};publish(e)
    elif kind in ['mixed','grouped_repair','hidden_repair','zero_repair','custom_repair_range']:
        e.drawHeal([[.25,.25]],.01);wait_for(lambda:settled(e))
        if kind=='mixed':e.setParameter('exposure',.4);e.finishGesture()
        elif kind=='hidden_repair':e._layer()['visible']=False;publish(e)
        elif kind=='zero_repair':e.setOpacity(0);e.finishGesture()
        elif kind=='custom_repair_range':
            e._layer()['mask']['ops']=[{'kind':'rect','mode':'add','points':[[0,0],[.2,.2]]}];publish(e)
        elif kind=='grouped_repair':
            child=e.activeLayerId;e.groupLayer();wait_for(lambda:settled(e))
            e._layer()['visible']=False;e._layer()['opacity']=.4
            e._layer()['mask']['ops']=[{'kind':'rect','mode':'add','points':[[0,0],[.2,.2]]}]
            publish(e);e.selection.pickLayer(child)
    elif kind=='group':e.groupLayer()
    elif kind=='sixty_strokes':
        e._layer()['heal']={'ops':[{'kind':'heal','points':[[.25,.25]],'radius':.01} for _ in range(60)]}
        publish(e)
    wait_for(lambda:settled(e))
    before=snapshot(e);old_selected=e.activeLayerId
    assert paint(e)
    wait_for(lambda:settled(e))
    result=deepcopy(e._layer())
    assert len(e._layers)==len(before[0])+1 and e._layers[:-1]==before[0]
    assert result['parent_id']=='' and result['visible'] and result['opacity']==1
    assert not any(result['recipe'].values()) and result['locked']==[]
    assert len(result['heal']['ops'])==1 and len(result['mask']['ops'])==1
    assert result['mask']['ops'][0]['kind']=='brush' and result['mask']['ops'][0]['points']==[[.5,.5]]
    assert e.activeLayerId==e.selection.pickedLayerId==result['id'] and e.activeRepairInfo['isolated']
    assert e._cursor==before[2]+1 and e._generation==before[3]+1
    e.undo();wait_for(lambda:settled(e))
    assert e._layers==before[0] and e.activeLayerId==old_selected and e._cursor==before[2]
    e.redo();wait_for(lambda:settled(e))
    assert e._layers==before[0]+[result] and e.activeLayerId==result['id']


def test_subsequent_stroke_updates_same_repair_range_and_preserves_strength_without_repeated_focus(editor,tmp_path):  # noqa: F811
    e=editor;focus=[];e.selection.repairFocusRequested.connect(focus.append)
    assert paint(e);wait_for(lambda:settled(e));lid=e.activeLayerId
    e.setOpacity(50);e.finishGesture();wait_for(lambda:settled(e))
    before=snapshot(e)
    assert paint(e,[[.7,.7]])
    wait_for(lambda:settled(e))
    assert e.activeLayerId==lid and len(e._layers)==2 and focus==[lid]
    assert e._layer()['opacity']==.5 and len(e._layer()['heal']['ops'])==2
    assert len(e._layer()['mask']['ops'])==2 and e._layer()['mask']['ops'][1]['points']==[[.7,.7]]
    assert e._cursor==before[2]+1 and e._generation==before[3]+1
    target=tmp_path/'repair.iphoto';e.saveProject(str(target))
    saved=read_project(target);assert saved['layers']==e._layers
    e.openProject(str(target));wait_for(lambda:not e._pending_project and settled(e))
    assert e.activeLayerId==lid and paint(e,[[.8,.8]])
    wait_for(lambda:settled(e))
    assert len(e._layers)==2 and len(e._layer()['heal']['ops'])==3 and e._layer()['opacity']==.5


def test_legacy_pure_repair_can_be_continued_without_changing_its_strength(editor):  # noqa: F811
    e=editor;e.drawHeal([[.2,.2]],.01);e.setOpacity(50);e.finishGesture();wait_for(lambda:settled(e))
    lid=e.activeLayerId;before=deepcopy(e._layers)
    assert paint(e);wait_for(lambda:settled(e))
    assert len(e._layers)==1 and e.activeLayerId==lid and e._layer()['opacity']==.5
    assert len(e._layer()['heal']['ops'])==2 and len(e._layer()['mask']['ops'])==2
    e.undo();wait_for(lambda:settled(e));assert e._layers==before


@pytest.mark.parametrize('reuse',[False,True])
def test_capacity_rejects_new_layer_atomically_but_existing_repair_can_continue(editor,reuse):  # noqa: F811
    e=editor
    if reuse:assert paint(e)
    fill_capacity(e);before=snapshot(e)
    assert paint(e)==reuse
    wait_for(lambda:settled(e))
    assert len(e._layers)==MAX_LAYERS
    if reuse:assert len(e._layer()['heal']['ops'])==2 and e._cursor==before[2]+1
    else:assert snapshot(e)==before and '位置已满' in e.status


@pytest.mark.parametrize('case',['layer','photo','generation','bool_generation','busy','draft','region','unavailable'])
def test_stale_or_blocked_stroke_never_creates_partial_layer(editor,monkeypatch,case):  # noqa: F811
    e=editor;binding={}
    if case=='layer':binding['layer']='other'
    elif case=='photo':binding['photo']='file:///other-photo.png'
    elif case=='generation':binding['generation']=e.documentGeneration-1
    elif case=='bool_generation':binding['generation']=True
    elif case=='busy':monkeypatch.setattr(e,'_can_edit',lambda:False)
    elif case=='draft':e.beginSelection('empty');wait_for(lambda:settled(e))
    elif case=='region':e._region_candidate={}
    elif case=='unavailable':monkeypatch.setattr(heal,'available',lambda:False)
    before=snapshot(e)
    assert not paint(e,**binding) and snapshot(e)==before


@pytest.mark.parametrize('points,radius',[([[2,.5]],.01),([],.01),([[.5,.5]],float('nan')),
                                        ([[.5,.5]],float('inf')),([[.5,.5]],.9)])
def test_invalid_stroke_does_not_add_an_empty_repair_layer(editor,points,radius):  # noqa: F811
    e=editor;before=snapshot(e)
    assert not paint(e,points,radius) and snapshot(e)==before


def test_pending_parameter_gesture_is_committed_before_the_single_repair_transaction(editor):  # noqa: F811
    e=editor;original=deepcopy(e._layers);cursor=e._cursor
    e.setParameter('exposure',.4);expected=deepcopy(e._layers)
    assert paint(e);wait_for(lambda:settled(e));assert e._cursor==cursor+2
    e.undo();wait_for(lambda:settled(e));assert e._layers==expected
    e.undo();wait_for(lambda:settled(e));assert e._layers==original


def test_ai_can_modify_named_smoothing_after_manual_repair_while_repair_is_preserved(editor):  # noqa: F811
    e=editor;face,_,_=setup_layers(e);e.selection.pickLayer(face['id'])
    wait_for(lambda:settled(e));assert paint(e);wait_for(lambda:settled(e))
    before=deepcopy(e._layers);repair=deepcopy(e._layer());cursor=e._cursor
    with mock_api(response(e.parameters,[edit_for(face,skin_smoothing=15)])) as (url,requests):
        configure(e.ai,url);assert e.sendMessage('面部磨皮轻一点，保留修复和其他效果','auto')
        wait_for(lambda:not e.ai.busy and e.conversation[-1]['state']=='applied' and settled(e))
        assert len(requests)==1 and len(e._layers)==len(before)
        assert next(l for l in e._layers if l['id']==repair['id'])==repair
        changed=next(l for l in e._layers if l['id']==face['id'])
        assert changed['recipe']['skin_smoothing']==15 and changed['mask']==face['mask']
        assert e._cursor==cursor+1
        context=json.loads(requests[0][2]['messages'][1]['content'][0]['text'])
        assert any(l['id']==repair['id'] for l in context['existing_layers'])
    e.undo();wait_for(lambda:settled(e))
    assert e._layers==before and e.activeLayerId==repair['id']


def test_brush_call_during_ai_with_current_range_cannot_consume_the_pending_request(editor):  # noqa: F811
    e=editor;e.beginSelection('empty');wait_for(lambda:settled(e))
    e.drawDraft('rect','replace',[[.2,.2],[.7,.7]],.01);wait_for(lambda:settled(e))
    with mock_api(selection_response(),delay=.2) as (url,requests):
        configure(e.ai,url);assert e.sendMessage('只提亮这个范围','auto')
        wait_for(lambda:e.ai.busy)
        before=snapshot(e);pending=e._pending_request;messages=deepcopy(e.conversation)
        assert not paint(e) and snapshot(e)==before
        assert e._pending_request==pending and e.conversation==messages and e.ai.busy
        wait_for(lambda:not e.ai.busy and settled(e))
        assert len(requests)==1 and not e.hasSelectionDraft and e.conversation[-1]['state']=='applied'
        assert not any(l.get('heal') for l in e._layers)


def test_paint_during_project_save_preserves_writer_snapshot_and_remains_dirty(editor,tmp_path,monkeypatch):  # noqa: F811
    e=editor;before=deepcopy(e._layers);started=Event();release=Event();writer=session.write_project
    def delayed(path,payload,overwrite=False):
        started.set();assert release.wait(3)
        return writer(path,payload,overwrite=overwrite)
    monkeypatch.setattr(session,'write_project',delayed)
    target=tmp_path/'save-before-repair.iphoto'
    try:
        assert e.saveProjectAsync(str(target));wait_for(lambda:started.is_set())
        assert e.savingProject and paint(e)
        wait_for(lambda:settled(e));assert len(e._layers)==len(before)+1
    finally:release.set()
    wait_for(lambda:not e.savingProject)
    assert e.dirty and read_project(target)['layers']==before
    assert e._layers[:-1]==before and e.activeRepairInfo['count']==1


def test_actual_stroke_reveals_repair_strength_and_reuse_keeps_manual_scroll(canvas):  # noqa: F811
    ui=canvas;ui.e.selection.pickLayer(ui.e.activeLayerId)
    requested=[];ui.e.selection.repairFocusRequested.connect(requested.append)
    # Reproduce the previous layer's detail section being on screen.
    ui.e.selection.parameterFocusRequested.emit(ui.e.activeLayerId,'skin_smoothing')
    wait_for(lambda:contained(ui.find('parameterRow_skin_smoothing'),ui.find('propertyScroll')))
    before=deepcopy(ui.e._layers);cursor=ui.e._cursor;generation=ui.e._generation
    ui.click('tool_heal');ui.click('photoCanvas')
    wait_for(lambda:settled(ui.e) and ui.e.activeRepairInfo['count']==1)
    try:
        wait_for(lambda:contained(ui.find('repairControls'),ui.find('propertyScroll')))
    except AssertionError:
        scroll=ui.find('propertyScroll');controls=ui.find('repairControls')
        print('focus diagnostic',requested,ui.e.selection.pickedLayerId,ui.e.activeRepairInfo,
              controls.isVisible(),controls.height(),controls.mapToItem(scroll,QPointF()).y(),
              scroll.height(),scroll.property('contentItem').property('contentY'),
              scroll.parentItem().parentItem().property('pendingParameterRow'))
        raise
    assert len(ui.e._layers)==2 and ui.e._layers[0]==before[0]
    assert ui.e._cursor==cursor+1 and ui.e._generation==generation+1
    assert contained(ui.find('repairStrengthSlider'),ui.find('propertyScroll'))
    assert '修复强度' in ui.find('repairStrengthCaption').property('text')
    assert ui.find('reviewLayerRepairsFooterButton').isVisible() and ui.find('continueLayerRepairButton').isVisible()
    assert not ui.find('addLayerMaskButton').isVisible() and not ui.find('eraseLayerMaskButton').isVisible()
    ui.click('repairStrengthSlider');wait_for(lambda:settled(ui.e));assert ui.e.layerOpacity==50
    lid=ui.e.activeLayerId;view=ui.find('propertyScroll').property('contentItem')
    ui.e.selection.parameterFocusRequested.emit(lid,'skin_smoothing')
    wait_for(lambda:contained(ui.find('parameterRow_skin_smoothing'),ui.find('propertyScroll')))
    offset=view.property('contentY');assert offset>0
    ui.click('photoCanvas',.6,.6);wait_for(lambda:settled(ui.e))
    assert ui.e.activeLayerId==lid and len(ui.e._layers)==2 and ui.e.activeRepairInfo['count']==2
    assert view.property('contentY')==pytest.approx(offset,abs=1)


def test_actual_drag_drops_stale_stroke_if_layer_changes_before_release(canvas):  # noqa: F811
    ui=canvas;first=ui.e.activeLayerId;ui.e.addGlobalLayer();wait_for(lambda:settled(ui.e))
    ui.click('tool_heal');point=ui.point('photoCanvas')
    QTest.mousePress(ui.w,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(30)
    ui.e.selection.pickLayer(first);wait_for(lambda:settled(ui.e))
    before=snapshot(ui.e)
    QTest.mouseRelease(ui.w,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(60)
    assert snapshot(ui.e)==before and not any(l.get('heal') for l in ui.e._layers)


def test_footer_reviews_actual_strokes_and_continues_without_an_extra_layer_or_edit(canvas):  # noqa: F811
    ui=canvas;ui.e.selection.pickLayer(ui.e.activeLayerId)
    ui.click('tool_heal');ui.click('photoCanvas',.5,.5);wait_for(lambda:settled(ui.e))
    ui.click('photoCanvas',.6,.6);wait_for(lambda:settled(ui.e))
    before=snapshot(ui.e);lid=ui.e.activeLayerId
    points=[op['points'][0] for op in ui.e._layer()['heal']['ops']]
    for point in points:
        ui.click('reviewLayerRepairsFooterButton');wait_for(lambda:settled(ui.e))
        viewport=ui.e.viewport
        center=((viewport._size[0]/2-viewport.imageX)/viewport.imageWidth,
                (viewport._size[1]/2-viewport.imageY)/viewport.imageHeight)
        assert center==pytest.approx(point) and viewport.zoom==1 and ui.e.selection.tool=='inspect'
        assert snapshot(ui.e)==before
    ui.click('continueLayerRepairButton')
    assert ui.e.selection.tool=='heal' and not ui.e.selection.showMask and snapshot(ui.e)==before
    ui.click('canvasSurface');wait_for(lambda:settled(ui.e))
    assert ui.e.activeLayerId==lid and len(ui.e._layers)==2 and ui.e.activeRepairInfo['count']==3
    assert ui.e._cursor==before[2]+1 and ui.e._generation==before[3]+1
