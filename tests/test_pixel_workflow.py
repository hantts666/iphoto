"""UI event routing with an explicit fake segmenter; no accuracy claims here."""

from copy import deepcopy
from collections import deque
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest

from iphoto.controllers import pixel_selections, worker_bridge
from iphoto.document import empty_mask
from iphoto.segmentation.classical import bitmap_mask
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_pixel_selection import ring


def test_s_and_alt_click_coordinates_and_space_pan_keep_selection(canvas, monkeypatch):  # noqa: F811
    ui = canvas
    requests = []
    original = ui.e._request
    _, alpha, _ = ring()
    mask = bitmap_mask(alpha, "routing fixture")
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    monkeypatch.setattr(pixel_selections, "warm", lambda _: None)

    def request(op, **kwargs):
        if op != "segment":
            return original(op, **kwargs)
        if not kwargs.get("jobs"):
            return  # embedding warm-up request, not a segmentation
        requests.append(deepcopy(kwargs))
        pixel_selections.complete(
            ui.e,
            {"items": [{"id": "target", "mask": mask, "quality": {}}]},
            kwargs["context"],
        )

    monkeypatch.setattr(ui.e, "_request", request)
    before = deepcopy(ui.e._layers)
    ui.click("photoCanvas")
    ui.key(Qt.Key_S)
    assert ui.w.property("selectionTool") == "smart"
    ui.click("selectionMouse", 0.25, 0.5)
    wait_for(lambda: settled(ui.e))
    assert ui.e.pixelPoints[0] == pytest.approx([0.25, 0.5, 1], abs=0.004)
    ui.click("selectionMouse", 0.5, 0.5, Qt.AltModifier)
    wait_for(lambda: settled(ui.e))
    assert ui.e.pixelPoints[-1] == pytest.approx([0.5, 0.5, 0], abs=0.004)
    assert len(requests) == 2 and ui.e._layers == before
    assert requests[1]["jobs"][0]["hint"]["bitmap"]  # prior keeps the target stable
    QTest.keyPress(ui.w, Qt.Key_Space)
    ui.drag(ui.point("photoCanvas", 0.3, 0.3), ui.point("photoCanvas", 0.45, 0.4))
    QTest.keyRelease(ui.w, Qt.Key_Space)
    assert len(requests) == 2
    ui.click("undoPixelPointButton")
    wait_for(lambda: settled(ui.e))
    assert len(ui.e.pixelPoints) == 1
    ui.e.discardSelection()
    wait_for(lambda: settled(ui.e))
    assert not ui.e.pixelPoints and ui.e._pixel_hint is None


def test_queued_pixel_click_is_visible_and_cancellable(canvas, monkeypatch):  # noqa: F811
    ui = canvas
    editor = ui.e
    editor.selection.pickLayer(editor.activeLayerId)
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    monkeypatch.setattr(pixel_selections, "warm", lambda _: None)
    ui.click("tool_smart")
    assert editor.selection.pickedLayerId == ""

    # Simulate the separate encoder process. The real QML click must wait in
    # the segment queue while ordinary render work stays available.
    class WarmProcess:
        current = QProcess.Running

        def state(self):
            return self.current

        def kill(self):
            self.current = QProcess.NotRunning

    real_warm_process = editor._warm_process
    editor._warm_process = WarmProcess()
    editor._warm_sha = editor._sha
    editor.changed.emit()
    before = deepcopy(editor._candidate)
    try:
        ui.click("selectionMouse", 0.3, 0.5)
        assert editor.busy and editor.selection.taskKind == "pixel"
        assert editor.selection.queuedPixelTask
        assert editor.status.startswith("照片首次编码中；点选已排队")
        assert any(
            request["op"] == "segment" and request.get("priority") != "low"
            for request in editor._pixel_queue
        )
        button = ui.find("cancelAiRequest")
        assert button.property("visible") and button.property("text") == "取消等待中的点选"
        editor.selection.cancelTask()
        assert not editor.busy and editor.selection.taskKind == "none"
        assert not any(request["op"] == "segment" for request in editor._pixel_queue)
        assert editor._candidate == before
    finally:
        editor._warm_process = real_warm_process
        editor._warm_sha = ""
        editor.changed.emit()


