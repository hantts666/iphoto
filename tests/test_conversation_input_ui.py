"""Submitting a chat instruction clears only requests accepted by the editor."""

from dataclasses import replace

from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest

from iphoto.workspace import ROOT
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui


def _offline(editor):
    editor.ai.settings = replace(editor.ai.settings, enabled=False)
    editor.ai.changed.emit()


def test_accepted_chat_prompt_clears_after_enter(ui):
    editor, window, find, warnings, tmp_path = ui
    _offline(editor)
    assert not editor.ai.enabled
    field = find("descriptionInput")
    field.setProperty("text", "把照片提亮一点")
    field.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Return)
    wait_for(lambda: editor.conversationCount >= 1)
    assert field.property("text") == ""
    wait_for(lambda: not editor.busy and settled(editor))
    count = editor.conversationCount
    QTest.keyClick(window, Qt.Key_Return)
    assert editor.conversationCount == count
    assert "请输入 1～4000 字" not in editor.status
    assert not warnings


def test_rejected_advice_prompt_stays_editable_without_duplicate_message(ui):
    editor, window, find, warnings, tmp_path = ui
    _offline(editor)
    assert not editor.ai.enabled
    find("chatModeBox").setProperty("currentIndex", 1)
    field = find("descriptionInput")
    field.setProperty("text", "给我一条修图建议")
    field.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Return)
    assert field.property("text") == "给我一条修图建议"
    assert editor.conversationCount == 0
    assert "云端模型" in editor.status
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-chat-prompt-rejected.png"))
    assert not warnings


def test_missing_ai_key_opens_settings_without_losing_prompt(ui):
    editor, window, find, warnings, tmp_path = ui
    assert editor.ai.enabled and not editor.ai.ready
    field = find("descriptionInput")
    field.setProperty("text", "先帮我分析天空")
    field.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Return)
    settings = window.findChild(QObject, "aiSettingsDialog")
    assert settings and settings.property("opened")
    assert field.property("text") == "先帮我分析天空"
    assert editor.conversationCount == 0
    assert not warnings
