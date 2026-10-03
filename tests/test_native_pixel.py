"""Native object masks, preview promotion and non-publishing phase messages."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from iphoto.controllers import pixel_selections, worker_bridge
from iphoto.document import raster_mask
from iphoto.segmentation import service
from test_import_export import ui  # noqa: F401
from test_matting import soft_scene
from test_object_composition import prepare


@pytest.mark.parametrize("background", [False, True])
def test_real_alpha_runs_on_source_but_reuses_the_exact_encoding_preview(monkeypatch, background):
    source, truth, _ = soft_scene(1920, 320)
    proxy = source.copy()
    proxy.thumbnail((1600, 1600))
    before = source.tobytes()
    phases, encodings = [], []

    class Engine:
        def predict(self, image, coords, labels):
            encodings.append(image)
            seed = Image.fromarray((truth >= .5).astype(np.uint8)*255).resize(image.size, Image.Resampling.NEAREST)
            return np.where(np.asarray(seed)[None] > 127, 5., -5.), np.array([.96]), {"fixture": True}

    monkeypatch.setattr(service, "backend", lambda: Engine())
    # Encoding must not copy the whole native source merely to resize it.
    source.copy = lambda: pytest.fail("Full RGB copy for an embedding")
    result = service.segment_jobs(proxy, [{"id": "target", "points": [[.5, .9, 1]]}],
                                  source=source, tolerant=background,
                                  detail_progress=lambda *phase: phases.append(phase))
    item = result["items"][0]
    quality = item["quality"]
    assert item["mask"]["label"] == "所选区域"
    assert encodings == [proxy] and source.tobytes() == before
    assert quality["resolution"] == ("preview" if background else "source")
    assert quality["mask_size"] == list(proxy.size if background else source.size)
    assert phases == ([("segment", 1, 1)] if background else [("segment", 1, 1), ("edges", 1, 1)])
    if not background:
        actual = np.asarray(raster_mask(item["mask"], source.size)) / 255
        band = (truth > 0) & (truth < 1)
        assert np.abs(actual-truth)[band].mean() < .06
        assert item["mask"]["bitmap"]["sampling"] == "alpha"
        assert quality["original_matting"]["partial_pixels"] > 100


@pytest.mark.parametrize("case", ["segment", "edges", "local_edges", "stale", "old_active", "cancelled", "background",
                                  "bad_total", "bad_part", "boolean", "bad_phase", "wrong_target"])
def test_object_phase_never_publishes_a_partial_mask_or_releases_a_job(case):
    progress = {"kind": "object", "phase": "edges" if case == "edges" else "segment", "part": 1, "total": 1}
    if case == "local_edges": progress["phase"] = "local_edges"
    if case == "bad_total": progress["total"] = 2
    if case == "bad_part": progress["part"] = 0
    if case == "boolean": progress["part"] = True
    if case == "bad_phase": progress["phase"] = "complete"
    active = {"id": 7, "op": "segment", "generation": 9 if case == "old_active" else 10,
              "jobs": [{"mask_target": "body_skin" if case == "wrong_target" else "object"}],
              "cancelled": case == "cancelled", "priority": "low" if case == "background" else "normal"}
    line = json.dumps({"id": 7, "generation": 9 if case == "stale" else 10, "progress": progress}) + "\n"
    layers = [{"id": "unchanged"}]
    owner = SimpleNamespace(_pixel_buffer=b"", _pixel_active=active, _generation=10,
                            _status="previous", _layers=layers, _warm_ready_sha="unprepared",
                            _pixel_process=SimpleNamespace(readAllStandardOutput=lambda: line.encode()),
                            changed=SimpleNamespace(emit=lambda: None),
                            _pump_pixel=lambda: pytest.fail("Phase is not a completed result"))
    worker_bridge._pixel_read(owner)
    assert owner._pixel_active is active and owner._layers is layers and owner._warm_ready_sha == "unprepared"
    assert (owner._status != "previous") == (case in ("segment", "edges", "local_edges"))
    if case == "edges": assert "原图" in owner._status and "取消" in owner._status


def test_cached_preview_is_recomputed_and_excluded_from_a_native_union(ui, monkeypatch):  # noqa: F811
    editor, _, _, _, _ = ui
    prepare(editor)
    ids = [obj["id"] for obj in editor._scene.catalog["objects"]][:2]
    editor._scene.precise[ids[0]]["quality"]["resolution"] = "preview"
    editor._scene.precise[ids[1]]["quality"]["resolution"] = "source"
    before = deepcopy((editor._layers, editor._scene.precise))
    calls = []
    monkeypatch.setattr(pixel_selections, "start", lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    assert pixel_selections.select_objects(editor, ids, local_only=True)
    args, kwargs = calls[0]
    assert [job["id"] for job in args[1]] == [ids[0]]
    assert set(kwargs["composition"]["cached"]) == {ids[1]}
    assert (editor._layers, editor._scene.precise) == before


@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_actual_generic_pixel_process_checks_source_identity_before_any_result(tmp_path, failure):
    from iphoto.workspace import ROOT
    source = tmp_path / "source.png"
    Image.new("RGB", (1800, 320), (90, 140, 180)).save(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    proxy = tmp_path / "preview.png"
    Image.new("RGB", (1600, 284), (90, 140, 180)).save(proxy)
    if failure == "missing": source.unlink()
    else: Image.new("RGB", (1800, 320), (180, 140, 90)).save(source)
    request = {"id": 5, "generation": 9, "op": "segment", "proxy_path": str(proxy),
               "source_path": str(source), "source_sha": sha,
               "jobs": [{"id": "target", "points": [[.5, .5, 1]]}]}
    child = subprocess.run([sys.executable, str(ROOT/"run.py"), "--pixel-worker"],
                           input=json.dumps(request)+"\n", capture_output=True, text=True, encoding="utf8", timeout=30)
    assert child.returncode == 0
    response = json.loads(child.stdout)
    assert response["id"] == 5 and response["generation"] == 9 and response["ok"] is False
    assert "result" not in response


@pytest.mark.parametrize("error", [ValueError("Dense edge cannot be solved"), ModuleNotFoundError("Optional matting unavailable")])
def test_unreliable_auto_matting_retains_a_native_bounded_result_and_reports_fallback(monkeypatch, error):
    source, truth, _ = soft_scene(1920, 320)
    proxy = source.copy(); proxy.thumbnail((1600, 1600))
    before = source.tobytes()
    phases = []
    class Engine:
        def predict(self, image, coords, labels):
            seed = Image.fromarray((truth>=.5).astype(np.uint8)*255).resize(image.size, Image.Resampling.NEAREST)
            return np.where(np.asarray(seed)[None]>127, 5., -5.), np.array([.96]), {}
    def reject(*args, **kwargs): raise error
    monkeypatch.setattr(service, "backend", lambda: Engine())
    monkeypatch.setattr("iphoto.matting.service.refine_alpha", reject)
    result = service.segment_jobs(proxy, [{"id":"target","points":[[.5,.9,1]]}], source=source,
                                  detail_progress=lambda *phase: phases.append(phase))
    item = result["items"][0]
    quality = item["quality"]
    pixels = np.asarray(raster_mask(item["mask"], source.size))
    assert quality["resolution"] == "source" and quality["mask_size"] == list(source.size)
    assert quality["original_edges"]["reason"] == str(error) and "original_matting" not in quality
    assert quality["warnings"] and phases[-1] == ("local_edges",1,1)
    assert np.any((pixels>0)&(pixels<255)) and pixels[0,0] == 0 and pixels[-1,-1] == 255
    assert source.tobytes() == before


@pytest.mark.parametrize("radius", [4, 12])
def test_tiled_native_color_edges_match_whole_rgb_without_a_tile_seam(radius):
    from iphoto.segmentation.edges import guided_edge, native_edge
    from iphoto.document import empty_mask
    from iphoto.masks import encode_bitmap
    source, truth, _ = soft_scene(1050, 650)
    hard = truth>=.5
    seed = {**empty_mask(), "bitmap": encode_bitmap(Image.fromarray(hard.astype(np.uint8)*255), preserve_resolution=True)}
    whole = np.asarray(guided_edge(source, hard, radius))
    actual = np.asarray(raster_mask(native_edge(source, seed, radius), source.size))
    assert np.abs(actual.astype(int)-whole.astype(int)).max() <= 1
    assert np.array_equal(actual[whole==0],whole[whole==0])
    assert np.array_equal(actual[whole==255],whole[whole==255])
    assert native_edge(source, {**seed,"label":"枝条附近的天空"}, radius)["label"] == "枝条附近的天空"


def test_repeated_refinement_retains_the_target_name_without_an_operation_log():
    from iphoto.matting.service import refine_alpha
    source, _, mask = soft_scene(240, 160)
    mask["label"] = "树叶之间的天空"
    first, _ = refine_alpha(source, mask)
    second, _ = refine_alpha(source, first)
    assert first["label"] == second["label"] == mask["label"]
