"""Object selection, nested composites, recovery, and cache invariants."""

from copy import deepcopy
import json
import numpy as np
from PIL import Image
import pytest
from iphoto.ai import build_payload
from iphoto.ai_settings import AISettings
from iphoto.ai_tasks import parse_selection
from iphoto.document import (
    new_layer,
    empty_mask,
    raster_mask,
    render_layers,
    validate_layers,
    validate_project,
    write_project,
    read_project,
)
from iphoto.engine import Recipe, render
from iphoto.layer_tree import display_rows, descendants, duplicate_subtree
from iphoto.preview_cache import LayerPreviewCache, composition_key
from iphoto.scene import (
    parse_scene,
    parse_targets,
    validate_catalog,
    combine_masks,
    SceneIndex,
)
from iphoto.workspace import Editor
from iphoto.plugins import refine, bitmap_mask
from test_ai import wait_for, mock_api, configure
from test_editor import settled


def response(result, finish="stop"):
    return {
        "choices": [
            {"finish_reason": finish, "message": {"content": json.dumps(result)}}
        ]
    }


def scene_response():
    return response(
        {
            "status": "analyzed",
            "summary": "两个前景对象及背景；模拟协议测试。",
            "objects": [
                {
                    "name": "左侧方块",
                    "category": "方块",
                    "polygons": [[[100, 100], [400, 100], [400, 700], [100, 700]]],
                },
                {
                    "name": "右侧方块",
                    "category": "方块",
                    "polygons": [[[600, 100], [900, 100], [900, 700], [600, 700]]],
                },
                {
                    "name": "背景",
                    "category": "背景",
                    "polygons": [[[0, 0], [999, 0], [999, 999], [0, 999]]],
                },
            ],
        }
    )


def catalog():
    result = parse_scene(scene_response())
    return {"summary": result["summary"], "objects": result["objects"]}


def target_response(ids=None, exclude=None):
    return response(
        {
            "status": "selected",
            "summary": "组合两个方块",
            "object_ids": ids or ["object-1", "object-2"],
            "exclude_ids": exclude or [],
        }
    )


@pytest.fixture
def workspace_v14(qt_app, ai_store, tmp_path):
    p = tmp_path / "objects.png"
    image = Image.new("RGB", (200, 140), (50, 100, 160))
    image.save(p)
    editor = Editor(ai_store=ai_store)
    editor.openImage(str(p))
    wait_for(lambda: editor.hasImage and settled(editor))
    yield editor
    editor.close()


def test_flat_qwen_coordinates_are_unambiguous_and_strict():
    data = {
        "status": "selected",
        "summary": "三角形",
        "polygons": [[0, 0, 999, 0, 999, 999]],
    }
    mask = parse_selection(response(data))["mask"]
    assert mask["ops"][0]["points"] == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
    for bad in (
        [0, 0, 999, 0, 999],
        [0, False, 999, 0, 999, 999],
        [0, 0, 1000, 0, 999, 999],
        [0, 0, float("nan"), 0, 999, 999],
    ):
        with pytest.raises(ValueError):
            parse_selection(response({**data, "polygons": [bad]}))
    with pytest.raises(ValueError):
        parse_selection(response({"type": "object", "properties": {}}))
    with pytest.raises(ValueError):
        parse_selection(response(data, "length"))


@pytest.mark.parametrize("mode", ["selection", "scene", "regions", "targets"])
def test_recognition_payload_is_not_confused_by_mask_or_old_conversation(mode):
    settings = AISettings()
    payload = build_payload(
        settings,
        "选中目标",
        Recipe().to_dict(),
        [],
        "data:image/jpeg;base64,source",
        mode,
        {
            "selection_image": "data:image/png;base64,mask",
            "recent_conversation": [{"text": "不相关"}],
            "objects": [{"id": "object-1", "name": "人物", "category": "人物"}],
        },
    )
    parts = payload["messages"][1]["content"]
    context = json.loads(parts[0]["text"])
    assert "recent_conversation" not in context and "selection_image" not in context
    assert len([p for p in parts if p["type"] == "image_url"]) == (
        0 if mode == "targets" else 1
    )
    assert '"properties"' not in payload["messages"][0]["content"]


