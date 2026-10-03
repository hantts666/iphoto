"""AI matting progress cannot publish a partial result or release a live task."""
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace

from PIL import Image
import pytest

from iphoto.controllers import matte_process, matting
from iphoto.document import empty_mask
from iphoto.paths import ROOT


@pytest.mark.parametrize("case", ["prepare", "tile", "wrong_id", "wrong_op", "stale_reply", "old_active", "wrong_source", "closing", "cancelled", "classic", "zero", "boolean", "too_many", "missing_total", "wrong_phase", "non_dict", "duplicate", "different_total"])
def test_progress_preserves_task_mask_layers_and_generation(case):
    progress = {"phase": "details", "tile": 2, "tiles": 3}
    if case == "prepare": progress = {"phase": "prepare"}
    if case == "zero": progress["tile"] = 0
    if case == "boolean": progress["tile"] = True
    if case == "too_many": progress["tiles"] = 129
    if case == "missing_total": del progress["tiles"]
    if case == "wrong_phase": progress["phase"] = "complete"
    if case == "non_dict": progress = []
    active = {"id": 7, "op": "matte", "generation": 9 if case == "old_active" else 10,
              "source_sha": "old" if case == "wrong_source" else "photo", "method": "classic" if case == "classic" else "neural"}
    if case in ("duplicate", "different_total"):
        active.update(detail_tile=2 if case == "duplicate" else 1, detail_tiles=4 if case == "different_total" else 3)
    response = {"id": 8 if case == "wrong_id" else 7, "op": "segment" if case == "wrong_op" else "matte",
                "generation": 9 if case == "stale_reply" else 10, "progress": progress}
    owner = SimpleNamespace(_matte_active=active, _matte_buffer=b"", _generation=10, _sha="photo",
                            _closing=case == "closing", _matte_aborting=case == "cancelled", _status="previous",
                            _candidate=empty_mask(True), _layers=[{"id":"unchanged"}],
                            _matte_process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(response)+"\n").encode()),
                            changed=SimpleNamespace(emit=lambda: None))
    snapshot = deepcopy((owner._candidate, owner._layers, owner._generation))
    matte_process.read(owner)
    assert owner._matte_active is active
    assert snapshot == (owner._candidate, owner._layers, owner._generation)
    assert (owner._status != "previous") == (case in ("prepare", "tile"))
    if case == "tile": assert "2/3 块" in owner._status and "取消" in owner._status


def test_all_progress_lines_precede_the_single_final_atomic_result(monkeypatch):
    active = {"id":7,"op":"matte","generation":10,"source_sha":"photo","method":"neural","points":[[.5,.5,1]]}
    messages = [{"progress":{"phase":"details","tile":i,"tiles":2}} for i in (1,2)] + [{"ok":True,"result":{"fixture":"final"}}]
    output = "".join(json.dumps({"id":7,"op":"matte","generation":10,**message})+"\n" for message in messages)
    calls = []
    monkeypatch.setattr(matting,"complete",lambda owner,result,**kwargs:calls.append((result,kwargs)))
    owner = SimpleNamespace(_matte_active=active,_matte_buffer=b"",_generation=10,_sha="photo",_closing=False,_matte_aborting=False,
                            _matte_process=SimpleNamespace(readAllStandardOutput=lambda:output.encode()),changed=SimpleNamespace(emit=lambda:None))
    matte_process.read(owner)
    assert owner._matte_active is None and calls == [({"fixture":"final"},{"points":active["points"]})]


@pytest.mark.parametrize("failure", ["changed", "missing", "bad_method", "bad_points"])
def test_actual_neural_worker_rejects_invalid_requests_without_a_result(tmp_path, failure):
    source = tmp_path/"source.png"
    Image.new("RGB",(96,64),(100,100,100)).save(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if failure == "changed": Image.new("RGB",(96,64),(150,100,100)).save(source)
    if failure == "missing": source.unlink()
    mask = empty_mask()
    mask["ops"] = [{"kind":"rect","mode":"add","points":[[.4,.2],[.8,.8]],"radius":0}]
    request = {"id":7,"op":"matte","generation":10,"source_path":str(source),"source_sha":sha,
               "method":"invalid" if failure == "bad_method" else "neural","radius":4,"mask":mask,"points":[[True,.5,1]]}
    child = subprocess.run([sys.executable,str(ROOT/"run.py"),"--matte-worker"], input=json.dumps(request)+"\n",
                           capture_output=True,text=True,encoding="utf8",timeout=30)
    assert child.returncode == 0
    messages = [json.loads(line) for line in child.stdout.splitlines() if line.startswith("{")]
    final = messages[-1]
    assert final["id"] == 7 and final["generation"] == 10 and final["ok"] is False and "result" not in final
    assert all("result" not in message for message in messages)
