"""Compose and persist multi-line requests without sending or losing a tail."""
from copy import deepcopy
from dataclasses import replace

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication, QInputMethodEvent
from PySide6.QtTest import QTest

from iphoto.document import read_project
from test_ai import completion, configure, mock_api, wait_for
from test_editor import settled
from test_import_export import ui  # noqa: F401


def click(window, item):
    assert item.isVisible() and item.property('enabled')
    point=item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    assert 0<=point.x()<window.width() and 0<=point.y()<window.height()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(30)


def paste(window, text):
    clipboard=QGuiApplication.clipboard(); previous=clipboard.text()
    try:
        clipboard.setText(text)
        QTest.keyClick(window, Qt.Key_V, Qt.ControlModifier)
        QTest.qWait(30)
    finally:
        clipboard.setText(previous)


@pytest.fixture(params=[(1440,930),(1080,700)])
def compose(request):
    context=request.getfixturevalue('ui')
    editor,window,find,_,_=context
    editor.ai.settings=replace(editor.ai.settings,enabled=False)
    editor.ai.changed.emit()
    window.resize(*request.param);window.requestActivate();QTest.qWait(100)
    if not window.property('chatOpen'):
        click(window,find('chatToggleButton'))
    click(window,find('descriptionInput'))
    return context


def document(editor):
    return deepcopy(editor._layers),deepcopy(editor._history),editor._cursor,editor.documentGeneration


def test_shift_return_and_keypad_enter_compose_then_send_exactly_once(compose):
    editor,window,find,warnings,_=compose
    field=find('descriptionInput');before=document(editor)
    paste(window,'人物轻磨皮，保留眉眼和嘴唇。')
    QTest.keyClick(window,Qt.Key_Return,Qt.ShiftModifier)
    paste(window,'背景保持原来的清晰度。')
    QTest.keyClick(window,Qt.Key_Enter,Qt.ShiftModifier)
    paste(window,'只增加一点曝光。')
    expected='人物轻磨皮，保留眉眼和嘴唇。\n背景保持原来的清晰度。\n只增加一点曝光。'
    assert field.property('text')==editor.conversationDraft==expected
    assert editor.conversationCount==0 and document(editor)==before
    hint=find('conversationInputKeyHint')
    assert hint.isVisible() and 'Shift+Enter' in hint.property('text')
    QTest.keyClick(window,Qt.Key_Enter,Qt.ControlModifier)
    wait_for(lambda:not editor.busy and settled(editor) and editor.conversationCount>0)
    assert editor.conversation[0]['text']==expected
    assert field.property('text')==editor.conversationDraft==''
    assert field.hasActiveFocus() and window.property('textFocus')
    count=editor.conversationCount
    QTest.keyClick(window,Qt.Key_Return)
    assert editor.conversationCount==count and not warnings


@pytest.mark.parametrize('edit', ['append','prefix','replace','unicode'])
def test_limit_preserves_existing_tail_and_caret_stays_visible(compose,edit):
    editor,window,find,warnings,_=compose
    field=find('descriptionInput');scroll=find('conversationInputScroll');before=document(editor)
    if edit=='append':
        initial='保留皮肤纹理。\n'*390
        incoming='面部轻磨皮，背景保持。\n'*300
        field.setProperty('text',initial)
        QTest.keyClick(window,Qt.Key_End,Qt.ControlModifier)
        paste(window,incoming)
        expected=(initial+incoming)[:4000]
    elif edit=='prefix':
        initial='已有的尾部要求保持。\n'*300
        field.setProperty('text',initial)
        QTest.keyClick(window,Qt.Key_Home,Qt.ControlModifier)
        paste(window,'请先分析面部。'*1000)
        expected=('请先分析面部。'*1000)[:4000-len(initial)]+initial
    elif edit=='replace':
        field.setProperty('text','要替换的旧要求')
        QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
        paste(window,'保持现有调色，检查皮肤。\n'*350)
        expected=('保持现有调色，检查皮肤。\n'*350)[:4000]
    else:
        field.setProperty('text','a'*3999)
        QTest.keyClick(window,Qt.Key_End,Qt.ControlModifier)
        paste(window,'🙂')
        expected='a'*3999
    assert field.property('text')==editor.conversationDraft==expected
    assert len(expected)<=4000 and editor.conversationCount==0 and document(editor)==before
    assert scroll.height()<=78 and find('canvasSurface').height()>100
    if edit!='unicode':
        QTest.keyClick(window,Qt.Key_End,Qt.ControlModifier)
        QTest.qWait(100)
        rect=field.property('cursorRectangle')
        top=field.mapToItem(scroll,rect.topLeft()).y()
        bottom=field.mapToItem(scroll,rect.bottomRight()).y()
        assert -1<=top and bottom<=scroll.height()+1,(top,bottom,scroll.height())
    assert not warnings


