"""The actual Qt canvas swaps its enlarged proxy for source-resolution detail."""

import numpy as np
from PIL import Image, ImageStat
from PySide6.QtCore import QPointF, QProcess, QUrl
from PySide6.QtGui import QImage

from iphoto.controllers import detail_tiles
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def test_first_native_zoom_dispatches_without_waiting_for_the_viewport_debounce(canvas, monkeypatch):  # noqa: F811
    ui = canvas
    requests = []
    original_request = detail_tiles.request

    def observe(editor):
        requests.append((editor.viewport.zoom, list(editor.viewport.visibleRect)))
        return original_request(editor)

    monkeypatch.setattr(detail_tiles, "request", observe)
    assert not ui.find("canvasViewport").property("detailWanted")
    before = (ui.e._cursor, ui.e._generation, ui.e.dirty)
    ui.click("actualSizeButton")
    # The initial request is scheduled in the next event turn, without a
    # fixed settling delay; ui.click delivers input and processes 40ms.
    assert requests and requests[0][0] == 1
    count = len(requests)
    for zoom in (1.25, 1.5, 1.75):
        ui.n.setZoom(zoom)
        ui.app.processEvents()
    assert len(requests) == count
    wait_for(lambda: ui.w.property("detailReady") and settled(ui.e), seconds=20)
    assert before == (ui.e._cursor, ui.e._generation, ui.e.dirty)


def test_zoom_100_loads_true_photo_pixels_and_fit_releases_worker(canvas, tmp_path):  # noqa: F811
    ui = canvas
    path = tmp_path / "fine-detail.png"
    row = (np.arange(2400, dtype=np.uint8) % 2) * 255
    pixels = np.tile(row, (1600, 1))
    pixels[1::2] = 255 - pixels[1::2]
    Image.fromarray(pixels, "L").convert("RGB").save(path)
    ui.e.openImage(str(path))
    wait_for(lambda: ui.e.imageName == path.name and settled(ui.e) and ui.w.property("previewReady"))
    with Image.open(QUrl(ui.e.previewUrl).toLocalFile()) as proxy:
        patch = proxy.crop((720, 480, 880, 640)).convert("L")
        assert ImageStat.Stat(patch).stddev[0] < 2

    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("detailReady") and bool(ui.e.detailUrl), seconds=20)
    shot = ui.w.grabWindow()
    center_item = ui.find("canvasSurface")
    center = center_item.mapToScene(QPointF(center_item.width()/2, center_item.height()/2)).toPoint()
    patch = shot.copy(center.x()-70, center.y()-70, 140, 140).convertToFormat(QImage.Format_Grayscale8)
    luminance = np.frombuffer(patch.bits(), dtype=np.uint8).reshape(patch.height(), patch.bytesPerLine())[:, :patch.width()]
    assert float(luminance.std()) > 100

    first_detail = ui.e.detailUrl
    ui.e._detail_idle_timer.setInterval(80)
    ui.e._detail_idle_timer.start()
    wait_for(lambda: ui.e._detail_process.state() == QProcess.NotRunning)
    assert ui.e.detailUrl == first_detail and ui.w.property("detailReady")
    ui.e.viewport.pan(-600, 0)
    wait_for(lambda: ui.w.property("detailReady") and ui.e.detailUrl != first_detail, seconds=20)

    ui.click("fitCanvasButton")
    wait_for(lambda: not ui.e.detailUrl and ui.e._detail_process.state() == QProcess.NotRunning)


def test_missing_source_keeps_quick_preview_and_clears_detail_loading(canvas, tmp_path):  # noqa: F811
    ui = canvas
    path = tmp_path / "removed-after-open.png"
    Image.new("RGB", (2400, 1600), (70, 100, 130)).save(path)
    ui.e.openImage(str(path))
    wait_for(lambda: ui.e.imageName == path.name and settled(ui.e) and ui.w.property("previewReady"))
    path.unlink()
    ui.click("actualSizeButton")
    wait_for(lambda: ui.e._detail_failed_generation == (ui.e._generation, ui.e._detail_version))
    assert ui.w.property("previewReady") and not ui.e.detailUrl and not ui.e.detailLoading


def test_switching_photo_discards_previous_detail(canvas, tmp_path):  # noqa: F811
    ui = canvas
    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("detailReady") and bool(ui.e.detailUrl), seconds=20)
    other = tmp_path / "other-photo.png"
    Image.new("RGB", (2000, 1400), (180, 70, 40)).save(other)
    ui.e.openImage(str(other))
    wait_for(lambda: ui.e.imageName == other.name and settled(ui.e)
             and ui.e._detail_process.state() == QProcess.NotRunning)
    assert not ui.e.detailUrl and ui.e.viewport.fitMode


