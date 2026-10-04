"""One description route: exact catalog objects or fresh image localization.

The HTTP and transaction paths are real; bitmap delivery is a protocol fixture,
so these tests do not make segmentation accuracy claims.
"""

from copy import deepcopy
import json

import pytest
from PySide6.QtTest import QTest

from iphoto.controllers import conversation, pixel_selections
from iphoto.document import raster_mask
from iphoto.segmentation.classical import bitmap_mask
from iphoto.scene import parse_targets
from test_ai import configure, mock_api, wait_for
from test_editor import settled
from test_v14 import catalog, response, target_response, workspace_v14 as _workspace_v14

workspace_v14 = _workspace_v14


def local_route(**changes):
    return response({"status": "locate", "summary": "清单没有对应局部目标",
                     "object_ids": [], "exclude_ids": [], **changes})


def localized(status="selected"):
    return response({"status": status, "summary": "图中定位结果", "mask_target": "object",
                     "box": [100,100,400,700] if status == "selected" else [],
                     "point": [200,300] if status == "selected" else []})


@pytest.mark.parametrize("changes", [
    {"object_ids": ["object-1"]}, {"exclude_ids": ["object-2"]},
    {"object_ids": ["invented"]}, {"summary": ""}, {"status": "retry"},
])
def test_localization_route_rejects_object_composition_and_invalid_contract(changes):
    with pytest.raises(ValueError):
        parse_targets(local_route(**changes), catalog()["objects"])


def test_localization_route_is_valid_without_inventing_catalog_ids():
    result = parse_targets(local_route(), catalog()["objects"])
    assert result["status"] == "locate" and not result["object_ids"] and not result["exclude_ids"]


@pytest.mark.parametrize("intent", ["只选头发", "只选纱布", "只选脸颊", "选择清单里没有的目标"])
@pytest.mark.parametrize("current_layer", [False, True])
def test_catalog_missing_part_continues_one_request_without_coarse_candidate(
    workspace_v14, pixel_protocol_stub, monkeypatch, intent, current_layer,
):
    e = workspace_v14
    e._scene.set(catalog())
    if current_layer:
        e.beginSelection("current")
    e.drawDraft("rect", "replace", [[.6,.6],[.9,.9]], 0)
    wait_for(lambda: settled(e))
    before = deepcopy((e._candidate,e._layers,e._cursor,e._selection_target_id))
    intermediate = []
    statuses = []
    e.changed.connect(lambda: statuses.append(e.status))

    def reply(payload):
        context = json.loads(payload["messages"][1]["content"][0]["text"])
        if "objects" in context:
            return local_route()
        intermediate.append(deepcopy((e._candidate,e._layers,e._cursor,e._selection_target_id)))
        assert context["request"] == intent and context["mode"] == "selection"
        return localized()

    monkeypatch.setattr(pixel_selections, "select_objects", lambda *_a, **_k: pytest.fail("never compose a parent for a missing part"))
    with mock_api(reply) as (url, requests):
        configure(e.ai, url)
        assert e.selectByDescription(intent)
        wait_for(lambda: not e.busy and e._pending_request is None and settled(e))
        assert len(requests) == 2 and intermediate == [before]
        assert len(requests[0][2]["messages"][1]["content"]) == 1
        assert len(requests[1][2]["messages"][1]["content"]) == 2
    assert e.hasSelectionDraft and e._candidate != before[0]
    assert (e._layers,e._cursor,e._selection_target_id) == before[1:]
    assert e._selection_target_id == (e._selected if current_layer else "")
    assert [m["role"] for m in e._conversation] == ["user","assistant"]
    assert e._conversation[0]["text"] == intent and e._conversation[-1]["state"] == "draft"
    assert "AI 正在图中定位具体目标，原选区保留…" in statuses


