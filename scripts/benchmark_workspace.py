"""Reproducible v1.2.1 preview/cache/24MP export measurements; sample only."""
from pathlib import Path
import json
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "src"))
from PIL import Image
from iphoto.document import new_layer, render_layers
from iphoto.engine import load_source, preview, Recipe, PRESETS, export_image


def main():
    layers = [new_layer("全图",True),new_layer("局部")]
    layers[0]["recipe"] = Recipe.from_dict(PRESETS["natural"]).to_dict()
    layers[1]["recipe"] = Recipe(exposure=.2,tint=12,vibrance=15,sharpness=20).to_dict()
    layers[1]["mask"].update(feather=.01,ops=[{"kind":"ellipse","mode":"add","points":[[.2,.2],[.75,.8]]}])
    with tempfile.TemporaryDirectory(prefix="iphoto-bench-") as directory:
        source_path = Path(directory) / "sample24mp.jpg"
        with Image.open(ROOT / "assets/lake.jpg") as image:
            image.resize((6000,4000)).save(source_path,quality=95)
        source = load_source(source_path)
        proxy = preview(source.image)
        elapsed = []
        for _ in range(3):
            started = time.perf_counter(); render_layers(proxy,layers)
            elapsed.append(round((time.perf_counter()-started)*1000))
        started = time.perf_counter()
        export_image(source,Recipe(),Path(directory)/"result.jpg",render_layers(source.image,layers))
        report = {"input":"6000x4000, resized bundled sample", "layers":2,
                  "preview_render_ms":elapsed,"export_seconds":round(time.perf_counter()-started,3),
                  "scope":"CPU render; preview excludes PNG encoding, IPC and display. Export includes composition and JPEG encoding."}
        path = ROOT / "artifacts/quality-benchmark.json"
        path.write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps(report),flush=True)


if __name__ == "__main__": main()