def test_escape_cancels_active_pixel_task_from_canvas(canvas):  # noqa: F811
    ui = canvas
    editor = ui.e
    before = deepcopy(editor._candidate)
    editor._pixel_active = {
        "id": 999, "op": "segment", "generation": editor._generation,
        "context": {"purpose": "points", "points": [[.2, .4, 1]]},
    }
    editor.changed.emit()
    assert editor.selection.taskKind == "pixel"
    dialog = ui.w.findChild(QObject, "exportDialog")
    assert dialog is not None
    dialog.open()
    QTest.qWait(80)
    assert ui.w.property("modalActive")
    ui.key(Qt.Key_Escape)
    assert editor._pixel_active is not None, "Escape in a dialog must not cancel the pixel task"
    dialog.close()
    QTest.qWait(40)
    ui.find("photoCanvas").forceActiveFocus()
    ui.key(Qt.Key_Escape)
    assert editor._pixel_active is None and not editor.busy
    assert editor._candidate == before
    assert "已停止" in editor.status


def test_foreground_pixel_preempts_background_without_using_main_queue(canvas, monkeypatch):  # noqa: F811
    editor = canvas.e
    stops = []
    monkeypatch.setattr(editor, "_pump_pixel", lambda: None)

    def stop():
        stops.append(editor._pixel_active["id"])
        editor._pixel_active = None

    monkeypatch.setattr(editor, "_stop_pixel", stop)
    original_main_queue = list(editor._queue)
    editor._request("segment", jobs=[{"id": "background"}], priority="low",
                    context={"purpose": "precache"})
    editor._pixel_active = editor._pixel_queue.popleft()
    assert not editor.busy
    editor._request("segment", jobs=[{"id": "click"}],
                    context={"purpose": "points", "points": [[.2, .4, 1]]})
    assert len(stops) == 1
    assert [request["jobs"][0]["id"] for request in editor._pixel_queue] == ["click", "background"]
    assert list(editor._queue) == original_main_queue
    assert editor.busy and editor.selection.taskKind == "pixel"
    editor._pixel_queue.clear()


def test_pixel_process_exit_releases_pending_background_rows():
    failed = []
    notes = []
    owner = SimpleNamespace(
        _pixel_aborting=False,
        _pixel_active={"id": 1, "op": "segment", "priority": "low",
                       "context": {"purpose": "precache", "source_sha": "photo", "scene_revision": 2},
                       "jobs": [{"id": "object-1"}]},
        _pixel_queue=deque([{"id": 2, "op": "segment", "priority": "low",
                             "context": {"purpose": "precache", "source_sha": "photo", "scene_revision": 2},
                             "jobs": [{"id": "object-2"}]}]),
        _pixel_buffer=b"partial",
        _closing=False,
        _sha="photo",
        _scene=SimpleNamespace(revision=2, mark_pixel_status=lambda ids, state: failed.append((ids, state))),
        changed=SimpleNamespace(emit=lambda: None),
        _notify=lambda *args: notes.append(args),
        _pump_pixel=lambda: None,
    )
    worker_bridge._pixel_finished(owner, 1, QProcess.CrashExit)
    assert failed == [(["object-1", "object-2"], "unavailable")]
    assert not owner._pixel_queue and owner._pixel_active is None
    assert owner._pixel_buffer == b"" and notes


@pytest.mark.parametrize("outcome", ["cancelled", "stale", "error"])
def test_worker_result_rejected_without_overwriting_mask(outcome):
    old = empty_mask(True)
    notes = []
    response = {
        "id": 9,
        "op": "segment",
        "generation": 10,
        "ok": outcome != "error",
        "error": "test failure",
        "result": {"items": []},
    }
    process = SimpleNamespace(
        readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()
    )
    owner = SimpleNamespace(
        process=process,
        _buffer=b"",
        _active={"id": 9, "op": "segment", "cancelled": outcome == "cancelled"},
        _generation=11 if outcome == "stale" else 10,
        _candidate=old,
        _pending_project=None,
        changed=SimpleNamespace(emit=lambda: None),
        _pump=lambda: None,
        _notify=lambda *args: notes.append(args),
        _message=lambda *args, **kwargs: notes.append(args),
        _status="working",
    )
    worker_bridge._read(owner)
    assert owner._candidate is old and owner._active is None and notes
    assert "保留" in owner._status


