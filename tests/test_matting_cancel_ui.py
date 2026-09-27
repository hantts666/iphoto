"""A running full-resolution matte can be dismissed from the real window."""

from copy import deepcopy

from PIL import Image
from PySide6.QtCore import Qt

from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def test_escape_cancels_matte_then_selection_and_open_work(canvas, tmp_path):  # noqa: F811
    ui = canvas
    ui.e.drawDraft("rect", "replace", [[.25, .15], [.75, .85]], .025)
    wait_for(lambda: ui.e.hasSelectionDraft and settled(ui.e))
    before = deepcopy(ui.e._candidate)
    ui.e.refineMatte(8)
    wait_for(lambda: ui.e.matteBusy and ui.e.selection.taskKind == "matte")
    ui.key(Qt.Key_Escape)
    assert not ui.e.matteBusy and not ui.e.busy and ui.e._candidate == before
    ui.e.drawDraft("rect", "replace", [[.1, .2], [.35, .6]], .025)
    wait_for(lambda: settled(ui.e) and ui.e._candidate != before)
    other = tmp_path / "after-cancel.png"
    Image.new("RGB", (480, 320), (150, 60, 90)).save(other)
    ui.e.openImage(str(other))
    wait_for(lambda: ui.e.imageName == other.name and settled(ui.e))
    assert ui.e._candidate is None and not ui.e.matteBusy


def test_cancel_then_immediate_retry_completes_new_matte(canvas):  # noqa: F811
    ui = canvas
    ui.e.drawDraft("rect", "replace", [[.25, .15], [.75, .85]], .025)
    wait_for(lambda: ui.e.hasSelectionDraft and settled(ui.e))
    ui.e.refineMatte(8)
    wait_for(lambda: ui.e._matte_active is not None)
    ui.e.cancelMatte()
    ui.e.refineMatte(8)
    wait_for(lambda: ui.e._matte_active is not None and ui.e.matteBusy)
    wait_for(lambda: not ui.e.matteBusy
             and ui.e._candidate.get("bitmap", {}).get("sampling") == "alpha",
             seconds=30)
    assert ui.e._candidate["bitmap"]["width"] == 2400
