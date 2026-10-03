"""Face identities across local/cloud routes; fixtures do not measure accuracy."""
from copy import deepcopy
import json
from types import SimpleNamespace

from PIL import Image
from PySide6.QtTest import QTest
import pytest

from iphoto.controllers import face_inventory, pixel_selections, worker_bridge
from iphoto.document import empty_mask, raster_mask
from iphoto.scene import SceneIndex
from iphoto.segmentation.classical import bitmap_mask
from test_ai import configure, mock_api, wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_face_detection import completion
from test_scene_object_actions_ui import reveal


def face(lid="cloud-face", left=.2):
    mask = {**empty_mask(), "label": lid, "ops": [{"kind": "polygon", "mode": "add",
            "points": [[left,.2],[left+.2,.2],[left+.2,.4],[left,.4]]}]}
    return {"id": lid, "name": lid, "category": "人脸", "mask_target": "face",
            "mask": mask, "anchor": [left+.1,.3]}


def owner(local=(), objects=()):
    scene = SceneIndex()
    if objects:
        scene.set({"summary": "定位", "objects": list(objects)})
    return SimpleNamespace(_face_hints=list(local), _scene=scene)


def test_common_faces_only_offer_full_faces_with_anchors_and_keep_local_geometry():
    local = face("local-face-1")
    local["skin_crop"] = [.1,.1,.5,.5]
    cheek = {**face("cheek", .6), "mask_target": "face_skin"}
    untyped = {**face("person", .6), "mask_target": "object"}
    no_anchor = face("no-anchor", .6); no_anchor.pop("anchor")
    e = owner([local], [face(), face("other", .6), cheek, untyped, no_anchor])
    before = deepcopy(e._scene.catalog)
    result = face_inventory.current(e)
    assert [f["id"] for f in result] == ["other", "local-face-1"]
    assert result[-1]["skin_crop"] == local["skin_crop"]
    assert "skin_crop" in result[0] and e._scene.catalog == before
    assert face_inventory.current(e) is result  # No repeated raster work on slider updates.
    e._face_hints = []
    assert face_inventory.current(e) is not result
    e._scene.set(None)
    assert face_inventory.current(e) == []


def test_appending_face_preserves_existing_native_masks_checks_hits_and_queued_revision():
    e = owner(objects=[{**face("tree", .65), "mask_target": "object"}])
    scene = e._scene
    mask = bitmap_mask(raster_mask(scene.catalog["objects"][0]["mask"], (600,420)), "tree")
    scene.set_precise("tree", mask, {"resolution": "native"})
    scene.selected = {"tree"}; revision = scene.revision
    previous = deepcopy((scene.precise, scene.pixel_status, scene.selected, scene._hover))
    lid = scene.add_face(face())
    assert lid == "cloud-face" and scene.revision == revision
    assert (scene.precise, scene.pixel_status, scene.selected) == previous[:3]
    assert scene._hover["tree"] == previous[3]["tree"] and scene.hit(.75,.3) == "tree"
    assert scene.hit(.3,.3) == lid
    assert scene.add_face(face("duplicate")) == lid
    assert len(scene.catalog["objects"]) == 2


def test_separate_direct_faces_get_distinct_saved_names_and_masks():
    scene = SceneIndex()
    for left in (.1,.6):
        hint = face(left=left); hint.pop("id"); hint["name"] = "AI 人脸"
        scene.add_face(hint)
    objects = scene.catalog["objects"]
    assert [o["name"] for o in objects] == ["AI 人脸 1", "AI 人脸 2"]
    assert [o["mask"]["label"] for o in objects] == [o["name"] for o in objects]
    before = deepcopy(scene.catalog)
    invalid = face("bad", .4); invalid.pop("anchor")
    with pytest.raises(ValueError, match="定位点"):
        scene.add_face(invalid)
    assert scene.catalog == before


def test_cloud_only_face_shortcuts_use_a_context_crop_and_reuse_the_layer(canvas, pixel_protocol_stub, monkeypatch):  # noqa: F811
    e = canvas.e; e._face_hints = []
    e._scene.set({"summary": "侧脸", "objects": [face()]}); e.changed.emit()
    wait_for(lambda:canvas.find("selectFaceButton").isVisible())
    QTest.qWait(150)  # Let the newly visible rows finish laying out before the mouse click.
    requests = []; original = e._request
    def request(op, **data):
        if op == "segment": requests.append(deepcopy(data))
        return original(op, **data)
    monkeypatch.setattr(e, "_request", request)
    before = deepcopy(e._layers); cursor = e._cursor
    canvas.click("selectFaceButton"); wait_for(lambda:settled(e))
    assert e.hasSelectionDraft and e._layers == before and e._cursor == cursor
    job = requests[-1]["jobs"][0]
    assert job["mask_target"] == "face" and job["skin_crop"][0] < .2 and job["skin_crop"][2] > .4
    e.selection.discard(); wait_for(lambda:settled(e))
    canvas.click("faceRetouch_smooth"); wait_for(lambda:settled(e))
    assert len(e._layers) == len(before)+1 and e.parameters["skin_smoothing"] == 35
    # Mark the fixture's semantic result explicitly; the real parser is in QA.
    e._layer()["mask"].update(semantic_target="face_skin", label="cloud-face · 面部皮肤")
    e._commit(); before = deepcopy(e._layers); lid = e.activeLayerId
    e.selection.clearPick(); wait_for(lambda:settled(e))
    reveal(canvas, "faceRetouch_rosy")
    canvas.click("faceRetouch_rosy"); wait_for(lambda:settled(e) and e.parameters["hsl_orange_saturation"] == 5)
    assert len(requests) == 2 and e.activeLayerId == lid and len(e._layers) == len(before)
    e.undo(); wait_for(lambda:settled(e)); assert e._layers == before


