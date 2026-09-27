"""Content-aware fill: protocol, pixels, preview cache, editor flow."""

import pytest
from PIL import Image, ImageDraw

from iphoto.controllers import selections as selections_mod
from iphoto.document import render_nodes, validate_layers
from iphoto.preview_cache import LayerPreviewCache
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled


def blob_image():
    image = Image.new("RGB", (200, 120), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 40, 100, 80), fill=(10, 10, 10))
    return image


def inpaint_layer(rect=((0.3, 0.333), (0.5, 0.667)), method="telea", radius=5):
    return {
        "id": "fill",
        "name": "填充",
        "visible": True,
        "opacity": 1.0,
        "recipe": {},
        "locked": [],
        "mask": {
            "base": "empty",
            "inverted": False,
            "feather": 0.0,
            "label": "填充区",
            "ops": [{"kind": "rect", "mode": "add", "points": [list(rect[0]), list(rect[1])]}],
        },
        "kind": "adjustment",
        "parent_id": "",
        "collapsed": False,
        "inpaint": {"method": method, "radius": radius},
    }


def test_inpaint_removes_blob_pixels():
    layer = inpaint_layer()
    out = render_nodes(blob_image(), [{"layer": layer, "children": []}])
    assert out.getpixel((80, 60))[0] > 200
    assert out.getpixel((10, 10)) == (255, 255, 255)
    assert out.getpixel((150, 100)) == (255, 255, 255)


def test_preview_cache_includes_zero_recipe_inpaint_layers():
    layers = [inpaint_layer()]
    direct = render_nodes(blob_image(), [{"layer": layers[0], "children": []}])
    cached = LayerPreviewCache().render(blob_image(), layers)
    assert cached.tobytes() == direct.tobytes()


@pytest.mark.parametrize(
    "bad",
    [
        {"method": "magic", "radius": 5},
        {"method": "telea", "radius": 0},
        {"method": "telea", "radius": 99},
        {"method": "telea"},
        "telea",
    ],
)
def test_invalid_inpaint_rejected(bad):
    layer = inpaint_layer()
    layer["inpaint"] = bad
    with pytest.raises(ValueError):
        validate_layers([layer])


def test_group_inpaint_rejected():
    layer = inpaint_layer()
    layer["kind"] = "group"
    with pytest.raises(ValueError):
        validate_layers([layer])


def test_clean_layer_keeps_inpaint():
    clean = validate_layers([inpaint_layer()])[0]
    assert clean["inpaint"] == {"method": "telea", "radius": 5.0}


@pytest.fixture
def workspace(tmp_path, qt_app, ai_store):
    path = tmp_path / "blob.png"
    blob_image().save(path)
    editor = Editor(ai_store=ai_store)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))
    try:
        yield editor
    finally:
        editor.close()


def test_apply_inpaint_creates_layer_and_undoes(workspace):
    e = workspace
    e.beginSelection("empty")
    e.drawDraft("rect", "replace", [[0.3, 0.333], [0.5, 0.667]], 0.02)
    wait_for(lambda: settled(e))
    e.selection.apply("inpaint")
    wait_for(lambda: settled(e))
    assert not e.hasSelectionDraft
    assert len(e.layers) == 2
    assert e._layers[-1]["inpaint"] == {"method": "telea", "radius": 5.0}
    assert e._layers[-1]["name"].startswith("内容感知填充")
    e.undo()
    wait_for(lambda: settled(e))
    assert len(e.layers) == 1


def test_apply_inpaint_without_capability_notifies(workspace, monkeypatch):
    e = workspace
    monkeypatch.setattr(selections_mod, "capabilities", lambda: [])
    e.beginSelection("empty")
    e.drawDraft("rect", "replace", [[0.3, 0.333], [0.5, 0.667]], 0.02)
    wait_for(lambda: settled(e))
    e.selection.apply("inpaint")
    wait_for(lambda: settled(e))
    assert len(e.layers) == 1 and e.hasSelectionDraft
    assert "OpenCV" in e.status


def test_apply_inpaint_empty_selection_notifies(workspace):
    e = workspace
    e.beginSelection("empty")
    wait_for(lambda: settled(e))
    e.selection.apply("inpaint")
    wait_for(lambda: settled(e))
    assert len(e.layers) == 1 and e.hasSelectionDraft
    assert "选区为空" in e.status
