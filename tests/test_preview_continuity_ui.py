"""Actual canvas pixels under controlled delayed/failed image loading.

Only frame delivery is held by the local HTTP fixture. The real QML view,
original comparison, document, photo worker, and input events remain in use.
"""

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Event, Thread

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from test_ai import wait_for
from test_editor import settled
from test_import_export import ui  # noqa: F401


@pytest.fixture
def frames():
    responses = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            item = responses[self.path]
            item["requested"].set()
            if not item["release"].wait(8):
                return
            body = item["body"]
            try:
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Photo changes cancel the previous image request.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def add(color):
        body = None
        if color is not None:
            buffer = BytesIO()
            Image.new("RGB", (300, 200), color).save(buffer, format="PNG")
            body = buffer.getvalue()
        path = f"/frame-{len(responses)}.png"
        item = {"body": body, "requested": Event(), "release": Event(),
                "url": f"http://127.0.0.1:{server.server_port}{path}"}
        responses[path] = item
        return item

    yield add
    for item in responses.values():
        item["release"].set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture(params=[(1440, 930), (1080, 700)])
def preview_ui(ui, request):  # noqa: F811
    editor, window, find, warnings, tmp_path = ui
    window.resize(*request.param)
    window.setProperty("chatOpen", False)
    wait_for(lambda: window.property("previewReady"))
    QTest.qWait(100)

    class View:
        e, w, errors, directory = editor, window, warnings, tmp_path

        @staticmethod
        def find(name):
            return find(name)

        def point(self, name, x=.5, y=.5):
            item = find(name)
            return item.mapToScene(QPointF(item.width()*x, item.height()*y)).toPoint()

        def color(self, x=.5):
            point = self.point("photoCanvas", x, .65)
            return window.grabWindow().pixelColor(point).getRgb()[:3]

        def start(self, frame):
            editor._preview = frame["url"]
            editor.changed.emit()
            wait_for(lambda: frame["requested"].is_set() and not window.property("previewReady"))

        def finish(self, frame, color):
            frame["release"].set()
            wait_for(lambda: window.property("previewReady") and self.color() == color)

        def state(self):
            return deepcopy((editor.layers, editor._cursor, editor._generation,
                             editor._candidate, editor._draft_history,
                             editor._draft_cursor, editor._history))

    view = View()
    yield view
    assert not warnings, warnings


def test_pending_frame_keeps_last_result_without_accepting_it_as_ready(preview_ui, frames):
    view = preview_ui
    before, state = view.color(), view.state()
    frame = frames((180, 70, 40))
    view.start(frame)
    assert view.color() == before
    assert not view.w.property("previewReady")
    assert view.state() == state
    view.finish(frame, (180, 70, 40))
    assert view.state() == state


def test_hold_original_is_immediate_and_does_not_restart_pending_preview(preview_ui, frames):
    view = preview_ui
    original = view.color()
    first = frames((180, 70, 40))
    view.start(first)
    view.finish(first, (180, 70, 40))
    second = frames((20, 150, 90))
    view.start(second)
    state = view.state()
    button = view.point("holdOriginalButton")
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.find("canvasViewport").property("holdingOriginal")
    assert view.color() == original
    assert view.find("photoPreviewImage").property("source").toString() == second["url"]
    assert not view.w.property("previewReady")
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.color() == (180, 70, 40)
    view.finish(second, (20, 150, 90))
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.color() == original
    assert view.w.property("previewReady")
    third = frames((120, 70, 160))
    view.start(third)
    assert view.color() == original
    third["release"].set()
    wait_for(lambda: view.w.property("previewReady"))
    assert view.color() == original
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.color() == (120, 70, 160)
    assert view.state() == state


def test_hold_original_preserves_split_comparison(preview_ui, frames):
    view = preview_ui
    original = view.color()
    frame = frames((180, 70, 40))
    view.start(frame)
    view.finish(frame, (180, 70, 40))
    view.w.setProperty("compare", True)
    view.w.setProperty("split", .4)
    assert view.color(.25) == original and view.color(.75) == (180, 70, 40)
    button = view.point("holdOriginalButton")
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.color(.75) == original
    assert not view.find("compareHandle").property("visible")
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, button)
    assert view.w.property("compare") and view.w.property("split") == .4
    assert view.find("compareHandle").property("visible")
    assert view.color(.25) == original and view.color(.75) == (180, 70, 40)


def test_late_previous_frame_cannot_replace_latest_result(preview_ui, frames):
    view = preview_ui
    slow, latest = frames((180, 70, 40)), frames((20, 150, 90))
    state = view.state()
    view.start(slow)
    view.start(latest)
    view.finish(latest, (20, 150, 90))
    slow["release"].set()
    QTest.qWait(100)
    assert view.color() == (20, 150, 90)
    assert view.find("photoPreviewImage").property("source").toString() == latest["url"]
    assert view.state() == state


def test_opening_other_photo_clears_retained_frame_and_late_load(preview_ui, frames):
    view = preview_ui
    shown, slow = frames((180, 70, 40)), frames((20, 150, 90))
    view.start(shown)
    view.finish(shown, (180, 70, 40))
    view.start(slow)
    old_original = view.e.originalUrl
    other = view.directory / "other.png"
    Image.new("RGB", (350, 220), (30, 40, 190)).save(other)
    view.e.openImage(str(other))
    wait_for(lambda: view.e.originalUrl != old_original)
    assert view.color() != (180, 70, 40)
    wait_for(lambda: settled(view.e) and view.w.property("previewReady"))
    assert view.color() == (30, 40, 190)
    slow["release"].set()
    QTest.qWait(100)
    assert view.color() == (30, 40, 190)
    assert view.e.imageName == other.name


def test_failed_preview_shows_original_and_recovers_without_losing_draft(preview_ui, frames):
    view = preview_ui
    original = view.color()
    view.e.drawDraft("rect", "replace", [[.1, .1], [.9, .9]], .025)
    wait_for(lambda: settled(view.e) and view.w.property("previewReady")
             and view.w.property("selectionPreviewReady"))
    assert view.e.selection.showMask and view.color() != original
    state = view.state()
    broken = frames(None)
    view.start(broken)
    broken["release"].set()
    wait_for(lambda: view.find("canvasViewport").property("previewFailed"))
    assert view.find("previewLoadFailure").property("visible")
    assert not view.w.property("previewReady")
    assert view.color() == original
    assert view.e.selection.showMask and view.e.hasSelectionDraft
    assert view.state() == state
    # Canvas and navigator share this failed source. Only those exact errors
    # are expected; binding/layout errors must still fail the fixture.
    wait_for(lambda: len(view.errors) == 2)
    assert all(broken["url"] in error and "server replied: Not Found" in error
               and ("CanvasViewport.qml" in error or "CanvasNavigator.qml" in error)
               for error in view.errors), view.errors
    view.errors.clear()
    view.e.selection.toggleShowMask()
    good = frames((180, 70, 40))
    view.start(good)
    view.finish(good, (180, 70, 40))
    assert not view.find("previewLoadFailure").property("visible")
    assert view.e.hasSelectionDraft and view.state() == state
