"""Healing brush: stroke protocol, pixel repair, editor flow, tool routing."""

import pytest
from PIL import Image, ImageDraw

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


def scratch_image():
    image = Image.new("RGB", (200, 120), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.line((60, 60, 100, 60), fill=(10, 10, 10), width=8)
    return image


def heal_layer(strokes=(((0.3, 0.5), (0.5, 0.5)),), radius=0.08):
    return {
        "id": "heal",
        "name": "修复",
        "visible": True,
        "opacity": 1.0,
        "recipe": {},
        "locked": [],
        "mask": {
            "base": "empty",
            "inverted": False,
            "feather": 0.0,
            "label": "全图",
            "ops": [],
        },
        "kind": "adjustment",
        "parent_id": "",
        "collapsed": False,
        "heal": {
            "ops": [
                {"kind": "heal", "points": [list(p) for p in stroke], "radius": radius}
                for stroke in strokes
            ]
        },
    }


def test_heal_stroke_repairs_blob():
    out = render_nodes(scratch_image(), [{"layer": heal_layer(), "children": []}])
    assert out.getpixel((80, 60))[0] > 200
    assert out.getpixel((10, 10)) == (255, 255, 255)


def test_preview_cache_includes_heal_layers():
    layers = [heal_layer()]
    direct = render_nodes(scratch_image(), [{"layer": layers[0], "children": []}])
    cached = LayerPreviewCache().render(scratch_image(), layers)
    assert cached.tobytes() == direct.tobytes()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda layer: layer["heal"].update({"ops": []}),
        lambda layer: layer["heal"]["ops"][0].update({"kind": "brush"}),
        lambda layer: layer["heal"]["ops"][0].update({"radius": 0.9}),
        lambda layer: layer["heal"]["ops"][0].update({"points": [[2, 2]]}),
        lambda layer: layer.update({"kind": "group"}),
    ],
)
def test_invalid_heal_rejected(mutate):
    layer = heal_layer()
    mutate(layer)
    with pytest.raises(ValueError):
        validate_layers([layer])


def test_clean_layer_keeps_heal():
    clean = validate_layers([heal_layer()])[0]
    assert clean["heal"]["ops"][0]["kind"] == "heal"
    assert clean["heal"]["ops"][0]["radius"] == pytest.approx(0.08)


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


def test_draw_heal_commits_per_stroke_and_undoes(workspace):
    e = workspace
    e.selection.chooseTool("heal")
    assert e.selection.tool == "heal" and not e.hasSelectionDraft
    e.drawHeal([[0.35, 0.4], [0.45, 0.6]], 0.06)
    wait_for(lambda: settled(e))
    assert len(e._layer()["heal"]["ops"]) == 1
    e.drawHeal([[0.4, 0.5]], 0.05)
    wait_for(lambda: settled(e))
    assert len(e._layer()["heal"]["ops"]) == 2
    e.undo()
    wait_for(lambda: settled(e))
    assert len(e._layer()["heal"]["ops"]) == 1
    e.undo()
    wait_for(lambda: settled(e))
    assert "heal" not in e._layer()


def test_draw_heal_refused_during_draft(workspace):
    e = workspace
    e.beginSelection("empty")
    e.drawHeal([[0.4, 0.5]], 0.05)
    wait_for(lambda: settled(e))
    assert "heal" not in e._layer()
    assert "选区" in e.status


def test_draw_heal_refused_on_group(workspace):
    e = workspace
    e.groupLayer()
    e.drawHeal([[0.4, 0.5]], 0.05)
    wait_for(lambda: settled(e))
    assert "heal" not in e._layer()
    assert "修复" in e.status


def test_draw_heal_rejects_bad_stroke(workspace):
    e = workspace
    e.drawHeal([[2.0, 0.5]], 0.05)
    wait_for(lambda: settled(e))
    assert "heal" not in e._layer()
