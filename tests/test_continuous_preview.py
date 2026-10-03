"""Continuous real input, and late frames at document boundaries."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest

from iphoto.controllers import detail_tiles, preview_updates, worker_bridge
from iphoto.document import render_layers
from iphoto.engine import load_source
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


@pytest.mark.parametrize("native", [False, True])
def test_continuous_slider_shows_multiple_real_frames_and_one_undo(canvas, native):  # noqa: F811
    ui = canvas
    ui.click("layerSelect_" + ui.e.activeLayerId)
    if native:
        ui.click("actualSizeButton")
        wait_for(lambda: ui.w.property("detailReady"), seconds=20)
    before = deepcopy(ui.e._layers)
    cursor = ui.e._cursor
    name = "parameter_exposure"
    slider = ui.find(name)
    assert slider.isVisible()
    scroll = ui.find("propertyScroll")
    top = slider.mapToItem(scroll, 0, 0).y()
    assert top >= 0 and top + slider.height() <= scroll.height(), "Slider must be inside the actual clipped viewport"
    QTest.mousePress(ui.w, Qt.LeftButton, Qt.NoModifier, ui.point(name, slider.property("visualPosition")))
    generations = set()
    saw_pending = False
    for index in range(90):
        x = .5 + index / 90 * .33
        QTest.mouseMove(ui.w, ui.point(name, x), 20)
        item = ui.find("detailImage" if native else "photoPreviewImage")
        generations.add(item.property("displayedGeneration"))
        if item.property("displayedGeneration") != ui.e._generation:
            saw_pending = saw_pending or "正在更新" in ui.find("detailStatusCaption").property("text")
    # At least two completed updates actually became QML textures before release.
    assert len(generations) >= 3, generations
    assert saw_pending
    QTest.mouseRelease(ui.w, Qt.LeftButton, Qt.NoModifier, ui.point(name, x))
    wait_for(lambda: settled(ui.e) and ui.w.property("previewReady")
             and (not native or ui.w.property("detailReady")), seconds=20)
    assert ui.e._cursor == cursor + 1 and "exposure" in ui.e._locked
    full = render_layers(load_source(ui.e._path).image, deepcopy(ui.e._layers))
    if native:
        with Image.open(QUrl(ui.e.detailUrl).toLocalFile()) as tile:
            assert tile.tobytes() == full.crop(ui.e._detail_box).tobytes()
        assert ui.find("detailImage").property("displayedGeneration") == ui.e._generation
    else:
        with Image.open(QUrl(ui.e.previewUrl).toLocalFile()) as proxy:
            assert proxy.getpixel((proxy.width//2, proxy.height//2)) == full.getpixel((full.width//2, full.height//2))
        assert ui.find("photoPreviewImage").property("displayedGeneration") == ui.e._generation
    ui.e.undo()
    wait_for(lambda: settled(ui.e) and ui.w.property("previewReady")
             and (not native or ui.w.property("detailReady")), seconds=20)
    assert ui.e._layers == before


def _signal():
    return SimpleNamespace(emit=lambda: None)


@pytest.mark.parametrize("generation,span,delivered,accepted", [
    (16, (10, 20), 15, True),
    (14, (10, 20), 15, False),  # Completed frame cannot move backward.
    (9, (10, 20), 8, False),  # Older edit before this continuous input.
    (16, None, 15, False),  # Undo / reset / a structural change.
    (16, (10, 19), 15, False),  # Direct generation change, such as selection.
    (20, None, 15, True),  # Exact current result retains normal acceptance.
])
def test_proxy_delivery_keeps_intermediate_metadata_and_rejects_old_edits(tmp_path, generation, span, delivered, accepted):
    response = {"id": 1, "op": "render", "generation": generation, "ok": True,
                "result": {"preview": str(tmp_path / "result.png"), "histogram": [1],
                           "elapsed_ms": 7, "mask": str(tmp_path / "mask.png")}}
    owner = SimpleNamespace(
        process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()),
        _buffer=b"", _active={"id": 1, "op": "render"}, _generation=20,
        _parameter_preview_span=span, _preview_generation=delivered, _preview="previous",
        _mask_url="current-mask", _histogram=[], _stats=None, _elapsed=0, _last_render_metrics={},
        changed=_signal(), _pump=lambda: None,
    )
    worker_bridge._read(owner)
    assert owner._active is None
    assert (owner._preview != "previous") is accepted
    assert owner._preview_generation == (generation if accepted else delivered)
    assert owner._generation == 20
    assert owner._mask_url == (QUrl.fromLocalFile(response["result"]["mask"]).toString()
                               if accepted and generation == 20 else "current-mask")


@pytest.mark.parametrize("generation,version,span,source,delivered,accepted", [
    (16, 3, (10, 20), "photo", (15, 2), True),
    (14, 3, (10, 20), "photo", (15, 2), False),
    (16, 3, (10, 20), "photo", (16, 4), False),
    (9, 3, (10, 20), "photo", (8, 2), False),
    (16, 3, None, "photo", (15, 2), False),
    (16, 3, (10, 19), "photo", (15, 2), False),
    (16, 3, (10, 20), "old-photo", (15, 2), False),
    (16, 6, (10, 20), "photo", (15, 2), False),
    (20, 4, (10, 20), "photo", (15, 2), False),
    (20, 5, None, "photo", (15, 2), True),
])
def test_detail_delivery_preserves_frame_coordinates_and_document_guards(tmp_path, generation, version, span, source, delivered, accepted):
    response = {"id": 1, "ok": True, "result": {"path": str(tmp_path / "tile.png"),
                "mask": str(tmp_path / "mask.png"), "box": [128, 128, 1024, 768]}}
    owner = SimpleNamespace(
        _detail_process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(response) + "\n").encode()),
        _detail_buffer=b"", _detail_active={"id": 1, "generation": generation, "version": version, "source_sha": source},
        _detail_pending=None, _generation=20, _detail_version=5, _sha="photo",
        _parameter_preview_span=span, _detail_generation=delivered[0], _detail_frame_version=delivered[1],
        _detail_url="previous", _detail_mask_url="current-mask", _detail_box=(0, 0, 512, 512),
        _detail_idle_timer=SimpleNamespace(start=lambda: None), _closing=False, _detail_aborting=False,
        changed=_signal(),
    )
    detail_tiles.read(owner)
    assert owner._detail_active is None
    assert (owner._detail_url != "previous") is accepted
    assert (owner._detail_generation, owner._detail_frame_version) == ((generation, version) if accepted else delivered)
    assert owner._detail_box == ((128, 128, 1024, 768) if accepted else (0, 0, 512, 512))
    assert owner._generation == 20 and owner._detail_version == 5
    assert owner._detail_mask_url == (QUrl.fromLocalFile(response["result"]["mask"]).toString()
                                     if accepted and generation == 20 else "" if accepted else "current-mask")


def test_new_parameter_after_document_boundary_cannot_reopen_old_preview_span():
    timer = SimpleNamespace(isActive=lambda: True, start=lambda: None)
    owner = SimpleNamespace(_generation=20, _parameter_preview_span=(10, 20), _timer=timer, changed=_signal())
    owner._generation += 1  # A selection / layer navigation / photo lifecycle change.
    preview_updates.changed(owner, parameter=True)
    assert not preview_updates.accepts(owner, 20)
    assert preview_updates.accepts(owner, 22)
    preview_updates.changed(owner, parameter=True)
    assert preview_updates.accepts(owner, 22)
    preview_updates.changed(owner)
    assert not preview_updates.accepts(owner, 22)
