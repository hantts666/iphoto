"""Semantic policy and transaction guards; fixtures are not model accuracy tests."""

from copy import deepcopy
from types import SimpleNamespace
import json

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_tasks import parse_regions, REGION_SCHEMA
from iphoto.controllers import pixel_selections
from iphoto.document import empty_mask, raster_mask
from iphoto.engine import Recipe, file_hash
from iphoto.segmentation import face_models, face_skin, service
from iphoto.segmentation.pixel_worker import load_native


def region(target="face_skin", name="面部皮肤"):
    return {"name": name, "reason": "只磨皮肤", "mask_target": target,
            "box": [100, 100, 900, 900], "point": [300, 300],
            "recipe": Recipe(skin_smoothing=25).to_dict()}


def response(regions):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "status": "planned", "summary": "建立局部层", "regions": regions
    }, ensure_ascii=False)}}]}


def mask():
    return {**empty_mask(), "label": "目标", "ops": [
        {"kind": "rect", "mode": "add", "points": [[.1, .1], [.9, .9]]}
    ]}


def labels():
    result = np.zeros((512, 512), np.uint8)
    result[60:450, 60:340] = 1
    result[200:240, 200:220] = 10  # nose, connected to the target face
    result[70:200, 400:480] = 1   # another face, intentionally disconnected
    result[110:125, 430:450] = 10
    return result


@pytest.mark.parametrize("protected", face_skin.PROTECTED_CLASSES)
def test_protected_features_and_other_face_stay_exactly_zero(protected):
    semantic = labels()
    semantic[310:350, 210:280] = protected
    image = Image.new("RGB", (1024, 768), "gray")
    original = image.tobytes()
    result, quality = face_skin.segment(image, mask(), [[.3, .3, 1]], crop=[0, 0, 1, 1],
                                        engine=SimpleNamespace(predict=lambda _: semantic))
    alpha = raster_mask(result, image.size)
    assert alpha.getpixel((240*2, round(330*1.5))) == 0
    assert alpha.getpixel((440*2, round(140*1.5))) == 0
    assert alpha.getpixel((210*2, round(215*1.5))) == 255
    assert alpha.getpixel((150*2, round(150*1.5))) == 255
    assert image.tobytes() == original
    assert quality["semantic_target"] == "face_skin"
    assert (result["bitmap"]["width"], result["bitmap"]["height"]) == image.size
    assert result["bitmap"]["sampling"] == "alpha"


@pytest.mark.parametrize("invalid", [[], [[.3, .3, 0]], [[.3, .3, 1], [.4, .4, 1]], [[.99, .99, 1]]])
def test_invalid_or_non_skin_anchor_fails_without_a_mask(invalid):
    with pytest.raises(ValueError):
        face_skin.segment(Image.new("RGB", (800, 600)), mask(), invalid, crop=[0, 0, 1, 1],
                          engine=SimpleNamespace(predict=lambda _: labels()))


def test_no_facial_landmark_rejects_skin_like_body_region():
    semantic = labels()
    semantic[semantic == 10] = 1
    with pytest.raises(ValueError, match="未可靠识别"):
        face_skin.segment(Image.new("RGB", (800, 600)), mask(), [[.3, .3, 1]], crop=[0, 0, 1, 1],
                          engine=SimpleNamespace(predict=lambda _: semantic))


def test_full_resolution_mask_preserves_crop_mapping_and_protected_zero():
    result, _ = face_skin.segment(Image.new("RGB", (2600, 3300)), mask(), [[.35, .4, 1]],
                                 crop=[.2, .25, .7, .85], engine=SimpleNamespace(predict=lambda _: labels()))
    alpha = raster_mask(result, (2600, 3300))
    assert result["bitmap"]["height"] == 3300
    assert alpha.getbbox()[0] >= 520 and alpha.getbbox()[1] >= 825
    assert alpha.getpixel((900, 1300)) > 0
    assert alpha.getpixel((1900, 1300)) == 0


def test_typed_target_roundtrips_and_rejects_unknown_capability():
    assert "mask_target" in REGION_SCHEMA["properties"]["regions"]["items"]["required"]
    assert parse_regions(response([region(name="目标 A")]))["regions"][0]["mask_target"] == "face_skin"
    assert parse_regions(response([region("object")]))["regions"][0]["mask_target"] == "object"
    with pytest.raises(ValueError, match="目标类型"):
        parse_regions(response([region("run_python")]))


