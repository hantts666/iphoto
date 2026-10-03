"""Multipart transaction and crop geometry; fixtures are not skin accuracy evidence."""

from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.ai_tasks import parse_regions
from iphoto.controllers import pixel_selections
from iphoto.controllers import worker_bridge
from iphoto.document import empty_mask, raster_mask
from iphoto.engine import Recipe
from iphoto.segmentation import body_skin, service
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_editor import settled


PARTS = [{"box": [150, 150, 340, 800], "point": [210, 400]},
         {"box": [580, 210, 870, 720], "point": [700, 400]}]


def region(parts=None):
    return {"name": "手臂皮肤", "reason": "左右分别定位并合并", "mask_target": "body_skin",
            "parts": deepcopy(PARTS if parts is None else parts),
            "box": [100, 100, 900, 900], "point": [210, 400],
            "recipe": Recipe(skin_smoothing=25).to_dict()}


def parse(item):
    return parse_regions({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "status": "planned", "summary": "分开定位，同层处理", "regions": [item]
    }, ensure_ascii=False)}}]})["regions"][0]


def jobs(item=None):
    planned = parse(item or region())
    return [{"hint": p["mask"], "points": [[*p["anchor"], 1]]} for p in planned["parts"]]


class TargetFixture:
    def predict(self, image, coords, labels):
        hard = np.asarray(image)[:, :, 0] > 128
        return np.where(hard[None], 3., -3.).astype(np.float32), np.array([1.], np.float32), {"fixture": True}


def image(size=(800, 600)):
    result = Image.new("RGB", size, (25, 45, 60))
    draw = ImageDraw.Draw(result)
    draw.rectangle((round(size[0]*.15), round(size[1]*.15), round(size[0]*.34), round(size[1]*.8)), fill=(230, 120, 90))
    draw.rectangle((round(size[0]*.58), round(size[1]*.21), round(size[0]*.87), round(size[1]*.72)), fill=(230, 120, 90))
    return result


def test_independent_parts_merge_once_and_preserve_source_and_gap():
    source = image()
    original = source.tobytes()
    mask, quality = body_skin.segment(source, jobs(), engine=TargetFixture())
    alpha = raster_mask(mask, source.size)
    assert alpha.getpixel((170, 240)) == alpha.getpixel((560, 240)) == 255
    assert not alpha.crop((320, 0, 430, 600)).getbbox()
    assert source.tobytes() == original and quality["part_count"] == 2
    # Duplicate/overlapping local pieces do not sum alpha or create layers.
    overlapping, _ = body_skin.segment(source, [jobs()[0], jobs()[0]], engine=TargetFixture())
    single, _ = body_skin.segment(source, [jobs()[0]], engine=TargetFixture())
    assert raster_mask(overlapping, source.size).tobytes() == raster_mask(single, source.size).tobytes()


def test_optional_generic_matte_does_not_replace_the_body_part_route(monkeypatch):
    from iphoto.matting import models,neural
    from iphoto.segmentation import detail
    monkeypatch.setattr(models,"available",lambda:True)
    monkeypatch.setattr(neural,"refine",lambda *args,**kwargs:pytest.fail("Body part passed to generic matting"))
    monkeypatch.setattr(detail,"recover",lambda *args,**kwargs:pytest.fail("Body part passed to topology recovery"))
    mask,quality = body_skin.segment(image(),jobs(),engine=TargetFixture())
    assert quality["part_count"] == 2 and raster_mask(mask,image().size).getbbox()
    assert all("original_matting" not in part for part in quality["parts"])


def test_large_source_keeps_native_mask_size_and_local_model_budget():
    source = image((2600, 3300))
    mask, quality = body_skin.segment(source, jobs(), engine=TargetFixture())
    assert (mask["bitmap"]["width"], mask["bitmap"]["height"]) == source.size
    assert mask["bitmap"]["sampling"] == "alpha"
    assert all(max(q["crop_size"]) <= 1600 for q in quality["parts"])
    assert raster_mask(mask, source.size).getpixel((550, 1300)) == 255


