"""Compare real local neural masks against saved coarse Qianwen hints."""

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from iphoto.document import raster_mask
from iphoto.segmentation.efficient_sam import EfficientSAM
from iphoto.segmentation.service import segment


def overlay(image, mask):
    out = image.convert("RGBA")
    color = Image.new("RGBA", out.size, (63, 235, 143, 0))
    color.putalpha(mask.point(lambda p: round(p * 0.42)))
    return Image.alpha_composite(out, color).convert("RGB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("s", "ti"), default="s")
    args = parser.parse_args()
    folder = ROOT / f"artifacts/selection-v15-{args.variant}"
    folder.mkdir(parents=True, exist_ok=True)
    photo = Image.open(ROOT / "assets/lake.jpg").convert("RGB")
    photo.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    project = json.loads(
        (ROOT / "artifacts/v14-demo.iphoto").read_text(encoding="utf-8")
    )
    catalog = project.get("scene_catalog") or project.get("scene")
    if not catalog:
        raise ValueError(f"No saved catalog; available keys: {list(project)}")
    started = perf_counter()
    engine = EfficientSAM(args.variant)
    report = {
        "model": args.variant,
        "model_load_ms": round((perf_counter() - started) * 1000, 1),
        "cases": [],
    }
    for obj in catalog["objects"]:
        if obj["category"] not in ("天空", "建筑", "水面"):
            continue
        print(f"Segmenting {obj['id']} {obj['name']}", flush=True)
        try:
            result, quality = segment(photo, obj["mask"], engine=engine)
            mask = raster_mask(result, photo.size)
            mask.save(folder / f"{obj['id']}-mask.png")
            old = raster_mask(obj["mask"], photo.size)
            side = Image.new("RGB", (photo.width * 2, photo.height + 36), (30, 34, 38))
            side.paste(overlay(photo, old), (0, 36))
            side.paste(overlay(photo, mask), (photo.width, 36))
            draw = ImageDraw.Draw(side)
            draw.text((14, 10), "BEFORE: VLM polygons", fill="white")
            draw.text(
                (photo.width + 14, 10),
                f"AFTER: EfficientSAM-{args.variant} pixels",
                fill="white",
            )
            side.save(folder / f"{obj['id']}-comparison.jpg", quality=92)
            report["cases"].append(
                {"id": obj["id"], "name": obj["name"], "quality": quality}
            )
            (folder / f"{obj['id']}-result.json").write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
            print(json.dumps(quality, ensure_ascii=False), flush=True)
        except ValueError as exc:
            report["cases"].append({"id": obj["id"], "error": str(exc)})
            print(str(exc), flush=True)
    (folder / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