def test_background_masks_yield_to_preview_and_click():
    """After one object, the next paint and click run before queued objects."""
    from PySide6.QtCore import QProcess

    written = []
    process = SimpleNamespace(
        state=lambda: QProcess.Running,
        write=lambda value: written.append(json.loads(value)),
    )
    low = lambda lid: {
        "id": lid,
        "op": "segment",
        "priority": "low",
        "context": {"purpose": "precache", "source_sha": "photo", "scene_revision": 2},
        "jobs": [{"id": lid}],
    }
    owner = SimpleNamespace(
        process=process,
        _closing=False,
        _active=None,
        _queue=deque([low(1), low(2)]),
        _pending_render={"id": 3, "op": "render"},
        _sha="photo",
        _scene=SimpleNamespace(revision=2, precise={}),
        changed=SimpleNamespace(emit=lambda: None),
    )
    worker_bridge._pump(owner)
    assert written[-1]["op"] == "render"
    owner._active = None
    worker_bridge._pump(owner)
    assert written[-1]["jobs"][0]["id"] == 1
    owner._active = None
    owner._queue.append({"id": 4, "op": "segment", "jobs": [{"id": "click"}]})
    worker_bridge._pump(owner)
    assert written[-1]["jobs"][0]["id"] == "click"
    owner._active = None
    worker_bridge._pump(owner)
    assert written[-1]["jobs"][0]["id"] == 2


def test_separate_preheat_does_not_hold_up_render():
    written = []
    warm_state = {"value": QProcess.Running}
    owner = SimpleNamespace(
        process=SimpleNamespace(
            state=lambda: QProcess.Running,
            write=lambda value: written.append(json.loads(value)),
        ),
        _warm_process=SimpleNamespace(state=lambda: warm_state["value"]),
        _closing=False,
        _active=None,
        _queue=deque([{"id": 2, "op": "segment", "jobs": [{"id": "click"}]}]),
        _pending_render={"id": 3, "op": "render"},
        _sha="photo",
        _scene=SimpleNamespace(revision=0, precise={}),
        changed=SimpleNamespace(emit=lambda: None),
    )
    worker_bridge._pump(owner)
    assert written[-1]["op"] == "render"
    assert owner._queue[0]["op"] == "segment"
    owner._active = None
    warm_state["value"] = QProcess.NotRunning
    worker_bridge._pump(owner)
    assert written[-1]["op"] == "segment"


def test_interactive_object_result_skips_queued_duplicate_background_mask():
    from PySide6.QtCore import QProcess

    written = []
    owner = SimpleNamespace(
        process=SimpleNamespace(state=lambda: QProcess.Running,
                                write=lambda value: written.append(json.loads(value))),
        _closing=False,
        _active=None,
        _queue=deque([{"id": 10, "op": "segment", "priority": "low",
                       "context": {"purpose": "precache", "source_sha": "photo", "scene_revision": 2},
                       "jobs": [{"id": "object-1"}]}]),
        _pending_render=None,
        _sha="photo",
        _scene=SimpleNamespace(revision=2, precise={"object-1": {"mask": {}}}),
        changed=SimpleNamespace(emit=lambda: None),
    )
    worker_bridge._pump(owner)
    assert not written and not owner._queue


def test_background_mask_survives_edit_generation_but_not_catalog_change(monkeypatch):
    completed = []
    monkeypatch.setattr(pixel_selections, "complete", lambda *_args: completed.append(True))
    response = {"id": 7, "op": "segment", "generation": 3, "ok": True, "result": {"items": []}}
    owner = SimpleNamespace(
        process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()),
        _buffer=b"",
        _active={"id": 7, "op": "segment", "context": {
            "purpose": "precache", "source_sha": "photo", "scene_revision": 2,
        }},
        _generation=4,
        _sha="photo",
        _scene=SimpleNamespace(revision=2),
        changed=SimpleNamespace(emit=lambda: None),
        _pump=lambda: None,
        _notify=lambda *_args: None,
        _status="",
    )
    worker_bridge._read(owner)
    assert completed == [True]
    owner._buffer = b""
    owner._active = {"id": 7, "op": "segment", "context": {
        "purpose": "precache", "source_sha": "photo", "scene_revision": 2,
    }}
    owner._scene.revision = 3
    worker_bridge._read(owner)
    assert completed == [True]


