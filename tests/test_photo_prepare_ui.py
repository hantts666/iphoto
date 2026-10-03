"""Photo preparation lifetime against real QML and source-resolution frames.

Only the long-running encoder is held; source loading, tile rendering, input,
task routing and cancellation are real. Actual local-model QA is separate.
"""

from collections import deque
from copy import deepcopy
import json

import pytest
from PIL import Image
from PySide6.QtCore import QProcess, Qt
from PySide6.QtTest import QTest

from iphoto.controllers import detail_tiles, pixel_selections
from iphoto.segmentation.classical import bitmap_mask
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_selection_controller import editor  # noqa: F401
from test_v14 import catalog


class HeldWarm:
    current = QProcess.NotRunning
    killed = False

    def state(self):
        return self.current

    def start(self, *_):
        self.current = QProcess.Running

    def kill(self):
        self.killed = True
        self.current = QProcess.NotRunning


def document_state(e):
    return deepcopy((e._layers, e._candidate, e._history, e._cursor,
                     e._generation, e._draft_history, e._pixel_points))


@pytest.fixture
def preparing(canvas, monkeypatch):  # noqa: F811
    ui = canvas
    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("detailReady"), seconds=20)
    real = ui.e._warm_process
    held = HeldWarm()
    ui.e._warm_process = held
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    try:
        yield ui, held
    finally:
        held.kill()
        ui.e._warm_process = real
        ui.e._warm_sha = ""
        ui.e._warm_abandoned = True
        ui.e.changed.emit()


@pytest.mark.parametrize("cancel", ["button", "escape"])
def test_preparation_keeps_native_frame_and_cancel_preserves_photo(preparing, cancel):
    ui, held = preparing
    e = ui.e
    state, url, box = document_state(e), e.detailUrl, e.detailRect
    ui.click("tool_smart")
    wait_for(lambda: e._detail_process.state() == QProcess.NotRunning)
    assert e.photoPreparing and not e.busy
    assert e.selection.taskKind == "warm" and e.selection.taskCancellable
    assert ui.find("aiRequestProgress").isVisible()
    assert "可继续浏览" in ui.find("aiRequestProgressText").property("text")
    assert ui.find("cancelAiRequest").property("text") == "取消照片准备"
    assert ui.w.property("detailReady") and ui.find("detailImage").isVisible()
    assert (e.detailUrl, e.detailRect, document_state(e)) == (url, box, state)
    assert not e.selection.showMask
    if cancel == "button":
        ui.click("cancelAiRequest")
    else:
        ui.find("photoCanvas").forceActiveFocus()
        ui.key(Qt.Key_Escape)
    assert held.killed and not e.photoPreparing
    assert e.selection.taskKind == "none" and not ui.find("aiRequestProgress").isVisible()
    assert "已取消照片准备" in e.status
    assert (e.detailUrl, e.detailRect, document_state(e)) == (url, box, state)
    assert ui.w.property("detailReady")
    # A successful exit delivered after explicit cancellation must not mark
    # the photo ready or silently retry cancelled background work.
    e._warm_finished(0, QProcess.NormalExit)
    QTest.qWait(220)
    assert e._warm_ready_sha != e._sha and not e._pixel_queue
    assert (e.detailUrl, document_state(e)) == (url, state)


def test_pan_during_preparation_resumes_latest_view_after_encoder_finishes(preparing):
    ui, held = preparing
    e = ui.e
    state, url, box = document_state(e), e.detailUrl, e.detailRect
    ui.click("tool_smart")
    wait_for(lambda: e._detail_process.state() == QProcess.NotRunning)
    e.viewport.pan(-850, 0)
    QTest.qWait(220)
    assert e.photoPreparing and not ui.w.property("detailReady")
    assert "照片正在准备，完成后加载当前区域细节" in ui.find("detailStatusCaption").property("text")
    assert e.detailUrl == url and e.detailRect == box
    held.current = QProcess.NotRunning
    e._warm_finished(0, QProcess.NormalExit)
    wait_for(lambda: ui.w.property("detailReady") and e.detailUrl != url, seconds=20)
    assert e.detailRect != box and e._warm_ready_sha == e._sha
    assert e.selection.taskKind == "none" and document_state(e) == state


@pytest.mark.parametrize("tool", ["smart", "object"])
def test_first_target_tool_does_not_tint_full_layer_and_respects_explicit_mask_choice(canvas, monkeypatch, tool):  # noqa: F811
    ui = canvas
    monkeypatch.setattr(pixel_selections, "warm", lambda _: None)
    ui.e.selection.toggleShowMask()
    assert ui.e.selection.showMask and not ui.e.hasSelectionDraft
    ui.click("tool_" + tool)
    assert not ui.e.selection.showMask and not ui.e.hasSelectionDraft
    ui.key(Qt.Key_Q)
    assert ui.e.selection.showMask
    ui.click("tool_" + tool)
    assert ui.e.selection.showMask
    # A real manual range must become visible and remain visible when the
    # user switches acquisition tools to refine it.
    ui.e.drawDraft("rect", "replace", [[.25, .2], [.7, .8]], .025)
    wait_for(lambda: ui.e.hasSelectionDraft and settled(ui.e))
    assert ui.e.selection.showMask
    ui.click("tool_" + ("object" if tool == "smart" else "smart"))
    assert ui.e.selection.showMask and ui.e.hasSelectionDraft


