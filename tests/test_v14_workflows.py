"""Multi-request and failure paths that are easy to miss in single-request tests."""

from copy import deepcopy
import json
from test_ai import mock_api, configure, wait_for
from test_editor import settled
from test_v14 import catalog, response, target_response, workspace_v14 as _workspace_v14

workspace_v14 = _workspace_v14


def test_first_description_locates_without_analysis_then_reuses_an_explicit_catalog(workspace_v14,pixel_protocol_stub):
    e = workspace_v14
    before = deepcopy(e._layers)

    def reply(payload):
        context = json.loads(payload["messages"][1]["content"][0]["text"])
        return target_response() if context.get("objects") else response({
            "status": "selected", "summary": "方块", "box": [100,100,900,700],
            "point": [200,300], "mask_target": "object",
        })

    with mock_api(reply) as (url, requests):
        configure(e.ai, url)
        e.selectByDescription("选择所有方块")
        wait_for(lambda: e.hasSelectionDraft and not e.busy and settled(e))
        assert len(requests) == 1 and e._layers == before and not e.sceneObjects
        assert len(requests[0][2]["messages"][1]["content"]) == 2
        e._scene.set(catalog())
        e.selectByDescription("同样选择两个方块")
        wait_for(lambda: not e.busy and settled(e))
        assert len(requests) == 2 and e.checkedObjectCount == 2
        assert len(requests[1][2]["messages"][1]["content"]) == 1
        e.discardSelection()
        e.analyzeScene(False)
        assert len(requests) == 2


def test_cancel_first_description_keeps_existing_mask_without_scene_followup(workspace_v14):
    e = workspace_v14
    e.drawDraft("rect", "replace", [[0.1, 0.1], [0.4, 0.6]], 0.02)
    wait_for(lambda: settled(e))
    before = deepcopy(e._candidate)
    with mock_api(response({"status": "unsupported", "summary": "未定位"}), delay=2) as (url, requests):
        configure(e.ai, url)
        e.selectByDescription("选择方块")
        wait_for(lambda: bool(requests))
        e.ai.cancel()
        wait_for(lambda: not e.busy)
        assert len(requests) == 1 and not e._scene_followup
        assert e._candidate == before and not e.sceneObjects