def test_explicit_null_target_is_not_treated_as_a_legacy_plan():
    with pytest.raises(ValueError, match="目标类型"):
        parse_regions(response([region(None)]))


@pytest.mark.parametrize("name,target", [("面部皮肤", "face_skin"), ("脸颊", "face_skin"),
                                         ("手臂皮肤", "object"), ("人物", "object"),
                                         ("面部与手臂", "object")])
def test_legacy_plans_only_infer_unambiguous_faces(name, target):
    old = region(name=name)
    old.pop("mask_target")
    assert parse_regions(response([old]))["regions"][0]["mask_target"] == target


def test_mixed_jobs_fail_atomically_when_facial_result_is_unreliable(monkeypatch):
    image = Image.new("RGB", (100, 100))
    calls = []
    monkeypatch.setattr(service, "segment", lambda *args: (mask(), {"fixture": True}))
    def reject(*args, **kwargs):
        calls.append(True)
        raise ValueError("没有人脸")
    monkeypatch.setattr(face_skin, "segment", reject)
    with pytest.raises(ValueError, match="没有人脸"):
        service.segment_jobs(image, [
            {"id": "object", "hint": mask(), "points": []},
            {"id": "face", "mask_target": "face_skin", "hint": mask(), "points": [[.3, .3, 1]]},
        ])
    assert calls == [True]


def owner():
    return SimpleNamespace(_sha="photo", _pending_request=None, _warm_sha="", _status="",
                           changed=SimpleNamespace(emit=lambda: None))


def test_face_only_job_does_not_need_sam_and_stops_its_warmup(monkeypatch):
    o = owner()
    requests, stopped = [], []
    o._request = lambda op, **kw: requests.append((op, kw))
    o._stop_warm = lambda: stopped.append(True)
    monkeypatch.setattr(face_models, "available", lambda: True)
    monkeypatch.setattr(pixel_selections, "ready", lambda _: pytest.fail("Face parsing must not load SAM"))
    assert pixel_selections.start(o, [{"id": "0", "mask_target": "face_skin"}], {"purpose": "regions"})
    assert stopped == [True] and len(requests) == 1
    assert "保护眉眼和嘴唇" in o._status


def test_missing_facial_model_does_not_start_or_fall_back_to_object_mask(monkeypatch):
    o = owner()
    notices = []
    o._notify = lambda *args: notices.append(args)
    o._request = lambda *args, **kw: pytest.fail("Missing model must not dispatch a job")
    monkeypatch.setattr(face_models, "available", lambda: False)
    assert not pixel_selections.start(o, [{"id": "0", "mask_target": "face_skin"}], {"purpose": "regions"})
    assert "setup_face_parsing.py" in notices[0][0]


def test_region_job_retains_semantic_type_and_original_face_context(monkeypatch):
    o = owner()
    o._layers = []
    planned = [{**parse_regions(response([region()]))["regions"][0], "skin_crop": [.1, .2, .8, .9]}]
    before = deepcopy(planned)
    requests = []
    monkeypatch.setattr(pixel_selections, "start", lambda *args: requests.append(args) or True)
    assert pixel_selections.select_regions(o, planned, "面部", True)
    assert requests[0][1][0]["mask_target"] == "face_skin"
    assert requests[0][1][0]["skin_crop"] == planned[0]["skin_crop"]
    assert planned == before


def test_native_source_hash_and_orientation_are_verified(tmp_path):
    photo = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (100, 200))
    exif = Image.Exif(); exif[274] = 6
    image.save(photo, exif=exif)
    request = {"source_path": str(photo), "source_sha": file_hash(photo)}
    assert load_native(request).size == (200, 100)
    request["source_sha"] = "stale"
    with pytest.raises(ValueError, match="源文件已变化"):
        load_native(request)


def test_corrupt_model_with_expected_size_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(face_models, "MODEL_DIR", tmp_path)
    with (tmp_path / face_models.NAME).open("wb") as output:
        output.truncate(face_models.SIZE)
    assert face_models.available()
    with pytest.raises(ValueError, match="校验失败"):
        face_models.verified_path()