@pytest.mark.parametrize(
    "mutation", ["unknown_id", "conflict", "missing", "too_many", "bad_finish"]
)
def test_target_protocol_rejects_invented_or_ambiguous_objects(mutation):
    data = {
        "status": "selected",
        "summary": "目标",
        "object_ids": ["object-1"],
        "exclude_ids": [],
    }
    if mutation == "unknown_id":
        data["object_ids"] = ["object-999"]
    if mutation == "conflict":
        data["exclude_ids"] = ["object-1"]
    if mutation == "missing":
        data["object_ids"] = []
    if mutation == "too_many":
        data["exclude_ids"] = ["object-2"] * 17
    with pytest.raises(ValueError):
        parse_targets(
            response(data, "length" if mutation == "bad_finish" else "stop"),
            catalog()["objects"],
        )


def test_scene_protocol_hit_priority_and_no_raster_work_on_pointer_move(monkeypatch):
    index = SceneIndex()
    index.set(catalog())
    assert index.hit(0.2, 0.3) == "object-1"
    assert index.hit(0.8, 0.3) == "object-2" and index.hit(0.5, 0.9) == "object-3"
    assert index.hit(-0.1, 0.1) == ""
    monkeypatch.setattr(
        "iphoto.scene.raster_mask",
        lambda *_: pytest.fail("pointer movement must reuse index"),
    )
    for i in range(1000):
        index.hit((i % 100) / 100, 0.3)
    bad = catalog()
    bad["objects"][1]["id"] = "object-1"
    with pytest.raises(ValueError):
        validate_catalog(bad)


def test_scene_cache_is_bounded_and_rebuilds_selection_state():
    index = SceneIndex()
    index.set(catalog())
    index.selected = {"object-1"}
    index.remember("photo-a")
    index.set(None)
    assert index.restore("photo-a") and not index.selected and len(index.rows()) == 3
    for key in ("b", "c", "d"):
        index.remember(key)
    assert not index.restore("photo-a") and len(index._cache) == 3


@pytest.mark.parametrize("mode", ["replace", "add", "subtract", "intersect"])
def test_object_set_operations_keep_exact_protected_pixels(mode):
    objects = catalog()["objects"]
    size = (200, 140)
    base = (
        objects[2]["mask"] if mode in ("subtract", "intersect") else objects[0]["mask"]
    )
    result = combine_masks([objects[1]["mask"]], size, base, mode)
    mask = raster_mask(result, size)
    layer = new_layer()
    layer.update(mask=result, recipe=Recipe(exposure=1).to_dict())
    original = Image.new("RGB", size, (80, 100, 140))
    out = render_layers(original, [layer])
    zeros = np.asarray(mask) == 0
    assert zeros.any() and np.array_equal(
        np.asarray(out)[zeros], np.asarray(original)[zeros]
    )
    if mode == "add":
        assert mask.getpixel((40, 40)) == 255 and mask.getpixel((150, 40)) == 255
    if mode == "subtract":
        assert mask.getpixel((150, 40)) == 0 and mask.getpixel((40, 40)) == 255


def nested_layers():
    base = new_layer("底层", True)
    base["recipe"]["exposure"] = 0.1
    outer = new_layer("外组", True, kind="group")
    outer["opacity"] = 0.65
    inner = new_layer("内组", True, parent_id=outer["id"], kind="group")
    inner["opacity"] = 0.5
    a = new_layer("内调整", True, parent_id=inner["id"])
    a["recipe"]["contrast"] = 25
    b = new_layer("组上层", True, parent_id=outer["id"])
    b["recipe"]["exposure"] = 0.4
    return [base, outer, inner, a, b]


