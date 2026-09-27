"""Conversation export keeps a failed destination editable in the real QML UI."""

from pathlib import Path
from threading import Event
from time import monotonic

from PySide6.QtCore import QPointF, QObject, QTimer, Qt
from PySide6.QtTest import QTest

from iphoto import storage
from iphoto.controllers import conversation
from iphoto.workspace import ROOT
from test_ai import wait_for
from test_import_export import ui


def _click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def test_conversation_export_failure_keeps_path_and_retries(ui):
    editor, window, find, warnings, tmp_path = ui
    editor._message("user", "帮我保留天空的颜色")
    _click(window, find("exportConversationButton"))
    dialog = window.findChild(QObject, "conversationExportDialog")
    assert dialog and dialog.property("opened")
    suggested = Path(dialog.property("filePath"))
    assert suggested.parent == tmp_path and suggested.suffix == ".md"
    occupied = tmp_path / "occupied.md"
    occupied.write_text("原有记录", encoding="utf-8")
    dialog.setProperty("filePath", str(occupied))
    _click(window, find("conversationExportConfirmButton"))
    wait_for(lambda: dialog.property("opened") and "已存在" in dialog.property("errorText"))
    assert dialog.property("opened")
    assert "已存在" in dialog.property("errorText")
    assert occupied.read_text(encoding="utf-8") == "原有记录"
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-conversation-export-retry.png"))
    target = tmp_path / "new-chat.md"
    path_field = find("conversationExportPathField")
    assert path_field.property("activeFocus")
    path_field.setProperty("text", str(target))
    QTest.keyClick(window, Qt.Key_Return)
    wait_for(lambda: target.exists() and not editor.exportingConversation)
    assert target.exists() and "帮我保留天空的颜色" in target.read_text(encoding="utf-8")
    assert not dialog.property("opened")
    assert not warnings


def test_conversation_export_write_failure_leaves_no_partial_file(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    editor._message("user", "这一条不能丢")
    _click(window, find("exportConversationButton"))
    dialog = window.findChild(QObject, "conversationExportDialog")
    target = tmp_path / "recoverable.md"
    dialog.setProperty("filePath", str(target))

    with monkeypatch.context() as patch:
        def fail_fsync(_fd):
            raise OSError("disk full")

        patch.setattr(storage.os, "fsync", fail_fsync)
        _click(window, find("conversationExportConfirmButton"))
        wait_for(lambda: dialog.property("opened") and "disk full" in dialog.property("errorText"))

    assert dialog.property("opened")
    assert "disk full" in dialog.property("errorText")
    assert not target.exists()
    assert not list(tmp_path.glob(".recoverable.md.*.tmp"))
    path_field = find("conversationExportPathField")
    assert path_field.property("activeFocus")
    QTest.keyClick(window, Qt.Key_Return)
    wait_for(lambda: target.exists() and not editor.exportingConversation)
    assert target.exists() and "这一条不能丢" in target.read_text(encoding="utf-8")
    assert not dialog.property("opened")
    assert not warnings


def test_conversation_export_suggests_unused_name_beside_project(ui):
    editor, window, find, warnings, tmp_path = ui
    editor._message("user", "一条对话")
    first = Path(editor.suggestConversationPath())
    assert first.parent == tmp_path and first.suffix == ".md"
    first.write_text("已有记录", encoding="utf-8")
    second = Path(editor.suggestConversationPath())
    assert second != first and not second.exists()
    projects = tmp_path / "projects"
    projects.mkdir()
    editor.saveProject(str(projects / "edit.iphoto"))
    _click(window, find("exportConversationButton"))
    dialog = window.findChild(QObject, "conversationExportDialog")
    suggested = Path(dialog.property("filePath"))
    assert suggested.parent == projects and suggested.name == "edit-对话.md"
    browse = next(child for child in dialog.findChildren(QObject)
                  if child.property("title") == "选择对话记录位置")
    assert Path(browse.property("currentFolder").toLocalFile()) == projects
    assert not warnings


def test_conversation_export_keeps_ui_responsive_and_uses_submit_snapshot(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    editor._message("user", "提交前的对话")
    entered, release = Event(), Event()
    original = conversation._write_conversation

    def blocked_write(path, snapshot):
        entered.set()
        if not release.wait(8):
            raise TimeoutError("test writer stayed blocked")
        return original(path, snapshot)

    monkeypatch.setattr(conversation, "_write_conversation", blocked_write)
    _click(window, find("exportConversationButton"))
    dialog = window.findChild(QObject, "conversationExportDialog")
    target = tmp_path / "snapshot.md"
    dialog.setProperty("filePath", str(target))
    try:
        started = monotonic()
        _click(window, find("conversationExportConfirmButton"))
        assert monotonic() - started < .5
        wait_for(entered.is_set)
        assert editor.exportingConversation and not dialog.property("opened")
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(monotonic()))
        timer.start()
        QTest.qWait(100)
        timer.stop()
        assert len(ticks) >= 3
        editor._message("user", "提交后的新对话")
        assert not editor.prepareClose()
        assert "正在导出对话" in editor.status
    finally:
        release.set()
    wait_for(lambda: target.exists() and not editor.exportingConversation)
    exported = target.read_text(encoding="utf-8")
    assert "提交前的对话" in exported and "提交后的新对话" not in exported
    assert not warnings
