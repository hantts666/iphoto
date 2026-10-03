"""Failed AI chat can be recovered without losing text or changing scope."""
from copy import deepcopy
import json

from PIL import Image
from PySide6.QtGui import QGuiApplication
import pytest

from iphoto.document import read_project, validate_conversation
from iphoto.engine import Recipe
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_ai_current_selection import draft, selection_response
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_preset_focus_ui import contained


def done(editor):
    wait_for(lambda:not editor.ai.busy and not editor._pending_request and settled(editor))


def snapshot(editor):
    return deepcopy((editor._layers,editor._history,editor._cursor,editor._generation))


def failure(ui,mode='auto',text='把面部轻微提亮，其他部分保留'):
    ui.w.setProperty('chatOpen',True)
    with mock_api({},status=403) as (url,requests):
        configure(ui.e.ai,url)
        if mode=='auto':
            ui.find('descriptionInput').setProperty('text',text)
            ui.click('applyDescriptionButton')
        else:assert ui.e.sendMessage(text,mode)
        done(ui.e)
        assert len(requests)==1 and ui.e.conversation[-1]['state']=='failed'
        wait_for(lambda:contained(ui.find('restoreFailedPrompt_'+ui.e.conversation[-1]['id']),ui.find('conversationList')))
    return ui.e.conversation[-1]


def test_failed_prompt_restores_text_mode_and_opens_settings_without_editing_or_request(canvas,tmp_path):  # noqa: F811
    ui,editor=canvas,canvas.e
    message=failure(ui)
    original=editor.conversation[-2]['text']
    before=snapshot(editor)
    assert ui.find('descriptionInput').property('text')==''
    wait_for(lambda:contained(ui.find('restoreFailedPrompt_'+message['id']),ui.find('conversationList')))
    ui.click('restoreFailedPrompt_'+message['id'])
    assert ui.find('descriptionInput').property('text')==original
    assert ui.find('chatModeBox').property('currentIndex')==0 and snapshot(editor)==before
    ui.click('failureAISettings_'+message['id'])
    assert ui.find('aiSettingsDialog').property('opened') and snapshot(editor)==before
    ui.find('aiSettingsDialog').setProperty('visible',False)
    project=tmp_path/'failed-chat.iphoto'
    editor.saveProject(str(project))
    payload=read_project(project)
    assert payload['conversation'][-1]['request_user_id']==payload['conversation'][-2]['id']
    assert payload['conversation_draft']==original
    editor.openProject(str(project))
    wait_for(lambda:not editor._pending_project and settled(editor))
    assert editor.failedPromptInfo(message['id'])['text']==original


