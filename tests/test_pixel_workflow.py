"""UI event routing with an explicit fake segmenter; no accuracy claims here."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
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

    def request(op, **kwargs):
        if op != "segment":
            return original(op, **kwargs)
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