@pytest.mark.parametrize("target", ["face", "face_skin", "object"])
def test_direct_text_only_remembers_a_successful_full_face_and_reopens_it(canvas, monkeypatch, tmp_path, target):  # noqa: F811
    e = canvas.e; e._face_hints = []
    original = e._request
    def request(op, **data):
        if op != "segment": return original(op, **data)
        job = data["jobs"][0]
        mask = bitmap_mask(raster_mask(job["hint"], (600,420)), "routing fixture")
        if target != "object": mask["semantic_target"] = target
        pixel_selections.complete(e, {"items": [{"id": "target", "mask": mask, "quality": {}}]}, data["context"])
    monkeypatch.setattr(e, "_request", request)
    before = deepcopy(e._layers)
    value = {"status": "selected", "summary": "目标", "box": [200,200,400,400],
             "point": [300,300], "mask_target": target}
    with mock_api(completion(value)) as (url, requests):
        configure(e.ai, url)
        canvas.find("selectionDescriptionInput").setProperty("text", "选择完整人脸")
        canvas.click("directSelectionButton")
        wait_for(lambda:not e.ai.busy and e._pending_request is None and settled(e))
        assert len(requests) == 1 and e.hasSelectionDraft and e._layers == before
    assert bool(e.selection.faces) == (target == "face")
    if target == "face":
        assert e._scene.precise["direct-face-1"]["mask"]["semantic_target"] == "face"
        e.selection.discard(); wait_for(lambda:settled(e))
        project = tmp_path/"direct.iphoto"; e.saveProject(str(project))
        # Prevent the explicit routing fixture from handling unrelated precache jobs.
        monkeypatch.setattr(pixel_selections, "precache", lambda *args:None)
        e.openProject(str(project)); wait_for(lambda:settled(e))
        assert e.selection.faces == [{"id": "direct-face-1", "name": "AI 人脸 1"}]


def test_expired_direct_face_cannot_change_candidate_or_inventory(canvas):  # noqa: F811
    e = canvas.e
    hint = face(); mask = bitmap_mask(Image.new("L", (600,420),255), "expired")
    mask["semantic_target"] = "face"
    previous = deepcopy((e._candidate, e._scene.catalog, e._layers))
    with pytest.raises(ValueError, match="照片已更新"):
        pixel_selections.complete(e, {"items": [{"id":"target", "mask":mask, "quality":{}}]},
                                  {"purpose":"hint", "face_hint":hint, "source_sha":"old-photo"})
    assert (e._candidate, e._scene.catalog, e._layers) == previous


@pytest.mark.parametrize("outcome", ["cancelled", "stale", "error"])
def test_cancelled_stale_and_failed_face_workers_never_publish_an_identity(outcome):
    hint = face(); mask = bitmap_mask(Image.new("L", (600,420),255), "face fixture")
    mask["semantic_target"] = "face"
    response = {"id": 9, "op": "segment", "generation": 10, "ok": outcome != "error",
                "error": "fixture prediction failed", "result": {"items": [
                    {"id": "target", "mask": mask, "quality": {}}]}}
    notes = []
    e = SimpleNamespace(process=SimpleNamespace(readAllStandardOutput=lambda:(json.dumps(response)+"\n").encode()),
                        _buffer=b"", _active={"id":9, "op":"segment", "cancelled":outcome=="cancelled",
                            "context":{"purpose":"hint", "face_hint":hint, "source_sha":"photo"}},
                        _generation=11 if outcome=="stale" else 10, _candidate=None, _scene=SceneIndex(),
                        _pending_project=None, _status="working", changed=SimpleNamespace(emit=lambda:None),
                        _pump=lambda:None, _notify=lambda *args:notes.append(args),
                        _message=lambda *args, **kw:notes.append(args))
    worker_bridge._read(e)
    assert e._candidate is None and e._scene.catalog is None and notes
