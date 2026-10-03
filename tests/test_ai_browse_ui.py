"""Cloud waiting allows native browsing while preserving edit/task protection.

Real complete QML and image workers; HTTP responses and encoder completion
are explicitly controlled to test timing, not model/segmentation quality.
"""

from copy import deepcopy

from PIL import Image
from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtTest import QTest

from iphoto.controllers import detail_tiles, pixel_selections
from iphoto.document import render_layers
from iphoto.engine import Recipe, load_source
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_photo_prepare_ui import HeldWarm, document_state
from test_v14 import scene_response


def test_first_scene_wait_allows_native_pan_after_photo_preparation_and_cancel_keeps_edits(canvas, monkeypatch):  # noqa: F811
    ui, e = canvas, canvas.e
    real, held = e._warm_process, HeldWarm()
    e._warm_process = held
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    state = document_state(e)
    try:
        with mock_api(scene_response(), delay=8) as (url, requests):
            configure(e.ai, url)
            ui.click("analyzeSceneButton")
            wait_for(lambda: e.ai.busy and bool(requests))
            assert e.photoPreparing and e.busy and not e.imageWorkBusy
            ui.click("actualSizeButton")
            QTest.qWait(220)
            assert not e.detailUrl and e._detail_process.state() == QProcess.NotRunning
            assert "照片正在准备" in ui.find("detailStatusCaption").property("text")
            held.current = QProcess.NotRunning
            e._warm_finished(0, QProcess.NormalExit)
            wait_for(lambda: ui.w.property("detailReady") and e.ai.busy, seconds=3)
            assert e.busy and not e.imageWorkBusy and e.selection.taskKind == "ai"
            assert "分析画面" in ui.find("aiRequestProgressText").property("text")
            assert not ui.find("tool_rect").property("enabled")
            # Viewing cannot accidentally relax document-edit protection.
            e.setParameter("exposure", .75)
            assert document_state(e) == state
            first = e.detailUrl
            ui.drag(ui.point("canvasSurface", .8, .5), ui.point("canvasSurface", .1, .5), Qt.MiddleButton)
            wait_for(lambda: e.detailUrl != first and ui.w.property("detailReady") and e.ai.busy, seconds=3)
            current = e.detailUrl
            ui.click("cancelAiRequest")
            wait_for(lambda: not e.ai.busy and e._pending_request is None)
            assert e.detailUrl == current and ui.w.property("detailReady")
            assert document_state(e) == state and e._scene.catalog is None
            assert "取消" in e.status and not ui.find("aiRequestProgress").isVisible()
    finally:
        e._warm_process, e._warm_sha = real, ""
        held.kill()
        e._warm_abandoned = True
        e.changed.emit()


def test_local_image_tasks_still_block_native_dispatch_during_cloud_wait(canvas):  # noqa: F811
    e = canvas.e
    # Controlled overlapping lifecycle states verify that excluding cloud
    # waiting cannot bypass export, pixel, matte or repair preparation guards.
    e._ai._retry_context = {"attempt": 0}
    try:
        blockers = [
            ("_export_request", {"op": "export"}),
            ("_matte_pending", {"op": "matte"}),
            ("_pixel_active", {"op": "segment"}),
            ("_active", {"op": "repair_crop"}),
            ("_pending_request", {"repair_grounding": {"preparing": True}}),
        ]
        for field, value in blockers:
            setattr(e, field, value)
            e.changed.emit()
            canvas.n.setZoom(1)
            detail_tiles.stop(e)
            e.requestDetail()
            assert e.ai.busy and e.busy and e.imageWorkBusy
            assert e._detail_pending is None and e._detail_active is None and not e.detailUrl
            setattr(e, field, None)
        assert e.ai.busy and e.busy and not e.imageWorkBusy
    finally:
        e._ai._retry_context = None
        for field, _ in blockers:
            setattr(e, field, None)
        e.changed.emit()


def test_ai_edit_after_browsing_delivers_current_pixels_and_one_undo(canvas):  # noqa: F811
    ui, e = canvas, canvas.e
    before = deepcopy(e._layers)
    cursor, generation = e._cursor, e._generation
    with mock_api(auto_response("adjust", [], Recipe(exposure=.4).to_dict()), delay=3) as (url, requests):
        configure(e.ai, url)
        ui.w.setProperty("chatOpen", True)
        QTest.qWait(60)
        ui.find("descriptionInput").setProperty("text", "提高当前层曝光")
        ui.click("applyDescriptionButton")
        wait_for(lambda: e.ai.busy and bool(requests))
        ui.click("actualSizeButton")
        wait_for(lambda: ui.w.property("detailReady") and e.ai.busy, seconds=2.5)
        viewed = e.detailUrl
        assert e._layers == before and e._generation == generation
        wait_for(lambda: not e.ai.busy and settled(e) and ui.w.property("detailReady"), seconds=12)
        assert e.detailUrl != viewed and e._detail_generation == e._generation
        assert e._cursor == cursor + 1 and len(e._layers) == len(before)
        assert e._layer()["recipe"]["exposure"] == .4
        source = load_source(e._path).image
        expected = render_layers(source, e._layers).crop(e._detail_box)
        with Image.open(QUrl(e.detailUrl).toLocalFile()) as actual:
            assert actual.tobytes() == expected.tobytes()
        ui.find("photoCanvas").forceActiveFocus()
        ui.key(Qt.Key_Z, Qt.ControlModifier)
        wait_for(lambda: settled(e) and ui.w.property("detailReady"))
        assert e._layers == before and e._cursor == cursor
        with Image.open(QUrl(e.detailUrl).toLocalFile()) as actual:
            assert actual.tobytes() == source.crop(e._detail_box).tobytes()