def test_rejected_multiline_advice_preserves_entire_draft(compose):
    editor,window,find,warnings,_=compose
    find('chatModeBox').setProperty('currentIndex',1)
    field=find('descriptionInput');text='请分析人物面部。\n解释磨皮和提亮的原因。'
    paste(window,text);before=document(editor)
    QTest.keyClick(window,Qt.Key_Return)
    assert field.property('text')==editor.conversationDraft==text
    assert editor.conversationCount==0 and document(editor)==before
    assert '云端模型' in editor.status and not warnings


def test_compose_next_request_while_ai_waits_without_duplicate_send(compose):
    editor,window,find,warnings,_=compose
    field=find('descriptionInput')
    with mock_api(completion(),delay=.4) as (url,requests):
        configure(editor.ai,url)
        paste(window,'先整体提亮一点。\n保留当前色彩。')
        QTest.keyClick(window,Qt.Key_Return)
        wait_for(lambda:editor.ai.busy and len(requests)==1)
        count=editor.conversationCount
        paste(window,'下一步调整面部。')
        QTest.keyClick(window,Qt.Key_Return,Qt.ShiftModifier)
        paste(window,'先保留这一段，等上一步完成。')
        text=field.property('text')
        assert '\n' in text and editor.conversationDraft==text
        QTest.keyClick(window,Qt.Key_Return,Qt.ControlModifier)
        assert editor.conversationCount==count and field.property('text')==text
        wait_for(lambda:not editor.ai.busy and settled(editor))
        assert len(requests)==1 and editor.conversationDraft==field.property('text')==text
        assert sum(m['role']=='user' for m in editor.conversation)==1
    assert not warnings


def test_multiline_save_reopen_and_photo_switch_preserve_scope(compose):
    editor,window,find,warnings,tmp_path=compose
    text='第一张照片：人物提亮。\n保护眉眼、嘴唇。\n背景不磨皮。'
    paste(window,text);original=deepcopy(editor._layers);cursor=editor._cursor
    target=tmp_path/'multiline.iphoto'
    editor.saveProjectAsync(str(target))
    wait_for(lambda:not editor.savingProject and editor.projectPath==str(target))
    stored=read_project(target)
    assert stored['conversation_draft']==text and stored['layers']==original and editor._cursor==cursor
    other=tmp_path/'other.png';Image.new('RGB',(300,200),'orange').save(other)
    editor.openImage(str(other));wait_for(lambda:settled(editor) and editor.imageName=='other.png')
    assert find('descriptionInput').property('text')==''
    editor.openProject(str(target));wait_for(lambda:settled(editor) and editor.projectPath==str(target))
    assert find('descriptionInput').property('text')==editor.conversationDraft==text
    assert editor._layers==original and editor.conversationCount==0 and not warnings


def test_typing_shortcuts_and_text_undo_do_not_change_photo(compose):
    editor,window,find,warnings,_=compose
    field=find('descriptionInput');before=document(editor);tool=editor.selection.tool
    paste(window,'人物轻磨皮。\n背景保持。')
    QTest.keyClick(window,Qt.Key_J)
    assert field.property('text').endswith('j') and editor.selection.tool==tool
    composed=field.property('text')
    QTest.keyClick(window,Qt.Key_Z,Qt.ControlModifier)
    # Qt may group the adjacent paste and typing into one text undo command.
    assert field.property('text')!=composed and document(editor)==before
    QTest.keyClick(window,Qt.Key_Z,Qt.ControlModifier|Qt.ShiftModifier)
    assert field.property('text')==composed and editor.conversationDraft==composed
    QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
    QTest.keyClick(window,Qt.Key_Backspace)
    assert field.property('text')==editor.conversationDraft==''
    assert document(editor)==before and editor.conversationCount==0 and not warnings


def test_ime_preedit_return_does_not_send_before_commit(compose):
    editor,window,find,warnings,_=compose
    field=find('descriptionInput');before=document(editor)
    paste(window,'人物')
    preedit=QInputMethodEvent('轻磨皮',[])
    QGuiApplication.sendEvent(field,preedit)
    assert field.property('inputMethodComposing')
    QTest.keyClick(window,Qt.Key_Return)
    assert editor.conversationCount==0 and document(editor)==before
    event=QInputMethodEvent();event.setCommitString('轻磨皮')
    QGuiApplication.sendEvent(field,event)
    assert not field.property('inputMethodComposing')
    assert '轻磨皮' in field.property('text')
    assert field.property('text')==editor.conversationDraft and not warnings
