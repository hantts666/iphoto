"""Known-alpha benchmark and real lake-image checks. No network or cloud key."""

import argparse
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from test_matting import soft_scene
from iphoto.document import empty_mask, raster_mask, new_layer, render_layers
from iphoto.matting.service import refine_alpha
from iphoto.segmentation.edges import guided_edge
from iphoto.masks import encode_bitmap


def board(images, labels, target):
    w, h = images[0].size
    canvas = Image.new("RGB", (w * len(images), h + 45), "#20272b")
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    draw = ImageDraw.Draw(canvas)
    for i, (image, label) in enumerate(zip(images, labels)):
        canvas.paste(image.convert("RGB"), (i * w, 45))
        draw.text((i * w + 8, 12), label, font=font, fill="white")
    canvas.save(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--natural", action="store_true")
    args = parser.parse_args()
    folder = ROOT / "artifacts/alpha-matting"
    folder.mkdir(parents=True, exist_ok=True)
    image, truth, hard = soft_scene()
    prior = empty_mask()
    prior["bitmap"] = encode_bitmap(
        guided_edge(image, np.asarray(raster_mask(hard, image.size)) > 127, 8)
    )
    refined, q = refine_alpha(image, hard, 8)
    band = (truth > 0) & (truth < 1)
    alphas = [raster_mask(m, image.size) for m in (hard, prior, refined)]
    errors = {
        name: float(np.abs(np.asarray(alpha) / 255 - truth)[band].mean())
        for name, alpha in zip(["hard", "previous_guided", "closed_form"], alphas)
    }
    board(
        [Image.fromarray(np.rint(truth * 255).astype(np.uint8)), *alphas],
        ["已知透明度（测试真值）", "二值选区", "旧边缘估算", "透明边缘细化"],
        folder / "alpha-comparison.png",
    )
    edits = []
    for mask in (hard, prior, refined):
        layer = new_layer()
        layer["mask"] = mask
        layer["recipe"]["exposure"] = -0.8
        edits.append(render_layers(image, [layer]))
    board(
        [image, *edits],
        ["受控测试原图", "硬选区局部压暗", "旧边缘局部压暗", "细化后局部压暗"],
        folder / "adjustment-comparison.png",
    )
    report = {"synthetic": {"edge_alpha_mae": errors, "quality": q}, "natural": []}
    (folder / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True), flush=True)
    if args.natural:
        from iphoto.segmentation.service import segment

        source = Image.open(ROOT / "assets/lake.jpg").convert("RGB")
        source.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        # Fixed hint on the built-in lake photo. SAM still discovers the boundary.
        hint = empty_mask()
        hint["ops"] = [
            {"kind": "rect", "mode": "add", "points": [[0.4, 0.05], [0.99, 0.72]]}
        ]
        before, segq = segment(source, hint, [[0.72, 0.49, 1]])
        before_path = folder / "forest-before.json"
        before_path.write_text(json.dumps(before), encoding="utf-8")
        after, q = refine_alpha(source, before, 8)
        (folder / "forest-after.json").write_text(json.dumps(after), encoding="utf-8")
        edits = []
        for name, mask in [("before", before), ("after", after)]:
            layer = new_layer()
            layer["mask"] = mask
            layer["recipe"]["exposure"] = -0.8
            output = render_layers(source, [layer])
            output.save(folder / f"forest-{name}.png")
            raster_mask(mask, source.size).save(folder / f"forest-{name}-alpha.png")
            edits.append(output)
        box = (715, 405, 1095, 705)
        board(
            [p.crop(box) for p in [source, *edits, raster_mask(after, source.size)]],
            ["原片局部", "原选区局部压暗", "细化后局部压暗", "细化后的透明度"],
            folder / "forest-comparison.png",
        )
        report["natural"].append(
            {
                "name": "lake forest",
                "segmentation": segq,
                "matting": q,
                "ground_truth": False,
            }
        )
        (folder / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report["natural"], ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