def test_nested_group_opacity_is_applied_once_after_children():
    image = Image.fromarray(
        np.random.default_rng(7).integers(10, 240, (50, 80, 3), dtype=np.uint8)
    )
    layers = nested_layers()
    base = render(image, Recipe(exposure=0.1))
    inner = render(base, Recipe(contrast=25))
    inner = Image.composite(inner, base, Image.new("L", image.size, 128))
    children = render(inner, Recipe(exposure=0.4))
    expected = Image.composite(children, base, Image.new("L", image.size, 166))
    assert np.array_equal(render_layers(image, validate_layers(layers)), expected)
    layers[1]["visible"] = False
    assert np.array_equal(render_layers(image, layers), base)
    assert not next(
        row for row in display_rows(layers) if row["id"] == layers[3]["id"]
    )["effectiveVisible"]


@pytest.mark.parametrize(
    "mutation",
    ["cycle", "missing_parent", "non_group_parent", "too_deep", "group_recipe"],
)
def test_invalid_group_graph_is_rejected(mutation):
    layers = nested_layers()
    if mutation == "cycle":
        layers[1]["parent_id"] = layers[2]["id"]
    if mutation == "missing_parent":
        layers[3]["parent_id"] = "missing"
    if mutation == "non_group_parent":
        layers[3]["parent_id"] = layers[0]["id"]
    if mutation == "group_recipe":
        layers[1]["recipe"]["exposure"] = 0.1
    if mutation == "too_deep":
        layers = []
        parent = ""
        for i in range(5):
            group = new_layer(str(i), True, parent, kind="group")
            layers.append(group)
            parent = group["id"]
    with pytest.raises(ValueError):
        validate_layers(layers)


def test_group_clone_remaps_descendants_and_collapse_only_changes_sidebar():
    layers = nested_layers()
    original_key = composition_key(layers)
    clones, root = duplicate_subtree(layers, layers[1]["id"])
    merged = validate_layers(layers + clones)
    assert descendants(merged, root) == {l["id"] for l in clones}
    assert not ({l["id"] for l in clones} & {l["id"] for l in layers})
    layers[1]["collapsed"] = True
    assert len(display_rows(layers)) == 2 and composition_key(layers) == original_key


def test_nested_cache_reuses_children_on_group_opacity_and_matches_uncached():
    image = Image.fromarray(
        np.random.default_rng(5).integers(0, 256, (64, 96, 3), dtype=np.uint8)
    )
    layers = nested_layers()
    cache = LayerPreviewCache(max_bytes=96 * 64 * 3 * 20)
    assert np.array_equal(cache.render(image, layers), render_layers(image, layers))
    for opacity in (0.4, 0.8):
        layers[1]["opacity"] = opacity
        assert np.array_equal(cache.render(image, layers), render_layers(image, layers))
        assert cache.reused >= 2
    for lid, key, value in [
        (3, "exposure", 0.6),
        (0, "warmth", 12),
        (4, "saturation", 20),
    ]:
        layers[lid]["recipe"][key] = value
        assert np.array_equal(cache.render(image, layers), render_layers(image, layers))
    assert cache.bytes <= cache.max_bytes


def test_project_roundtrip_keeps_hierarchy_scene_and_drafts(tmp_path):
    layers = nested_layers()
    payload = {
        "schema_version": "1.4",
        "source": "source.png",
        "source_sha256": "a" * 64,
        "layers": layers,
        "active_layer": layers[3]["id"],
        "conversation": [],
        "selection_draft": catalog()["objects"][0]["mask"],
        "scene_catalog": catalog(),
    }
    path = tmp_path / "nested.iphoto"
    write_project(path, payload)
    saved = read_project(path)
    assert saved["layers"] == layers and saved["scene_catalog"] == catalog()
    assert saved["selection_draft"] == payload["selection_draft"]
    for version in ("1.2", "1.3"):
        old = {
            **payload,
            "schema_version": version,
            "layers": [new_layer("旧图层", True)],
        }
        old["active_layer"] = old["layers"][0]["id"]
        for k in ("kind", "parent_id", "collapsed"):
            old["layers"][0].pop(k)
        assert validate_project(old)["layers"][0]["parent_id"] == ""