def test_photo_preparation_identity_and_foreground_priority(editor):  # noqa: F811
    e = editor
    real = e._warm_process
    held = HeldWarm()
    held.current = QProcess.Starting
    e._warm_process, e._warm_sha = held, e._sha
    e._warm_abandoned = False
    try:
        assert e.photoPreparing and e.selection.taskKind == "warm"
        e._warm_sha = "other-photo"
        assert not e.photoPreparing and e.selection.taskKind == "none"
        e._warm_sha, e._warm_abandoned = e._sha, True
        assert not e.photoPreparing
        e._warm_abandoned = False
        e._ai._retry_context = {"attempt": 0, "mode": "advice"}
        assert e.selection.taskKind == "ai" and "重试" in e.selection.taskText
        e._ai._retry_context = None
        e._pixel_queue.append({"op": "segment"})
        e._status = "等待中的点选"
        assert e.selection.taskKind == "pixel" and e.selection.taskText == e.status
        e._matte_pending = {"op": "matte"}
        assert e.selection.taskKind == "matte"
        e._matte_pending = None
        e._pixel_queue.clear()
        e._active = {"op": "selection"}
        assert e.selection.taskKind == "refine"
        e._active = None
        assert e.selection.taskKind == "warm"
    finally:
        e._warm_process, e._warm_sha = real, ""
        e._ai._retry_context = e._matte_pending = e._active = None
        e._pixel_queue.clear()


def test_cancel_preparation_drops_only_pending_catalog_results_and_cannot_restart(editor):  # noqa: F811
    e = editor
    e._scene.set(catalog())
    precise = bitmap_mask(Image.new("L", (24, 16), 255), "ready protocol fixture")
    e._scene.set_precise("object-1", precise, {})
    ready = deepcopy(e._scene.precise)
    e._scene.mark_pixel_status(["object-2"], "pending")
    e._pixel_queue = deque([{
        "op": "segment", "priority": "low", "jobs": [{"id": "object-2"}],
        "context": {"purpose": "precache", "source_sha": e._sha,
                    "scene_revision": e._scene.revision},
    }])
    real = e._warm_process
    held = HeldWarm()
    held.current = QProcess.Running
    e._warm_process, e._warm_sha = held, e._sha
    e._warm_abandoned = False
    state = document_state(e)
    try:
        e.selection.cancelTask()
        e._warm_finished(0, QProcess.NormalExit)
        QTest.qWait(220)
        assert held.killed and not e._pixel_queue and e._pixel_active is None
        assert e._scene.precise == ready
        assert next(row for row in e._scene.rows() if row["id"] == "object-2")["pixelStatus"] == "unavailable"
        assert e._warm_ready_sha != e._sha and document_state(e) == state
    finally:
        e._warm_process, e._warm_sha = real, ""


def test_released_detail_work_cannot_publish_late_reply_and_stop_clears_frame(editor):  # noqa: F811
    e = editor
    class PendingDetail:
        current = QProcess.Running
        def state(self):
            return self.current
        def kill(self):
            self.current = QProcess.NotRunning
        def readAllStandardOutput(self):
            return (json.dumps({"id": 777, "ok": True, "result": {
                "path": "late.png", "box": [0, 0, 100, 100],
            }}) + "\n").encode()
    real = e._detail_process
    e._detail_process = PendingDetail()
    e._detail_url, e._detail_mask_url = "retained-color", "retained-mask"
    e._detail_box = (10, 20, 100, 120)
    e._detail_active = e._detail_pending = {"id": 777}
    e._detail_buffer = b"partial abandoned response"
    frame = (e.detailUrl, e.detailMaskUrl, e.detailRect, e._detail_version,
             e._detail_generation, e._detail_frame_version)
    try:
        detail_tiles.release(e)
        assert e._detail_active is None and e._detail_pending is None and not e._detail_buffer
        detail_tiles.read(e)
        detail_tiles.finished(e)
        assert (e.detailUrl, e.detailMaskUrl, e.detailRect, e._detail_version,
                e._detail_generation, e._detail_frame_version) == frame
        assert e._detail_failed_generation == -1
        detail_tiles.stop(e)
        assert not e.detailUrl and not e.detailMaskUrl and e._detail_box is None
        assert e._detail_version == frame[3] + 1
    finally:
        e._detail_process = real
