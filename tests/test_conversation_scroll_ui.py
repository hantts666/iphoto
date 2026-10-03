"""Incoming chat messages do not interrupt someone reading older entries."""

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest

from iphoto.workspace import ROOT
from test_ai import completion, configure, mock_api, wait_for
from test_editor import settled
from test_import_export import ui


def _click(window, item):
    assert item.isVisible() and item.property("enabled"), item.objectName()
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def _wheel(window, item, direction):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    for _ in range(30):
        event = QWheelEvent(point, window.mapToGlobal(point), QPoint(),
                            QPoint(0, 120 * direction), Qt.NoButton,
                            Qt.NoModifier, Qt.NoScrollPhase, False)
        QGuiApplication.sendEvent(window, event)
        QTest.qWait(20)
    QTest.qWait(200)


def test_new_message_preserves_history_scroll_until_user_jumps_to_end(ui):
    editor, window, find, warnings, tmp_path = ui
    chat = find("conversationList")
    for number in range(18):
        editor._message("user", f"第 {number} 条历史消息，继续阅读。")
    wait_for(lambda: chat.property("count") == 18)
    QTest.qWait(100)
    _wheel(window, chat, 1)
    assert not chat.property("atYEnd")
    before = chat.property("contentY")
    editor._message("assistant", "刚收到的新消息")
    wait_for(lambda: chat.property("count") == 19)
    QTest.qWait(100)
    assert abs(chat.property("contentY") - before) < 5
    unread = find("conversationNewMessageButton")
    assert unread.property("visible")
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-conversation-unread.png"))
    _click(window, unread)
    wait_for(lambda: chat.property("atYEnd"))
    assert not unread.property("visible")
    editor._message("assistant", "继续跟随末尾")
    wait_for(lambda: chat.property("count") == 20)
    QTest.qWait(50)
    assert chat.property("atYEnd") and not unread.property("visible")
    _wheel(window, chat, 1)
    assert not chat.property("atYEnd")
    editor._message("assistant", "手动滚动测试")
    wait_for(lambda: chat.property("count") == 21)
    wait_for(lambda: unread.property("visible"))
    _wheel(window, chat, -1)
    wait_for(lambda: chat.property("atYEnd"))
    assert not unread.property("visible")
    assert not warnings


def test_repeated_ai_requests_keep_latest_reply_in_view(ui):
    editor, window, find, warnings, _ = ui
    chat = find("conversationList")
    # Real projects have differently sized rows, including long analysis results.
    for number in range(7):
        editor._message("user", "人物轻磨皮，背景保持清晰。")
        editor._message("assistant", ("已建立局部调整层，可继续调整磨皮和曝光。\n" * (number % 4 + 1)), state="applied")
    QTest.qWait(200)
    reply = completion(summary="已完成本次调整。\n保留原来的曝光和肤色。\n可以继续输入下一步修图要求。")
    with mock_api(reply, delay=.15) as (url, _):
        configure(editor.ai, url)
        for index, prompt in enumerate(("整体提亮一点", "整体再暖一点", "颜色再自然一点")):
            if index == 1:
                _wheel(window, chat, 1)
                assert not chat.property("atYEnd")
                window.resize(1080, 700)
                QTest.qWait(100)
                _click(window, find("chatToggleButton"))
                QTest.qWait(100)
            find("descriptionInput").setProperty("text", prompt)
            before_count = editor.conversationCount
            _click(window, find("applyDescriptionButton"))
            wait_for(lambda: editor.conversationCount > before_count)
            wait_for(lambda: not editor.ai.busy and settled(editor))
            QTest.qWait(150)
            assert chat.property("atYEnd"), (index, chat.property("followEnd"), chat.property("contentY"), chat.property("originY"), chat.property("contentHeight"), editor.conversation[-1]["text"])
            assert not find("conversationNewMessageButton").property("visible")
            assert window.grabWindow().save(str(ROOT / f"artifacts/ux-conversation-follow-{index}.png"))
            editor.undo()
            wait_for(lambda: settled(editor))
            editor.redo()
            wait_for(lambda: settled(editor))
    assert not warnings