def test_refining_existing_contour_preserves_multicolor_object_interior():
    from PIL import ImageDraw

    image = Image.new("RGB", (400, 300), (35, 70, 130))
    draw = ImageDraw.Draw(image)
    draw.ellipse((60, 35, 340, 265), fill=(210, 140, 60))
    # This blue interior patch belongs to the object even though its color
    # resembles the background. Boundary refinement must not erase it.
    draw.rectangle((150, 110, 250, 190), fill=(35, 70, 130))
    seed = Image.new("L", image.size, 0)
    ImageDraw.Draw(seed).ellipse((60, 35, 340, 265), fill=255)
    result, quality = refine(image, bitmap_mask(seed, "已有对象轮廓"))
    assert quality["method"] == "preserve-interior"
    out = raster_mask(result, image.size)
    assert out.getpixel((200, 150)) == 255 and out.getpixel((10, 10)) == 0


def test_editor_groups_undo_reparent_clone_delete_and_restore(workspace_v14, tmp_path):
    e = workspace_v14
    first = e.activeLayerId
    e.groupLayer()
    outer = e.activeLayerId
    e.groupLayer()
    top = e.activeLayerId
    assert e.activeIsGroup and len(e._layers) == 3
    e.addGlobalLayer()
    added = e.activeLayerId
    assert e.activeParentId == top
    e.moveToGroup(outer)
    assert e.activeParentId == outer
    e.setParameter("exposure", 0.5)
    e.finishGesture()
    e.selectLayer(top)
    e.moveToGroup(outer)
    assert e.activeParentId == ""  # cyclic move rejected
    e.duplicateLayer()
    clone = e.activeLayerId
    assert len(e._layers) == 8 and e.activeIsGroup
    e.deleteLayer()
    assert len(e._layers) == 4
    e.undo()
    assert len(e._layers) == 8
    e.redo()
    assert len(e._layers) == 4
    e.selectLayer(added)
    e.moveOutOfGroup()
    assert e.activeParentId == top
    e._scene.set(catalog())
    e._mark_dirty()
    wait_for(lambda: not e.busy and settled(e))
    path = tmp_path / "workspace.iphoto"
    e.saveProject(str(path))
    e.openProject(str(path))
    wait_for(lambda: not e.busy and settled(e))
    assert len(e.sceneObjects) == 3 and e._layer()["parent_id"] == top


def test_editor_scene_analysis_targets_combination_and_failure_preserve_layers(
    workspace_v14,
    pixel_protocol_stub,
):
    e = workspace_v14
    before = deepcopy(e._layers)
    with mock_api(scene_response()) as (url, requests):
        configure(e.ai, url)
        e.analyzeScene(True)
        wait_for(lambda: not e.busy)
        assert (
            len(e.sceneObjects) == 3 and not e.hasSelectionDraft and e._layers == before
        )
        e.analyzeScene(False)
        assert len(requests) == 1
    with mock_api(target_response()) as (url, requests):
        configure(e.ai, url)
        e.selectByDescription("两个方块都选中")
        wait_for(lambda: not e.busy and settled(e))
        assert e.hasSelectionDraft and e._layers == before
        assert (
            len(requests[0][2]["messages"][1]["content"]) == 1
        )  # no second photo upload
    selected = deepcopy(e._candidate)
    with mock_api(target_response(["unknown"])) as (url, _):
        configure(e.ai, url)
        e.selectByDescription("不存在的对象")
        wait_for(lambda: not e.busy and settled(e))
        assert e._candidate == selected
    e.clearObjectChecks()
    e.checkSceneObject("object-1", True)
    e.combineObjects("subtract")
    assert raster_mask(e._candidate, (200, 140)).getpixel((40, 40)) == 0
    e.undo()
    assert e._candidate == selected
    e.discardSelection()
    e.clickObject([0.8, 0.3], "replace")
    assert e.checkedObjectCount == 1
    e.selectionToLayer()
    assert len(e._layers) == 2
    e.selectLayer(e._layers[0]["id"])
    e.setParameter("warmth", 10)
    e.finishGesture()
    wait_for(lambda: not e.busy and settled(e))
    url = e.maskUrl
    e.setParameter("exposure", 0.2)
    e.finishGesture()
    wait_for(lambda: not e.busy and settled(e))
    assert e.maskUrl == url  # overlay file reused while only recipe changes