def test_zoom_mask_uses_source_pixels_and_shared_preview_state(canvas):  # noqa: F811
    ui = canvas
    ui.e.drawDraft("rect", "replace", [[.48, .2], [.8, .8]], .025)
    wait_for(lambda: ui.e.hasSelectionDraft and settled(ui.e) and bool(ui.e.maskUrl))
    assert ui.e.selection.showMask
    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("detailReady") and ui.w.property("maskDetailReady")
             and bool(ui.e.detailMaskUrl), seconds=20)
    left = round(ui.e.detailRect[0] * 2400)
    top = round(ui.e.detailRect[1] * 1600)
    with Image.open(QUrl(ui.e.detailMaskUrl).toLocalFile()) as overlay:
        assert overlay.size == (round(ui.e.detailRect[2] * 2400),
                                round(ui.e.detailRect[3] * 1600))
        assert overlay.getpixel((1150 - left, 800 - top))[3] == 0
        assert overlay.getpixel((1154 - left, 800 - top))[3] == 97
    boundary = ui.find("photoCanvas").mapToScene(QPointF(1152, 800)).toPoint()
    detailed = ui.w.grabWindow()
    ui.find("detailMaskImage").setProperty("source", "")
    ui.app.processEvents()
    fallback = ui.w.grabWindow()

    def transition_error(image):
        outside = image.pixelColor(boundary.x()-5, boundary.y()).getRgb()[:3]
        inside = image.pixelColor(boundary.x()+5, boundary.y()).getRgb()[:3]
        error = 0
        for x in range(-2, 3):
            color = image.pixelColor(boundary.x()+x, boundary.y()).getRgb()[:3]
            error += min(sum(abs(a-b) for a, b in zip(color, endpoint))
                         for endpoint in (outside, inside))
        return error

    assert transition_error(detailed) < transition_error(fallback)


def test_zoom_detail_matches_draft_adjustment_preview(canvas):  # noqa: F811
    ui = canvas
    ui.e.setParameter("exposure", 1)
    wait_for(lambda: settled(ui.e) and bool(ui.e.previewUrl))
    ui.e.drawDraft("rect", "replace", [[.45, .35], [.55, .65]], .025)
    ui.e.selection.setMaskView("adjustment")
    wait_for(lambda: settled(ui.e) and not ui.e.selection.showMask)
    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("detailReady") and bool(ui.e.detailUrl), seconds=20)
    left = round(ui.e.detailRect[0] * 2400)
    top = round(ui.e.detailRect[1] * 1600)
    with Image.open(QUrl(ui.e.previewUrl).toLocalFile()) as proxy, \
            Image.open(QUrl(ui.e.detailUrl).toLocalFile()) as detail:
        for source_x in (1000, 1200):
            expected = proxy.getpixel((round(source_x / 2400 * proxy.width), proxy.height // 2))
            actual = detail.getpixel((source_x-left, 800-top))
            assert max(abs(a-b) for a, b in zip(actual, expected)) <= 2
        assert detail.getpixel((1200-left, 800-top))[0] > detail.getpixel((1000-left, 800-top))[0]


def test_zoom_grayscale_mask_switches_to_overlay_without_stale_tile(canvas):  # noqa: F811
    ui = canvas
    ui.e.drawDraft("rect", "replace", [[.48, .2], [.8, .8]], .025)
    ui.e.selection.setMaskView("grayscale")
    wait_for(lambda: settled(ui.e) and ui.e.selection.showMask)
    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("maskDetailReady") and bool(ui.e.detailMaskUrl), seconds=20)
    grayscale_url = ui.e.detailMaskUrl
    left = round(ui.e.detailRect[0] * 2400)
    top = round(ui.e.detailRect[1] * 1600)
    with Image.open(QUrl(grayscale_url).toLocalFile()) as grayscale:
        assert grayscale.mode == "RGB"
        assert grayscale.getpixel((1150-left, 800-top)) == (0, 0, 0)
        assert grayscale.getpixel((1154-left, 800-top)) == (255, 255, 255)
    ui.e.selection.setMaskView("overlay")
    wait_for(lambda: ui.w.property("maskDetailReady")
             and ui.e.detailMaskUrl != grayscale_url, seconds=20)
    with Image.open(QUrl(ui.e.detailMaskUrl).toLocalFile()) as overlay:
        assert overlay.mode == "RGBA"


def test_editing_selection_invalidates_old_mask_detail(canvas):  # noqa: F811
    ui = canvas
    ui.e.drawDraft("rect", "replace", [[.48, .2], [.8, .8]], .025)
    wait_for(lambda: settled(ui.e) and bool(ui.e.maskUrl))
    ui.click("actualSizeButton")
    wait_for(lambda: ui.w.property("maskDetailReady"), seconds=20)
    old_url = ui.e.detailMaskUrl
    ui.e.drawDraft("rect", "replace", [[.2, .2], [.48, .8]], .025)
    assert not ui.e.detailMaskUrl and not ui.w.property("maskDetailReady")
    wait_for(lambda: ui.w.property("maskDetailReady")
             and ui.e.detailMaskUrl != old_url, seconds=20)
    left = round(ui.e.detailRect[0] * 2400)
    top = round(ui.e.detailRect[1] * 1600)
    with Image.open(QUrl(ui.e.detailMaskUrl).toLocalFile()) as overlay:
        assert overlay.getpixel((1100-left, 800-top))[3] == 97
        assert overlay.getpixel((1154-left, 800-top))[3] == 0
