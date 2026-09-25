"""Isolated CPU benchmark. Does not call AI or measure network / QML display time."""

from copy import deepcopy
import json
from pathlib import Path
import statistics
import sys
import time
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from iphoto.document import new_layer, render_layers, raster_mask
from iphoto.preview_cache import LayerPreviewCache
from iphoto.scene import SceneIndex


def timed(function):
    start = time.perf_counter()
    result = function()
    return result, (time.perf_counter() - start) * 1000


def main():
    picture = Image.open(ROOT / "assets/lake.jpg").convert("RGB")
    picture.thumbnail((1600, 1600))
    base = new_layer("全图", True)
    base["recipe"]["shadows"] = 18
    group = new_layer("图层组", True, kind="group")
    low = new_layer("下层", True, group["id"])
    low["recipe"]["exposure"] = 0.15
    high = new_layer("上层", True, group["id"])
    high["recipe"]["contrast"] = 12
    layers = [base, group, low, high]
    cache = LayerPreviewCache()
    cold, cold_ms = timed(lambda: cache.render(picture, layers))
    scenarios = []
    for name in ("group_opacity", "top_recipe"):
        cached = []
        uncached = []
        reused = []
        for i in range(3):
            if name == "group_opacity":
                group["opacity"] = 0.55 + i * 0.1
            else:
                high["recipe"]["contrast"] = 15 + i * 3
            result, elapsed = timed(lambda: cache.render(picture, layers))
            cached.append(elapsed)
            reused.append(cache.reused)
            reference, elapsed = timed(lambda: render_layers(picture, layers))
            uncached.append(elapsed)
            assert np.array_equal(result, reference)
        scenarios.append(
            {
                "name": name,
                "cached_ms": [round(v, 2) for v in cached],
                "uncached_ms": [round(v, 2) for v in uncached],
                "reused_nodes": reused,
                "median_reduction_percent": round(
                    (1 - statistics.median(cached) / statistics.median(uncached)) * 100,
                    1,
                ),
            }
        )
    # Use the actual, sanitized live scene if available; otherwise no invented
    # scene timing appears in the report.
    scene_report = ROOT / "artifacts/v14-live-workflow/real-workflow.iphoto"
    hit_report = None
    if scene_report.exists():
        catalog = json.loads(scene_report.read_text(encoding="utf-8"))["scene_catalog"]
        index = SceneIndex()
        _, build_ms = timed(lambda: index.set(catalog))
        points = [((i * 37 % 997) / 997, (i * 61 % 991) / 991) for i in range(10000)]
        _, hit_ms = timed(lambda: [index.hit(x, y) for x, y in points])
        hit_report = {
            "objects": len(catalog["objects"]),
            "index_build_ms": round(build_ms, 3),
            "10000_hits_ms": round(hit_ms, 3),
            "mean_hit_microseconds": round(hit_ms / 10, 3),
            "index_pixel_bytes": len(catalog["objects"]) * 384 * 384,
        }
    report = {
        "scope": "CPU composition only; excludes PNG, IPC, QML and cloud latency",
        "photo_size": picture.size,
        "cold_ms": round(cold_ms, 2),
        "scenarios": scenarios,
        "cache_pixel_bytes": cache.bytes,
        "cache_limit_bytes": cache.max_bytes,
        "object_hit_index": hit_report,
    }
    path = ROOT / "artifacts/v14-benchmark.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