def test_background_model_failure_stops_same_batch_without_disrupting_ai_request():
    response = {"id": 8, "op": "segment", "generation": 3, "ok": False,
                "error": "model unavailable"}
    notes = []
    failed = []
    owner = SimpleNamespace(
        process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()),
        _buffer=b"",
        _active={"id": 8, "op": "segment", "priority": "low", "context": {
            "purpose": "precache", "source_sha": "photo", "scene_revision": 2,
        }, "jobs": [{"id": "object-1"}]},
        _queue=deque([{"id": 9, "op": "segment", "priority": "low", "context": {
            "purpose": "precache", "source_sha": "photo", "scene_revision": 2,
        }, "jobs": [{"id": "object-2"}]}]),
        _generation=3,
        _sha="photo",
        _scene=SimpleNamespace(revision=2, mark_pixel_status=lambda ids, status: failed.append((ids, status))),
        _pending_request={"mode": "scene"},
        changed=SimpleNamespace(emit=lambda: None),
        _pump=lambda: None,
        _notify=lambda *args: notes.append(args),
        _status="",
    )
    worker_bridge._read(owner)
    assert not owner._queue
    assert owner._pending_request == {"mode": "scene"}
    assert notes and notes[0][-1] is not True
    assert failed == [(["object-1", "object-2"], "unavailable")]


def test_failed_point_click_keeps_prompts_additively():
    """A rejected click must not discard the prompt: points stack, info not error."""
    points = [[0.3, 0.4, 1]]
    response = {
        "id": 5,
        "op": "segment",
        "generation": 7,
        "ok": False,
        "error": "模型没有找到可靠目标，原选区保留；请框住目标或补充保留/排除点",
        "result": None,
    }
    process = SimpleNamespace(
        readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()
    )
    notes = []
    owner = SimpleNamespace(
        process=process,
        _buffer=b"",
        _active={
            "id": 5,
            "op": "segment",
            "context": {"purpose": "points", "points": points},
        },
        _generation=7,
        _candidate=None,
        _pixel_points=[],
        _status="working",
        _pending_project=None,
        changed=SimpleNamespace(emit=lambda: None),
        _pump=lambda: None,
        _notify=lambda *args, **kwargs: notes.append(args),
        _message=lambda *args, **kwargs: notes.append(args),
    )
    worker_bridge._read(owner)
    assert owner._pixel_points == points
    assert "提示点已保留" in owner._status
    assert notes and notes[-1][-1] is not True  # info toast, not error


def test_other_segment_errors_still_report_as_errors():
    response = {
        "id": 6,
        "op": "segment",
        "generation": 7,
        "ok": False,
        "error": "boom",
        "result": None,
    }
    process = SimpleNamespace(
        readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()
    )
    notes = []
    owner = SimpleNamespace(
        process=process,
        _buffer=b"",
        _active={"id": 6, "op": "segment", "context": {"purpose": "points", "points": [[0.1, 0.1, 1]]}},
        _generation=7,
        _candidate=None,
        _pixel_points=[],
        _status="working",
        _pending_project=None,
        changed=SimpleNamespace(emit=lambda: None),
        _pump=lambda: None,
        _notify=lambda *args, **kwargs: notes.append(args),
        _message=lambda *args, **kwargs: notes.append(args),
    )
    worker_bridge._read(owner)
    assert owner._pixel_points == []
    assert notes[-1] == ("boom", True)


def test_stray_worker_stdout_lines_are_ignored():
    """ONNX-runtime style '[W:onnxruntime:...]' logs must not raise toast errors."""
    valid = {
        "id": 7,
        "op": "segment",
        "generation": 7,
        "ok": True,
        "result": {"items": []},
    }
    payload = b"[W:onnxruntime:Default, session.cc:123] noise\n  indented noise\n" + (
        json.dumps(valid) + "\n"
    ).encode()
    process = SimpleNamespace(readAllStandardOutput=lambda: payload)
    notes = []
    owner = SimpleNamespace(
        process=process,
        _buffer=b"",
        _active={"id": 7, "op": "segment", "context": {"purpose": "warm"}},
        _generation=7,
        _candidate=None,
        _pixel_points=[],
        _status="working",
        _pending_project=None,
        changed=SimpleNamespace(emit=lambda: None),
        _pump=lambda: None,
        _notify=lambda *args, **kwargs: notes.append(args),
        _message=lambda *args, **kwargs: notes.append(args),
    )
    worker_bridge._read(owner)
    assert owner._active is None  # the valid response was consumed
    assert not any("Expecting value" in str(n) for n in notes), notes
