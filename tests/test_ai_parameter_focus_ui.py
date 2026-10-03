"""AI results expose actual edited controls without undo or scroll side effects."""
from copy import deepcopy
import json

import pytest
from PySide6.QtTest import QSignalSpy, QTest

from iphoto.engine import Recipe
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response, with_skin_grounding
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_preset_focus_ui import contained


@pytest.mark.parametrize('chat',[False,True])
def test_auto_portrait_exposes_applied_strength_and_preserves_manual_scroll(canvas,pixel_protocol_stub,chat):  # noqa: F811
    ui,editor=canvas,canvas.e
    ui.w.setProperty('chatOpen',True)
    ui.find('adjustmentSection_1').setProperty('expanded',True)
    before,cursor=deepcopy(editor._layers),editor._cursor
    recipe=Recipe(exposure=.2,skin_smoothing=25).to_dict()
    region={'name':'面部皮肤','reason':'轻磨皮','box':[200,200,650,650],'point':[400,400],'recipe':recipe}
    with mock_api(with_skin_grounding(auto_response(regions=[region])),delay=.15) as (url,requests):
        configure(editor.ai,url)
        ui.find('descriptionInput').setProperty('text','面部轻磨皮并稍微提亮，自动分层')
        ui.click('applyDescriptionButton')
        if not chat:ui.click('chatCollapseButton')
        row,scroll=ui.find('parameterRow_skin_smoothing'),ui.find('propertyScroll')
        wait_for(lambda:settled(editor) and not editor._pending_request and contained(row,scroll))
        assert len(requests)==2 and editor.parameters==recipe and editor._cursor==cursor+1
        assert editor._layers[:-1]==before and editor.selection.pickedLayerId==editor.activeLayerId
        assert ui.find('adjustmentSection_1').property('expanded')
        slider=ui.find('parameter_skin_smoothing')
        assert contained(slider,scroll) and slider.property('value')==25
        ui.drag(ui.point('parameter_skin_smoothing',slider.property('visualPosition')),
                ui.point('parameter_skin_smoothing',.55))
        wait_for(lambda:settled(editor))
        assert editor.parameters['skin_smoothing']>25 and editor._cursor==cursor+2
        assert 'skin_smoothing' in editor.lockedFields
        editor.undo()
        wait_for(lambda:settled(editor))
        assert editor.parameters==recipe and editor._cursor==cursor+1
        flick=scroll.property('contentItem')
        flick.setProperty('contentY',0)
        editor.setParameter('sharpness',10)
        editor.finishGesture()
        wait_for(lambda:settled(editor))
        assert flick.property('contentY')==0
        editor.undo()
        wait_for(lambda:settled(editor))
        editor.undo()
        wait_for(lambda:settled(editor))
        assert editor._layers==before


def test_current_edit_focuses_changed_colour_instead_of_old_smoothing(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    editor._layer()['recipe']=Recipe(exposure=.1,skin_smoothing=30).to_dict()
    editor._load_layer()
    editor._commit()
    editor._change()
    wait_for(lambda:settled(editor))
    editor.selection.clearPick()
    cursor=editor._cursor
    body={'status':'applied','summary':'仅增加暖色','recipe':Recipe(exposure=.1,skin_smoothing=30,warmth=15).to_dict()}
    response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(body)}}]}
    with mock_api(response) as (url,requests):
        configure(editor.ai,url)
        editor.sendMessage('保留磨皮，暖一点','edit')
        row,scroll=ui.find('parameterRow_warmth'),ui.find('propertyScroll')
        wait_for(lambda:settled(editor) and not editor._pending_request and contained(row,scroll))
        assert len(requests)==1 and editor._cursor==cursor+1
        assert ui.find('adjustmentSection_1').property('expanded')
        assert not ui.find('adjustmentSection_2').property('expanded')
        assert editor.parameters['skin_smoothing']==30 and editor.parameters['warmth']==15


@pytest.mark.parametrize('condition',['unchanged','hidden','group'])
def test_non_applied_or_invisible_effect_does_not_redirect_parameter_view(canvas,condition):  # noqa: F811
    editor=canvas.e
    if condition=='group':
        editor.groupLayer()
        wait_for(lambda:settled(editor))
    elif condition=='hidden':
        editor._layer()['visible']=False
        editor._layer()['recipe']=Recipe(skin_smoothing=25).to_dict()
        editor._load_layer()
        editor._change()
        wait_for(lambda:settled(editor))
    editor.selection.clearPick()
    previous=editor.parameters if condition=='unchanged' else {'skin_smoothing':25} if condition=='group' else {}
    spy=QSignalSpy(editor.selection.parameterFocusRequested)
    cursor=editor._cursor
    editor.selection.focusChangedParameters(previous)
    QTest.qWait(60)
    assert spy.count()==0 and not editor.selection.pickedLayerId and editor._cursor==cursor


def test_deferred_ai_parameter_reveal_cannot_scroll_new_range_view(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    editor._recipe=Recipe(skin_smoothing=25).to_dict()
    editor._sync_layer()
    editor.selection.focusChangedParameters()
    editor.selection.clearPick()
    QTest.qWait(80)
    assert not editor.selection.pickedLayerId
    assert ui.find('propertyScroll').property('contentItem').property('contentY')==0


def test_last_colour_control_remains_reachable_in_short_pane_with_locked_rows(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    ui.w.setProperty('chatOpen',True)
    editor._recipe=Recipe(warmth=10,tint=5,saturation=8,vibrance=20).to_dict()
    editor._locked={'warmth','tint','saturation'}
    editor._sync_layer()
    editor._commit()
    editor._change()
    wait_for(lambda:settled(editor))
    previous={**editor.parameters,'vibrance':0}
    cursor=editor._cursor
    editor.selection.focusChangedParameters(previous)
    row,scroll=ui.find('parameterRow_vibrance'),ui.find('propertyScroll')
    wait_for(lambda:contained(row,scroll))
    assert contained(ui.find('parameter_vibrance'),scroll)
    assert editor._cursor==cursor and editor.parameters['vibrance']==20
