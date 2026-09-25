"""Multi-request and failure paths that are easy to miss in single-request tests."""

from copy import deepcopy
from test_ai import mock_api, configure, wait_for
from test_editor import settled
from test_v14 import scene_response, target_response, workspace_v14


def test_first_description_analyzes_once_then_uses_text_only_requests(workspace_v14,pixel_protocol_stub):
    e = workspace_v14
    before = deepcopy(e._layers)

    def reply(payload):
        import json

        context = json.loads(payload["messages"][1]["content"][0]["text"])
        return target_response() if context.get("objects") else scene_response()

    with mock_api(reply) as (url, requests):
        configure(e.ai, url)
        e.selectByDescription("选择所有方块")
        wait_for(lambda: e.hasSelectionDraft and not e.busy and settled(e))
        assert len(requests) == 2 and e._layers == before
        assert len(requests[0][2]["messages"][1]["content"]) == 2
        assert len(requests[1][2]["messages"][1]["content"]) == 1
        e.selectByDescription("同样选择两个方块")
        wait_for(lambda: not e.busy and settled(e))
        assert len(requests) == 3 and e.checkedObjectCount == 2
        e.discardSelection()
        e.analyzeScene(False)
        assert len(requests) == 3


def test_cancel_first_scene_drops_followup_and_keeps_existing_mask(workspace_v14):
    e = workspace_v14
    e.drawDraft("rect", "replace", [[0.1, 0.1], [0.4, 0.6]], 0.02)
    wait_for(lambda: settled(e))
    before = deepcopy(e._candidate)
    with mock_api(scene_response(), delay=2) as (url, requests):
        configure(e.ai, url)
        e.selectByDescription("选择方块")
        wait_for(lambda: bool(requests))
        e.ai.cancel()
        wait_for(lambda: not e.busy)
        assert len(requests) == 1 and not e._scene_followup
        assert e._candidate == before and not e.sceneObjects
