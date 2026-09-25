"""Geometry invariants, independent of renderer and mouse-event delivery."""

import math
import pytest
from iphoto.viewport import Viewport


@pytest.fixture
def view(qt_app):
    model = Viewport()
    model.resize(900, 600, 1)
    model.setSource(2400, 1600)
    return model


@pytest.mark.parametrize("dpr", [1, 1.25, 1.5, 2])
def test_fit_and_physical_pixel_scale(view, dpr):
    view.resize(900, 600, dpr)
    assert view.fitMode
    assert view.imageWidth == pytest.approx(900)
    assert view.imageHeight == pytest.approx(600)
    assert view.visibleRect == pytest.approx([0, 0, 1, 1])
    view.setZoom(1)
    assert view.imageWidth * dpr == pytest.approx(2400)
    assert view.imageHeight * dpr == pytest.approx(1600)


@pytest.mark.parametrize("anchor", [(0, 0), (450, 300), (875, 585)])
def test_zoom_keeps_source_point_under_cursor(view, anchor):
    x, y = anchor
    source_point = (
        (x - view.imageX) / view.imageWidth,
        (y - view.imageY) / view.imageHeight,
    )
    for zoom in (1, 4, 32, 2, 0.375):
        view.zoomAt(zoom, x, y)
        assert (
            (x - view.imageX) / view.imageWidth,
            (y - view.imageY) / view.imageHeight,
        ) == pytest.approx(source_point)


@pytest.mark.parametrize("dpr", [1, 1.5, 2])
def test_resize_preserves_center_and_zoom_outside_fit(view, dpr):
    view.setZoom(2)
    view.centerOn(0.7, 0.3)
    view.resize(600, 450, dpr)
    assert view.zoom == 2 and not view.fitMode
    assert (
        (300 - view.imageX) / view.imageWidth,
        (225 - view.imageY) / view.imageHeight,
    ) == pytest.approx((0.7, 0.3))
    view.fit()
    view.resize(1000, 500, 1)
    assert view.imageHeight == pytest.approx(500)
    assert view.imageX == pytest.approx((1000 - 750) / 2)


@pytest.mark.parametrize("zoom", [0.01, 0.5, 1, 32])
def test_extreme_drag_never_loses_image_and_navigator_stays_valid(view, zoom):
    view.setZoom(zoom)
    for x, y in ((1e9, 1e9), (-1e9, -1e9), (1e9, -1e9)):
        view.pan(x, y)
        assert view.imageX < 900 and view.imageX + view.imageWidth > 0
        assert view.imageY < 600 and view.imageY + view.imageHeight > 0
        left, top, width, height = view.visibleRect
        assert 0 <= left <= 1 and 0 <= top <= 1
        assert 0 < width <= 1 and 0 < height <= 1
    view.fit()
    assert view.fitMode and view.visibleRect == pytest.approx([0, 0, 1, 1])


def test_limits_steps_invalid_input_and_new_document(view):
    view.setZoom(1e20)
    assert view.zoom == 32
    view.step(1)
    assert view.zoom == 32
    view.setZoom(-1)
    assert view.zoom == 0.01
    for value in (math.nan, math.inf, -math.inf):
        view.setZoom(value)
        view.pan(value, 0)
        view.resize(value, 100, 1)
        assert math.isfinite(view.imageX) and view.zoom == 0.01
    view.setZoom(0.49)
    view.step(1)
    assert view.zoom == 0.5
    view.step(-1)
    assert view.zoom == 0.3333
    view.setSource(400, 300)
    assert view.fitMode and view.zoom == 1
    assert not view.reducedPreview
    view.setSource(50000, 1)
    view.resize(200, 200, 1)
    assert view.zoom == 0.004
    view.setZoom(0.001)
    assert view.zoom == 0.004
