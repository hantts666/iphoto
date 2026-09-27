"""Raster mask cache: identity reuse, equivalence, bounded eviction."""

import base64

from PIL import Image
import pytest

from iphoto import masks
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled
from iphoto.document import (
    RASTER_CACHE,
    _RasterCache,
    empty_mask,
    raster_mask,
    raster_mask_cached,
    render_nodes,
)


def rect_mask():
    mask = empty_mask()
    mask["ops"].append(
        {"kind": "rect", "mode": "add", "points": [[0.2, 0.2], [0.7, 0.8]]}
    )
    return mask


def setup_function():
    RASTER_CACHE.clear()


def test_cache_returns_same_object_and_same_pixels():
    mask = rect_mask()
    first = raster_mask_cached(mask, (200, 100))
    second = raster_mask_cached(mask, (200, 100))
    assert first is second
    assert first.tobytes() == raster_mask(mask, (200, 100)).tobytes()


def test_label_changes_do_not_split_cache_entries():
    mask = rect_mask()
    raster_mask_cached(mask, (64, 64))
    renamed = dict(mask, label="别的名字")
    assert raster_mask_cached(renamed, (64, 64)) is not None
    assert len(RASTER_CACHE.entries) == 1


def test_eviction_respects_byte_budget():
    cache = _RasterCache(max_bytes=10_000)
    for index in range(5):
        cache.put(("k", index), Image.new("L", (100, 50), index))
    assert cache.bytes <= 10_000
    assert ("k", 4) in cache.entries and ("k", 0) not in cache.entries


def test_render_nodes_populates_cache():
    layer = {
        "id": "a",
        "name": "层",
        "visible": True,
        "opacity": 1.0,
        "recipe": {"exposure": 0.5},
        "locked": [],
        "mask": rect_mask(),
        "kind": "adjustment",
        "parent_id": "",
        "collapsed": False,
    }
    image = Image.new("RGB", (200, 100), (40, 60, 90))
    render_nodes(image, [{"layer": layer, "children": []}])
    assert len(RASTER_CACHE.entries) == 1


def test_full_size_png_validation_does_not_retain_decoded_masks(monkeypatch):
    masks._DECODE_CACHE.clear()
    masks._DECODE_CACHE_BYTES = 0
    masks._VALIDATED.clear()
    monkeypatch.setattr(masks, "MAX_DECODE_CACHE_BYTES", 2_200_000)
    bitmaps = [
        masks.encode_bitmap(Image.new("L", (1024, 1024), value), preserve_resolution=True)
        for value in (20, 90, 160)
    ]
    masks._VALIDATED.clear()  # External project: its PNGs have not been checked yet.
    for bitmap in bitmaps:
        masks.validate_bitmap(bitmap)
    assert masks._DECODE_CACHE_BYTES == 0
    for value, bitmap in zip((20, 90, 160), bitmaps):
        assert masks.decode_bitmap(bitmap, (1024, 1024)).getpixel((3, 3)) == value
    assert masks._DECODE_CACHE_BYTES <= masks.MAX_DECODE_CACHE_BYTES
    assert len(masks._DECODE_CACHE) <= 2
    masks._DECODE_CACHE.clear()
    masks._DECODE_CACHE_BYTES = 0
    masks._VALIDATED.clear()


def test_truncated_png_is_rejected_after_a_valid_bitmap_was_seen():
    bitmap = masks.encode_bitmap(Image.new("L", (64, 64), 128))
    raw = base64.b64decode(bitmap["png"])
    damaged = {**bitmap, "png": base64.b64encode(raw[:-24]).decode("ascii")}
    with pytest.raises(ValueError):
        masks.validate_bitmap(damaged)


def test_switching_photos_releases_decoded_mask_cache(tmp_path, qt_app, ai_store):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (300, 200), "blue").save(first)
    Image.new("RGB", (300, 200), "red").save(second)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(first))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor._layer()["mask"]["bitmap"] = masks.encode_bitmap(
            Image.new("L", (1024, 1024), 128), preserve_resolution=True
        )
        assert editor.layerMaskThumbnail(editor.activeLayerId)
        assert masks._DECODE_CACHE_BYTES > 0
        editor.openImage(str(tmp_path / "missing.png"))
        wait_for(lambda: settled(editor))
        assert editor._path == str(first) and masks._DECODE_CACHE_BYTES > 0
        editor.openImage(str(second))
        wait_for(lambda: editor._path == str(second) and settled(editor))
        assert masks._DECODE_CACHE_BYTES == 0
    finally:
        editor.close()
