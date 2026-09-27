"""Incoming chat messages do not interrupt someone reading older entries."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.workspace import ROOT
from test_ai import wait_for
from test_import_export import ui


def _click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def test_new_message_preserves_history_scroll_until_user_jumps_to_end(ui):
    editor, window, find, warnings, tmp_path = ui
    chat = find("conversationList")
    for number in range(18):
        editor._message("user", f"第 {number} 条历史消息，继续阅读。")
    wait_for(lambda: chat.property("count") == 18)
    QTest.qWait(100)
    chat.setProperty("contentY", 0)
    QTest.qWait(50)
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
    chat.setProperty("contentY", 0)
    QTest.qWait(50)
    assert not chat.property("atYEnd")
    editor._message("assistant", "手动滚动测试")
    wait_for(lambda: chat.property("count") == 21)
    assert unread.property("visible")
    chat.setProperty("contentY", chat.property("contentHeight") - chat.property("height"))
    wait_for(lambda: chat.property("atYEnd"))
    assert not unread.property("visible")
    assert not warnings
