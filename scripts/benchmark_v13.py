"""Reproducible local algorithm and cache evidence; no cloud or user photos."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"tests"))
from PIL import Image, ImageDraw
from iphoto.document import new_layer, render_layers, raster_mask
from iphoto.plugins import refine
from iphoto.preview_cache import LayerPreviewCache
from iphoto.engine import PRESETS, Recipe
from test_redesign import scene, box, iou


def timed(fn):
    started=time.perf_counter(); result=fn()
    return result,round((time.perf_counter()-started)*1000,1)


photo=Image.open(ROOT/"assets/lake.jpg").convert("RGB")
photo.thumbnail((1600,1600))
layers=[new_layer("底层",True),new_layer("中层",True),new_layer("顶层",True)]
layers[0]["recipe"]=Recipe.from_dict(PRESETS["natural"]).to_dict()
layers[1]["recipe"]["warmth"]=9
layers[2]["recipe"]["exposure"]=.1
cache=LayerPreviewCache()
_,cold=timed(lambda: cache.render(photo,layers))
fast=[]; baseline=[]
for exposure in (.12,.14,.16):
    layers[-1]["recipe"]["exposure"]=exposure
    output,elapsed=timed(lambda: cache.render(photo,layers)); fast.append(elapsed)
    _,elapsed=timed(lambda: render_layers(photo,layers)); baseline.append(elapsed)

image,truth=scene((640,480)); coarse=box()
(selected,quality),segtime=timed(lambda: refine(image,coarse))
result={"preview_size":list(photo.size),"layers":3,"measurement":"CPU composition only; excludes PNG, IPC and QML display",
        "cold_cache_ms":cold,"top_layer_change_cached_ms":fast,"uncached_ms":baseline,"reused_lower_layers":cache.reused,
        "cache_pixel_bytes":cache.bytes,"cache_budget_bytes":cache.max_bytes,
        "synthetic_selection":{"size":list(image.size),"before_iou":round(float(iou(raster_mask(coarse,image.size),truth)),4),
                               "after_iou":round(float(iou(raster_mask(selected,image.size),truth)),4),"elapsed_ms":segtime,"quality":quality}}
target=ROOT/"artifacts/v13-benchmark.json"; target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
for name,mask in [("coarse",coarse),("refined",selected)]:
    alpha=raster_mask(mask,image.size).point([round(v*.4) for v in range(256)])
    overlay=Image.new("RGBA",image.size,(40,235,160,0));overlay.putalpha(alpha)
    shown=image.convert("RGBA");shown.alpha_composite(overlay)
    shown.save(ROOT/f"artifacts/v13-synthetic-{name}.png")
print(json.dumps(result,ensure_ascii=False,indent=2))
