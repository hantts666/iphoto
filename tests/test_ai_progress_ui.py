"""An in-flight AI request should explain its observable wait in the chat pane."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.workspace import ROOT
from test_ai import configure, mock_api, wait_for
from test_import_export import ui


def _click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def test_ai_wait_is_visible_updates_and_can_be_cancelled(ui):
    editor, window, find, warnings, tmp_path = ui
    with mock_api(delay=4) as (url, requests):
        configure(editor.ai, url)
        find("chatModeBox").setProperty("currentIndex", 1)
        find("descriptionInput").setProperty("text", "请分析暗部")
        _click(window, find("applyDescriptionButton"))
        wait_for(lambda: editor.ai.busy and bool(requests))
        progress = find("aiRequestProgress")
        progress_text = find("aiRequestProgressText")
        assert progress.property("visible")
        initial = progress_text.property("text")
        assert "等待" in initial or "接收" in initial
        assert find("workspaceStatusText").property("text") == initial
        wait_for(lambda: progress_text.property("text") != initial, seconds=2.5)
        assert find("workspaceStatusText").property("text") == progress_text.property("text")
        assert window.grabWindow().save(str(ROOT / "artifacts/ux-ai-request-progress.png"))
        _click(window, find("cancelAiRequest"))
        wait_for(lambda: not editor.ai.busy)
        assert not progress.property("visible")
        assert "取消" in editor.status
    assert not warnings


def test_retry_wait_stays_visible_and_cancel_stops_retry(ui):
    editor, window, find, warnings, tmp_path = ui
    with mock_api(status=503) as (url, requests):
        configure(editor.ai, url)
        find("chatModeBox").setProperty("currentIndex", 1)
        find("descriptionInput").setProperty("text", "给我修图建议")
        _click(window, find("applyDescriptionButton"))
        wait_for(lambda: editor.ai._retry_context is not None)
        assert editor.ai.busy
        assert find("aiRequestProgress").property("visible")
        assert "重试" in find("aiRequestProgressText").property("text")
        count = len(requests)
        _click(window, find("cancelAiRequest"))
        wait_for(lambda: not editor.ai.busy)
        QTest.qWait(1800)
        assert len(requests) == count
        assert not find("aiRequestProgress").property("visible")
        assert "取消" in editor.status
    assert not warnings


def test_retry_starts_next_attempt_without_dropping_busy_state(ui):
    editor, window, find, warnings, tmp_path = ui
    with mock_api(status=503) as (url, requests):
        configure(editor.ai, url)
        find("chatModeBox").setProperty("currentIndex", 1)
        find("descriptionInput").setProperty("text", "分析色彩")
        _click(window, find("applyDescriptionButton"))
        wait_for(lambda: len(requests) >= 2, seconds=5)
        assert editor.ai.busy
        assert find("aiRequestProgress").property("visible")
        _click(window, find("cancelAiRequest"))
        wait_for(lambda: not editor.ai.busy)
    assert not warnings
