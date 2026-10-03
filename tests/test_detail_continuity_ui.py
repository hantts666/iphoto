"""Actual full Qt view with held tile delivery; rendering is tested separately."""

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from iphoto.controllers import detail_tiles
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_preview_continuity_ui import frames  # noqa: F401


@pytest.fixture
def detail_ui(canvas, monkeypatch):  # noqa: F811
    ui = canvas
    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("detailReady"), seconds=20)
    # Hold only subsequent delivery. The starting tile and photo came from the
    # actual worker; controlled PNGs exercise asynchronous QML texture swaps.
    monkeypatch.setattr(detail_tiles, "request", lambda editor: None)
    ui.e._detail_idle_timer.stop()

    class View:
        e, w = ui.e, ui.w

        def image(self, name="detailImage"):
            return ui.find(name)

        def point(self, name):
            return ui.point(name)

        def color(self):
            QTest.qWait(30)
            return ui.w.grabWindow().pixelColor(ui.point("canvasSurface")).getRgb()[:3]

        def deliver(self, frame, box=None, mask=None):
            ui.e._detail_box = box or (0, 0, 2400, 1600)
            ui.e._detail_generation = ui.e._generation
            ui.e._detail_frame_version = ui.e._detail_version
            ui.e._detail_url = frame["url"]
            ui.e._detail_mask_url = mask["url"] if mask else ""
            ui.e.changed.emit()
            wait_for(lambda: frame["requested"].is_set())

        def finish(self, frame, color):
            frame["release"].set()
            wait_for(lambda: self.image().property("hasFrame") and self.color() == color)

        def caption(self):
            return ui.find("detailStatusCaption").property("text")

        def state(self):
            return deepcopy((ui.e._layers, ui.e._history, ui.e._cursor, ui.e._generation))

    yield View()


def test_pending_tile_keeps_its_pixels_and_rectangle_until_new_texture_ready(detail_ui, frames):  # noqa: F811
    view = detail_ui
    old, next_frame = frames((180, 70, 40)), frames((20, 150, 90))
    view.deliver(old)
    view.finish(old, (180, 70, 40))
    assert view.w.property("detailReady")
    image = view.image()
    old_geometry = (image.x(), image.y(), image.width(), image.height())
    state = view.state()
    new_box = (256, 128, 2176, 1536)
    view.deliver(next_frame, new_box)
    assert view.color() == (180, 70, 40)
    assert (image.x(), image.y(), image.width(), image.height()) == old_geometry
    view.finish(next_frame, (20, 150, 90))
    photo = image.parentItem()
    assert (image.x(), image.y(), image.width(), image.height()) == pytest.approx(
        (256/2400*photo.width(), 128/1600*photo.height(), 1920/2400*photo.width(), 1408/1600*photo.height())
    )
    assert view.w.property("detailReady") and view.state() == state


def test_edit_keeps_sharp_frame_but_does_not_claim_latest_effect_ready(detail_ui, frames):  # noqa: F811
    view = detail_ui
    old, updated = frames((180, 70, 40)), frames((20, 150, 90))
    view.deliver(old)
    view.finish(old, (180, 70, 40))
    view.e.setParameter("exposure", .5)
    QTest.qWait(120)
    assert view.image().isVisible() and view.color() == (180, 70, 40)
    assert not view.w.property("detailReady")
    assert "暂显上一次效果" in view.caption()
    view.deliver(updated)
    assert view.color() == (180, 70, 40) and not view.w.property("detailReady")
    view.finish(updated, (20, 150, 90))
    assert view.w.property("detailReady") and "原图细节已显示" in view.caption()


def test_pan_outside_tile_does_not_claim_detail_ready_and_does_not_edit_document(detail_ui):
    view = detail_ui
    state = view.state()
    view.e.viewport.pan(-950, -550)
    QTest.qWait(40)
    assert not view.w.property("detailReady")
    assert "正在载入当前区域细节" in view.caption()
    assert view.state() == state


def test_latest_tile_wins_when_abandoned_request_arrives_late(detail_ui, frames):  # noqa: F811
    view = detail_ui
    slow, latest = frames((180, 70, 40)), frames((20, 150, 90))
    view.deliver(slow, (256, 128, 2176, 1536))
    view.deliver(latest)
    view.finish(latest, (20, 150, 90))
    slow["release"].set()
    QTest.qWait(100)
    assert view.color() == (20, 150, 90)
    assert view.image().property("displayedRect").toVariant() == [0, 0, 1, 1]
    assert view.w.property("detailReady")


def test_mask_waits_for_matching_color_rectangle_and_generation(detail_ui, frames):  # noqa: F811
    view = detail_ui
    if not view.e.selection.showMask:
        view.e.selection.toggleShowMask()
    # This delivery test isolates the native overlay from the existing full
    # image mask, whose actual alpha/boundary is covered in test_detail_ui.
    view.e._mask_url = ""
    view.e.changed.emit()
    old = frames((180, 70, 40))
    view.deliver(old)
    view.finish(old, (180, 70, 40))
    color, mask = frames((20, 150, 90)), frames((120, 70, 160))
    view.deliver(color, (256, 128, 2176, 1536), mask)
    mask["release"].set()
    wait_for(lambda: view.image("detailMaskImage").property("hasFrame"))
    assert not view.w.property("maskDetailReady") and view.color() == (180, 70, 40)
    color["release"].set()
    wait_for(lambda: view.w.property("maskDetailReady"))
    assert view.color() == (120, 70, 160)
    view.e._detail_version += 1
    view.e.changed.emit()
    QTest.qWait(30)
    assert not view.w.property("maskDetailReady") and view.color() == (20, 150, 90)


def test_original_peek_shows_native_source_and_failed_effect_falls_back(detail_ui, frames):  # noqa: F811
    view = detail_ui
    old = frames((180, 70, 40))
    view.deliver(old)
    view.finish(old, (180, 70, 40))
    point = view.point("holdOriginalButton")
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, point)
    assert not view.w.property("detailReady") and view.color() == (74, 123, 142)
    assert view.image('originalDetailImage').isVisible()
    assert "原图细节已显示" in view.caption()
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, point)
    assert view.w.property("detailReady") and view.color() == (180, 70, 40)
    view.e._detail_version += 1
    view.e._detail_failed_generation = (view.e._generation, view.e._detail_version)
    view.e.changed.emit()
    QTest.qWait(30)
    assert not view.w.property("detailReady") and view.color() == (74, 123, 142)
    assert "细节未能更新" in view.caption()