def test_direct_retry_uses_original_request_with_one_new_transaction(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    message=failure(ui)
    before,cursor=deepcopy(editor._layers),editor._cursor
    expected=Recipe(exposure=.2).to_dict()
    with mock_api(auto_response(action='adjust',regions=[],recipe=expected),delay=.25) as (url,requests):
        configure(editor.ai,url)
        wait_for(lambda:contained(ui.find('retryFailedPrompt_'+message['id']),ui.find('conversationList')))
        ui.click('retryFailedPrompt_'+message['id'])
        assert editor.ai.busy and not editor.retryFailedPrompt(message['id'])
        done(editor)
        assert len(requests)==1 and editor._cursor==cursor+1 and editor.parameters==expected
        content=json.loads(requests[0][2]['messages'][1]['content'][0]['text'])
        assert content['request']==editor.conversation[-2]['text']
        assert editor.conversation[-1]['state']=='applied'
        editor.undo()
        done(editor)
        assert editor._layers==before


@pytest.mark.parametrize('change',['recipe','mask','locked','heal','inpaint','name','selected','selection'])
def test_changed_context_restores_for_review_and_never_sends_old_request(canvas,change):  # noqa: F811
    ui,editor=canvas,canvas.e
    message=failure(ui)
    if change=='recipe':editor.setParameter('exposure',.3);editor.finishGesture()
    elif change=='mask':editor._layer()['mask']['inverted']=True;editor._change()
    elif change=='locked':editor._locked.add('warmth');editor._sync_layer()
    elif change=='heal':editor.drawHeal([[.5,.5]],.003)
    elif change=='inpaint':editor._layer()['inpaint']={'method':'ns','radius':3.0}
    elif change=='name':editor.renameLayer('新的名字')
    elif change=='selected':editor.addGlobalLayer()
    else:editor.beginSelection('empty')
    done(editor)
    before=snapshot(editor)
    with mock_api(auto_response()) as (url,requests):
        configure(editor.ai,url)
        assert not editor.retryFailedPrompt(message['id'])
        assert not requests and snapshot(editor)==before
        assert editor.conversationDraft==editor.failedPromptInfo(message['id'])['text']
        assert '未重试' in editor.status


def test_new_draft_is_preserved_and_original_request_is_copied(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    message=failure(ui)
    ui.find('descriptionInput').setProperty('text','新的要求正在写')
    before=snapshot(editor)
    assert not ui.find('retryFailedPrompt_'+message['id']).property('enabled')
    assert ui.find('restoreFailedPrompt_'+message['id']).property('text')=='复制要求'
    ui.click('restoreFailedPrompt_'+message['id'])
    assert editor.conversationDraft=='新的要求正在写' and snapshot(editor)==before
    assert QGuiApplication.clipboard().text()==editor.failedPromptInfo(message['id'])['text']


def test_legacy_failure_only_restores_adjacent_request_and_rejects_mismatched_reference(canvas):  # noqa: F811
    editor=canvas.e
    editor._message('user','旧要求',mode='advice')
    legacy=editor._message('error','HTTP 403：无权限',mode='advice',state='failed')
    info=editor.failedPromptInfo(legacy['id'])
    assert info['available'] and not info['retry'] and info['settings']
    assert editor.restoreFailedPrompt(legacy['id']) and editor.conversationDraftMode=='advice'
    editor._message('assistant','中间回复',mode='advice',state='answered')
    ambiguous=editor._message('error','发生错误',mode='advice',state='failed')
    assert not editor.failedPromptInfo(ambiguous['id']).get('available')
    modern=failure(canvas)
    modern['request_user_id']=legacy['id']
    assert not editor.failedPromptInfo(modern['id']).get('available')


@pytest.mark.parametrize('mode,index',[('advice',1),('regions',2)])
def test_restored_prompt_keeps_advice_or_region_preview_mode(canvas,mode,index):  # noqa: F811
    ui,editor=canvas,canvas.e
    message=failure(ui,mode=mode)
    ui.click('restoreFailedPrompt_'+message['id'])
    assert ui.find('chatModeBox').property('currentIndex')==index
    assert editor.conversationDraftMode==mode


def test_retry_of_current_corrected_range_keeps_hole_and_creates_one_layer(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    mask=draft(ui)
    message=failure(ui,text='只提亮这个范围，保留孔洞')
    before,cursor=deepcopy(editor._layers),editor._cursor
    assert editor._candidate==mask
    with mock_api(selection_response()) as (url,requests):
        configure(editor.ai,url)
        ui.click('retryFailedPrompt_'+message['id'])
        done(editor)
        assert len(requests)==1 and editor._layer()['mask']==mask
        assert editor._layers[:-1]==before and editor._cursor==cursor+1
        editor.undo()
        done(editor)
        assert editor._layers==before


def test_saved_failure_can_retry_after_project_reopen_but_not_on_another_photo(canvas,tmp_path):  # noqa: F811
    ui,editor=canvas,canvas.e
    message=failure(ui)
    project=tmp_path/'retry.iphoto'
    editor.saveProject(str(project))
    editor.openProject(str(project))
    wait_for(lambda:not editor._pending_project and settled(editor))
    with mock_api(auto_response(action='answer',regions=[])) as (url,requests):
        configure(editor.ai,url)
        assert editor.retryFailedPrompt(message['id'])
        done(editor)
        assert len(requests)==1
    other=tmp_path/'other.png'
    Image.new('RGB',(120,90),'orange').save(other)
    editor.openImage(str(other))
    done(editor)
    assert not editor.failedPromptInfo(message['id'])
    assert not editor.retryFailedPrompt(message['id'])


@pytest.mark.parametrize('fields',[{'request_binding':'x'}, {'request_binding':True},
                                 {'request_user_id':''},{'request_user_id':23}])
def test_invalid_saved_request_context_is_rejected(fields):
    with pytest.raises(ValueError):
        validate_conversation([{'id':'a','role':'error',**fields}])
