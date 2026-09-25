"""Real-model geometry benchmark on generated shapes with exact binary truth.

These controlled fixtures test curves/holes and prompts, NOT natural-photo
accuracy or hair matting. Natural-photo overlays are evaluated separately.
"""

import argparse
import json
from pathlib import Path
import sys
import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from iphoto.document import empty_mask, raster_mask
from iphoto.segmentation.efficient_sam import EfficientSAM
from iphoto.segmentation.service import segment


def metrics(prediction, truth):
    a, b = prediction > 127, truth > 127
    kernel = np.ones((3, 3), np.uint8)
    ea = a & ~cv2.erode(a.astype(np.uint8), kernel).astype(bool)
    eb = b & ~cv2.erode(b.astype(np.uint8), kernel).astype(bool)
    da = cv2.dilate(ea.astype(np.uint8), kernel, iterations=2).astype(bool)
    db = cv2.dilate(eb.astype(np.uint8), kernel, iterations=2).astype(bool)
    precision = (ea & db).sum() / max(1, ea.sum())
    recall = (eb & da).sum() / max(1, eb.sum())
    return {
        "iou": round(float((a & b).sum() / max(1, (a | b).sum())), 4),
        "boundary_f1_at_2px": round(
            float(2 * precision * recall / max(1e-9, precision + recall)), 4
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("s", "ti"), default="s")
    args = parser.parse_args()
    folder = ROOT / "artifacts" / ("geometry-v15-" + args.variant)
    folder.mkdir(exist_ok=True, parents=True)
    model = EfficientSAM(args.variant)
    report = []
    for case in ("ellipse", "ring"):
        truth = Image.new("L", (600, 420))
        draw = ImageDraw.Draw(truth)
        draw.ellipse((90, 55, 510, 365), fill=255)
        if case == "ring":
            draw.ellipse((245, 145, 355, 260), fill=0)
        image = Image.composite(
            Image.new("RGB", truth.size, (228, 158, 60)),
            Image.new("RGB", truth.size, (32, 75, 114)),
            truth,
        )
        hint = empty_mask()
        hint["ops"] = [
            {"kind": "rect", "mode": "add", "points": [[0.12, 0.10], [0.88, 0.9]]}
        ]
        points = [[0.28, 0.5, 1]] + ([[0.5, 0.5, 0]] if case == "ring" else [])
        result, quality = segment(image, hint, points, engine=model)
        alpha = raster_mask(result, image.size)
        alpha.save(folder / f"{case}-prediction.png")
        truth.save(folder / f"{case}-truth.png")
        image.save(folder / f"{case}-image.png")
        record = {
            "case": case,
            "metrics": metrics(np.asarray(alpha), np.asarray(truth)),
            "box_baseline": metrics(
                np.asarray(raster_mask(hint, truth.size)), np.asarray(truth)
            ),
            "quality": quality,
        }
        report.append(record)
        print(json.dumps(record), flush=True)
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