@pytest.mark.parametrize("parts", [None, [], [{}]*5, "not-a-list"])
def test_invalid_job_count_is_rejected(parts):
    with pytest.raises(ValueError):
        body_skin.segment(image(), parts, engine=TargetFixture())


@pytest.mark.parametrize("points", [[], [[.21,.4,0]], [[.21,.4,1],[.22,.4,1]], [[.99,.99,1]]])
def test_invalid_part_points_reject_the_whole_mask(points):
    parts = jobs()
    parts[1]["points"] = points
    with pytest.raises(ValueError):
        body_skin.segment(image(), parts, engine=TargetFixture())


def test_second_inference_failure_does_not_return_a_partial_mask():
    class FailSecond(TargetFixture):
        count = 0
        def predict(self, *args):
            self.count += 1
            if self.count == 2:
                raise ValueError("第二部位未识别")
            return super().predict(*args)
    with pytest.raises(ValueError, match="第二部位"):
        body_skin.segment(image(), jobs(), engine=FailSecond())


@pytest.mark.parametrize("change", [
    {"mask_target": "face_skin"}, {"mask_target": "object"}, {"parts": None},
    {"parts": PARTS*3}, {"parts": [{"box": [150,150,340,800], "point": [990,990]}]},
    {"parts": [{"box": [-1,150,340,800], "point": [210,400]}]},
    {"parts": [{"box": [50,150,340,800], "point": [210,400]}]},
    {"parts": [{"box": [150,150,340,800], "point": [210,400], "code": "x"}]},
])
def test_invalid_or_out_of_scope_parts_are_rejected(change):
    with pytest.raises(ValueError):
        parse({**region(), **change})


def test_body_target_without_a_skin_anchor_is_rejected_before_inference():
    item = region([])
    item.pop("box"); item.pop("point")
    item["polygons"] = [[[100,100],[900,100],[900,900],[100,900]]]
    with pytest.raises(ValueError, match="皮肤内部点"):
        parse(item)


def test_controller_preserves_parts_and_does_not_mark_them_as_generic_cached_masks(monkeypatch):
    captured = []
    owner = SimpleNamespace(_layers=[], _pending_request=None)
    planned = parse(region())
    monkeypatch.setattr(pixel_selections, "start", lambda *args: captured.append(args) or True)
    assert pixel_selections.select_regions(owner, [planned], "合并", True)
    job = captured[0][1][0]
    assert job["mask_target"] == "body_skin" and len(job["parts"]) == 2
    assert [p["points"][0][:2] for p in job["parts"]] == [p["anchor"] for p in planned["parts"]]


def test_body_dispatch_receives_the_native_source(monkeypatch):
    proxy, source = image(), image((2600,3300))
    calls = []
    monkeypatch.setattr(body_skin, "segment", lambda *args, **kwargs: (calls.append(args) or empty_mask(), {"fixture": True}))
    result = service.segment_jobs(proxy, [{"id": "0", "mask_target": "body_skin", "parts": jobs()}], source=source)
    assert calls[0][0] is source and len(calls[0][1]) == 2 and len(result["items"]) == 1


def test_body_only_task_stops_whole_photo_warmup_and_requires_sam(monkeypatch):
    owner = SimpleNamespace(_pending_request=None, _sha="photo", _warm_sha="photo", _status="",
                            changed=SimpleNamespace(emit=lambda: None))
    stopped, requested = [], []
    owner._stop_warm = lambda: stopped.append(True)
    owner._request = lambda *args, **kwargs: requested.append((args, kwargs))
    monkeypatch.setattr(pixel_selections, "ready", lambda _: True)
    assert pixel_selections.start(owner, [{"id":"0", "mask_target":"body_skin"}], {"purpose":"regions"})
    assert stopped == [True] and len(requested) == 1 and "原图细节" in owner._status
    monkeypatch.setattr(pixel_selections, "ready", lambda _: False)
    assert not pixel_selections.start(owner, [{"id":"0", "mask_target":"body_skin"}], {"purpose":"regions"})
    assert len(stopped) == len(requested) == 1


