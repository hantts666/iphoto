"""Planning status obeys worker identity/cancellation and publishes no masks."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from iphoto.controllers import worker_bridge
from iphoto.document import empty_mask


@pytest.mark.parametrize("case", ["valid", "wrong_id", "wrong_op", "wrong_active_op", "stale",
    "old_active", "cancelled", "background", "closing", "wrong_target", "bad_part", "boolean",
    "bad_total", "tile", "bad_phase"])
def test_detail_planning_is_status_only(case):
    progress={"kind":"object","phase":"details_plan","part":1,"total":1}
    if case=="bad_part": progress["part"]=0
    if case=="boolean": progress["part"]=True
    if case=="bad_total": progress["total"]=2
    if case=="tile": progress.update(tile=1,tiles=1)
    if case=="bad_phase": progress["phase"]="details_finished"
    active={"id":7,"op":"open" if case=="wrong_active_op" else "segment",
        "generation":9 if case=="old_active" else 10,
        "jobs":[{"mask_target":"face" if case=="wrong_target" else "object"}],
        "cancelled":case=="cancelled","priority":"low" if case=="background" else "normal"}
    message={"id":8 if case=="wrong_id" else 7,"op":"open" if case=="wrong_op" else "segment",
        "generation":9 if case=="stale" else 10,"progress":progress}
    owner=SimpleNamespace(_pixel_buffer=b"",_pixel_active=active,_generation=10,_closing=case=="closing",
        _status="previous",_layers=[{"id":"kept"}],_candidate=empty_mask(),_warm_ready_sha="unprepared",
        _pixel_process=SimpleNamespace(readAllStandardOutput=lambda:(json.dumps(message)+"\n").encode()),
        changed=SimpleNamespace(emit=lambda:None),_pump_pixel=lambda:pytest.fail("Progress released task"))
    snapshot=deepcopy((owner._layers,owner._candidate,owner._generation))
    worker_bridge._pixel_read(owner)
    assert owner._pixel_active is active and owner._warm_ready_sha=="unprepared"
    assert snapshot==(owner._layers,owner._candidate,owner._generation)
    assert (owner._status!="previous")== (case=="valid")
    if case=="valid": assert "检查细节范围与计算预算" in owner._status and "取消" in owner._status