@pytest.mark.parametrize("outcome", ["cancel", "unsupported", "protocol_error", "http_error"])
def test_second_stage_failure_preserves_previous_range_and_document(workspace_v14, monkeypatch, outcome):
    e = workspace_v14
    e._scene.set(catalog())
    e.drawDraft("rect", "replace", [[.2,.2],[.7,.7]], 0)
    wait_for(lambda: settled(e))
    before = deepcopy((e._candidate,e._layers,e._cursor,e._draft_history,e._selection_target_id))
    calls = []
    monkeypatch.setattr(pixel_selections, "select_hint", lambda *_a, **_k: calls.append(True))
    second = localized("unsupported") if outcome == "unsupported" else response({"unexpected": "data"})
    with mock_api(second, status=401 if outcome == "http_error" else 200, delay=2 if outcome == "cancel" else 0) as (second_url, second_requests):
        with mock_api(local_route()) as (url, requests):
            configure(e.ai, url)
            original = e.ai.plan
            def plan(*args, **kwargs):
                if args[5] == "selection":
                    configure(e.ai, second_url)
                return original(*args, **kwargs)
            monkeypatch.setattr(e.ai, "plan", plan)
            e.selectByDescription("头发")
            if outcome == "cancel":
                wait_for(lambda: bool(second_requests) and e.ai.busy)
                e.ai.cancel()
            wait_for(lambda: not e.busy and e._pending_request is None and settled(e))
            assert len(requests) == 1 and 1 <= len(second_requests) <= 2
            QTest.qWait(50)
    assert not calls and before == (e._candidate,e._layers,e._cursor,e._draft_history,e._selection_target_id)
    assert sum(m["role"] == "user" for m in e._conversation) == 1


@pytest.mark.parametrize("change", ["generation", "layer", "document", "cancelled", "closing"])
def test_late_catalog_route_cannot_start_a_new_request(workspace_v14, monkeypatch, change):
    e = workspace_v14
    e._pending_request = {"mode": "targets", "text": "头发", "layer_id": e._selected,
                          "binding": e._document_signature()}
    generation = e._generation
    before = deepcopy((e._candidate,e._layers,e._cursor))
    if change == "generation": e._generation += 1
    elif change == "layer": e._pending_request["layer_id"] = "missing"
    elif change == "document": e._pending_request["binding"] = "stale"
    elif change == "cancelled": e._pending_request = None
    else: e._closing = True
    monkeypatch.setattr(e.ai, "plan", lambda *_a, **_k: pytest.fail("stale route must not start HTTP"))
    conversation._cloud_plan(e, {**parse_targets(local_route(), catalog()["objects"]), "mode": "targets"}, generation)
    assert before == (e._candidate,e._layers,e._cursor)


def test_exact_catalog_choice_stays_one_text_only_request(workspace_v14, pixel_protocol_stub):
    e = workspace_v14
    e._scene.set(catalog())
    before = deepcopy(e._layers)
    with mock_api(target_response()) as (url, requests):
        configure(e.ai,url)
        e.selectByDescription("两个完整方块")
        wait_for(lambda: not e.busy and e._pending_request is None and settled(e))
        assert len(requests) == 1 and len(requests[0][2]["messages"][1]["content"]) == 1
    assert e._layers == before and e.checkedObjectCount == 2 and e.hasSelectionDraft


def test_exact_cached_object_reuses_its_bitmap_without_new_model_work(workspace_v14, monkeypatch):
    e = workspace_v14
    e._scene.set(catalog())
    obj = e._scene.catalog["objects"][0]
    cached = bitmap_mask(raster_mask(obj["mask"],(200,140)),"cached object")
    e._scene.set_precise(obj["id"],cached,{"predicted_iou":.99,"elapsed_ms":17,"warnings":[]})
    revision=e._scene.revision
    precise=deepcopy(e._scene.precise)
    monkeypatch.setattr(pixel_selections,"start",lambda *_a, **_k: pytest.fail("exact cached target must not restart the model"))
    with mock_api(target_response([obj["id"]])) as (url,requests):
        configure(e.ai,url)
        e.selectByDescription("完整的左侧方块")
        wait_for(lambda: not e.busy and e._pending_request is None and settled(e))
        assert len(requests)==1
    assert e._candidate["bitmap"]==cached["bitmap"] and len(e._layers)==1
    assert e._scene.revision==revision and e._scene.precise==precise


def test_localization_start_failure_releases_request_and_preserves_range(workspace_v14, monkeypatch):
    e=workspace_v14
    e.drawDraft("rect","replace",[[.2,.2],[.7,.7]],0)
    wait_for(lambda:settled(e))
    before=deepcopy((e._candidate,e._layers,e._cursor,e._draft_history))
    e._pending_request={"mode":"targets","text":"头发","layer_id":e._selected,
                        "binding":e._document_signature()}
    monkeypatch.setattr(e.ai,"plan",lambda *_a, **_k:False)
    conversation._cloud_plan(e,{**parse_targets(local_route(),catalog()["objects"]),"mode":"targets"},e._generation)
    assert e._pending_request is None
    assert before==(e._candidate,e._layers,e._cursor,e._draft_history)
    assert e._conversation[-1]["state"]=="failed"