@pytest.mark.parametrize("case", ["current", "stale", "cancelled", "generic", "bad_total", "bad_part", "boolean"])
def test_progress_cannot_publish_or_release_a_partial_body_job(case):
    progress = {"kind":"body", "part":2, "total":2}
    if case == "bad_total": progress["total"] = 5
    if case == "bad_part": progress["part"] = 0
    if case == "boolean": progress["part"] = True
    active = {"id":7,"generation":10,"op":"segment","jobs":[{"mask_target":"object" if case == "generic" else "body_skin"}],
              "cancelled":case == "cancelled", "context":{"purpose":"regions","auto_apply":True}}
    line = json.dumps({"id":7,"generation":9 if case == "stale" else 10,"progress":progress})+"\n"
    layers = [{"id":"original"}]
    owner = SimpleNamespace(_pixel_buffer=b"",_pixel_active=active,_generation=10,_status="original status",_layers=layers,
                            _warm_ready_sha="unprepared",_pixel_process=SimpleNamespace(readAllStandardOutput=lambda:line.encode()),
                            changed=SimpleNamespace(emit=lambda:None),_pump_pixel=lambda:pytest.fail("Progress cannot dispatch another job"))
    worker_bridge._pixel_read(owner)
    assert owner._pixel_active is active and owner._layers is layers and owner._warm_ready_sha=="unprepared"
    assert ("2/2" in owner._status) == (case == "current")


@pytest.mark.parametrize("outcome", ["applied", "unsupported", "cancelled"])
def test_each_body_part_is_grounded_before_atomic_layer_application(qt_app, ai_store, tmp_path, pixel_protocol_stub, outcome):
    photo = tmp_path / "parts.png"
    image().save(photo)
    editor = Editor(ai_store=ai_store)
    before = None
    count, crops = 0, []
    def reply(payload):
        nonlocal count
        sent = json.loads(payload["messages"][1]["content"][0]["text"])
        if sent["mode"] == "auto":
            assert sent["body_skin_available"] is True
            return auto_response(regions=[region()])
        count += 1
        crops.append(payload["messages"][1]["content"][1]["image_url"]["url"])
        if count == 2 and outcome == "cancelled":
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"status":"unsupported","summary":"停止","box":[],"point":[]})}}]}
        result = {"status":"unsupported","summary":"第二部位被遮挡","box":[],"point":[]} if count == 2 and outcome == "unsupported" else {
            "status":"selected","summary":"定位当前单独部位","box":[200,200,800,800],"point":[500,500]}
        return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(result)}}]}
    try:
        editor.openImage(str(photo)); wait_for(lambda:editor.hasImage and settled(editor))
        before, cursor = deepcopy(editor._layers), editor._cursor
        with mock_api(reply) as (url, requests):
            configure(editor.ai, url)
            assert editor.sendMessage("左右手臂同层轻磨皮", "auto")
            if outcome == "cancelled":
                wait_for(lambda:count >= 1 and (editor._pending_request or {}).get("skin_grounding",{}).get("active",{}).get("part") == 1)
                editor.ai.cancel()
                wait_for(lambda:not editor.busy)
            else:
                wait_for(lambda:settled(editor) and (len(editor._layers)==2 or editor.conversation[-1]["state"]=="unsupported"))
            assert len(crops)>=1
            if outcome == "applied":
                assert count == 2 and len(requests)==3 and crops[0]!=crops[1]
                assert editor._cursor==cursor+1 and len(editor._layers)==2
                assert editor.parameters["skin_smoothing"]==25
                editor.undo();wait_for(lambda:settled(editor))
            assert editor._layers==before and not editor.hasRegionDraft
    finally:
        editor.close()
