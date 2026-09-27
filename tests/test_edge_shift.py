"""Mask edge shift: protocol validation, raster morphology, editor flow."""

import pytest
from PIL import Image

from iphoto.document import empty_mask, raster_mask, validate_mask, validate_layers
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled


def shift_mask(base=False, shift=0, rect=None):
    mask = empty_mask(base)
    if rect:
        mask["ops"].append({"kind": "rect", "mode": "add", "points": rect})
    if shift:
        mask["edge_shift"] = shift
    return validate_mask(mask)


@pytest.mark.parametrize("shift", [0, 3, -5, 5])
def test_valid_shifts_roundtrip(shift):
    mask = empty_mask(True)
    if shift:
        mask["edge_shift"] = shift
    clean = validate_mask(mask)
    assert clean.get("edge_shift", 0) == shift


@pytest.mark.parametrize("bad", [6, -6, 2.5, "1", True, 100])
def test_invalid_shifts_rejected(bad):
    mask = empty_mask(True)
    mask["edge_shift"] = bad
    with pytest.raises(ValueError):
        validate_mask(mask)


def test_absent_shift_stays_absent():
    assert "edge_shift" not in validate_mask(empty_mask(True))


def test_contract_shrinks_rectangle():
    mask = shift_mask(rect=[[0.2, 0.2], [0.8, 0.8]], shift=-2)
    image = raster_mask(mask, (200, 100))
    assert image.getpixel((41, 50)) == 0
    assert image.getpixel((43, 50)) == 255


def test_expand_grows_rectangle():
    mask = shift_mask(rect=[[0.2, 0.2], [0.8, 0.8]], shift=2)
    image = raster_mask(mask, (200, 100))
    assert image.getpixel((38, 50)) == 255
    assert image.getpixel((36, 50)) == 0


def test_expand_on_empty_mask_stays_empty():
    image = raster_mask(shift_mask(shift=3), (120, 80))
    assert image.getbbox() is None


def test_layer_roundtrip_keeps_shift():
    layer = {
        "id": "a",
        "name": "层",
        "visible": True,
        "opacity": 1.0,
        "recipe": {},
        "locked": [],
        "mask": shift_mask(rect=[[0.1, 0.1], [0.5, 0.5]], shift=4),
        "kind": "adjustment",
        "parent_id": "",
        "collapsed": False,
    }
    clean = validate_layers([layer])[0]
    assert clean["mask"]["edge_shift"] == 4


@pytest.fixture
def workspace(tmp_path, qt_app, ai_store):
    path = tmp_path / "source.png"
    Image.new("RGB", (300, 200), (55, 90, 130)).save(path)
    editor = Editor(ai_store=ai_store)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))
    try:
        yield editor
    finally:
        editor.close()


def test_editor_edge_shift_flow(workspace):
    e = workspace
    assert e.edgeShift == 0
    e.beginSelection("empty")
    e.drawDraft("rect", "replace", [[0.2, 0.2], [0.7, 0.7]], 0.02)
    e.setEdgeShift(3)
    e.finishSelectionGesture()
    assert e.edgeShift == 3
    assert e._candidate["edge_shift"] == 3
    e.setEdgeShift(99)
    assert e.edgeShift == 5
    e.acceptSelection()
    wait_for(lambda: settled(e))
    assert e._layer()["mask"]["edge_shift"] == 5
    e.undo()
    wait_for(lambda: settled(e))
    assert e._layer()["mask"].get("edge_shift", 0) == 0
