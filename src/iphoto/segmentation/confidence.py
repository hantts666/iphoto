"""AI confidence guides for bounded native detail inference.

Geometry proposes local components. The segmentation model supplies sparse
semantic seeds; ViTMatte judges their interiors from the original RGB. No RGB
threshold is used to label pixels. Logits are ranking scores, not calibrated
probabilities. Results, including ordinary edge matting, are published together.
"""
from time import perf_counter

import cv2
import numpy as np
from PIL import Image

from ..document import empty_mask, raster_mask
from ..masks import encode_bitmap
from ..matting import neural
from ..matting.trimap import make_trimap

MAX_ROI = 2_000_000
MAX_REGIONS = 4
MAX_SECONDS = 180
SEED_PERCENTILE = 5


def _tile_count(guide):
    return sum(bool((guide[y:y+neural.TILE, x:x+neural.TILE] == 128).any())
               for y in range(0, guide.shape[0], neural.TILE)
               for x in range(0, guide.shape[1], neural.TILE))


def _local_logits(logits, box, size):
    left, top, right, bottom = box
    # Sample directly into the bounded ROI. Do not enlarge a float confidence
    # map to the size of a 24/60 MP source merely to discard most of it.
    height, width = logits.shape
    xs = (np.arange(left, right, dtype=np.float32)+.5)*width/size[0]-.5
    ys = (np.arange(top, bottom, dtype=np.float32)+.5)*height/size[1]-.5
    return cv2.remap(logits, np.broadcast_to(xs, (len(ys), len(xs))),
                     np.broadcast_to(ys[:, None], (len(ys), len(xs))),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _plans(hard, logits, points):
    height, width = hard.shape
    candidates = []
    # Labels for one class are released before examining the other class.
    for selected in (False, True):
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            (hard == selected).astype(np.uint8), connectivity=8)
        order = sorted(range(1, count), key=lambda i: int(stats[i, 4]), reverse=True)
        for lid in order:
            x, y, w, h, area = map(int, stats[lid])
            if not 256 <= area <= hard.size*.15 or w*h > MAX_ROI:
                continue
            box = (max(0, x-64), max(0, y-64), min(width, x+w+64), min(height, y+h+64))
            left, top, right, bottom = box
            if (right-left)*(bottom-top) > MAX_ROI:
                continue
            component = labels[top:bottom, left:right] == lid
            confidence = _local_logits(logits, box, (width, height))
            margin = confidence if selected else -confidence
            values = margin[component]
            threshold = float(np.percentile(values, 100-SEED_PERCENTILE))
            # A flat or weak model field offers no basis for choosing seeds.
            if threshold < 3 or threshold-float(np.median(values)) < .5:
                continue
            support = cv2.dilate(component.astype(np.uint8),
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))) > 0
            local_hard = hard[top:bottom, left:right]
            guide = (local_hard == selected).astype(np.uint8)*255
            guide[support] = 128
            guide[component & (margin >= threshold)] = 255
            for px, py, label in points:
                ax, ay = round(px*(width-1)), round(py*(height-1))
                if left <= ax < right and top <= ay < bottom:
                    # Explicit anchors remain semantic constraints even on
                    # one-pixel details. No inferred marks enter user history.
                    guide[ay-top, ax-left] = 255 if bool(label) == selected else 0
            candidates.append((area, box, support, guide, selected, threshold))
            if len(candidates) >= MAX_REGIONS*2:
                break
        del labels
    return sorted(candidates, key=lambda p: p[0], reverse=True)[:MAX_REGIONS]


def recover(image, mask, logits, points, *, engine=None, progress=None):
    """Re-open compact interiors using learned seeds, then estimate native alpha.

    A large solid foreground/background is retained. Unknown-pixel, tile and
    time budgets cover all regions and edge tiles in this operation, not each
    region separately. An inference/anchor failure publishes no partial result.
    """
    started = perf_counter()
    logits = np.asarray(logits, dtype=np.float32)
    if logits.ndim != 2 or min(logits.shape) < 2 or not np.isfinite(logits).all():
        raise ValueError("细节约束的模型输出无效，原选区保留")
    original = np.asarray(raster_mask({**mask, "feather": 0}, image.size))
    hard = original >= 128
    plans = _plans(hard, logits, points)
    if not plans:
        return None
    radius = min(64, max(8, round(8*max(image.size)/1600)))
    base = np.rint(make_trimap(original, radius)*255).astype(np.uint8)
    unknown = int((base == 128).sum()) + sum(int((p[3] == 128).sum()) for p in plans)
    tiles = _tile_count(base) + sum(_tile_count(p[3]) for p in plans)
    if unknown > neural.MAX_UNKNOWN or tiles > neural.MAX_TILES:
        return None
    offset = 0
    def report(index, total):
        if perf_counter()-started > MAX_SECONDS:
            raise ValueError("细节细化超时，原选区保留；请缩小范围")
        if progress is not None:
            progress(offset+index, tiles)
    output, count = neural.solve(image, base, engine=engine, progress=report)
    offset += count
    del base
    regions = []
    for area, box, support, guide, selected, threshold in plans:
        left, top, right, bottom = box
        alpha, count = neural.solve(image.crop(box), guide, engine=engine, progress=report)
        offset += count
        pixels = alpha if selected else 255-alpha
        output[top:bottom, left:right][support] = pixels[support]
        regions.append({"roi": list(box), "class": "selected" if selected else "excluded",
                        "area": area, "seed_margin": round(threshold, 3),
                        "unknown_pixels": int((guide == 128).sum()), "tiles": count})
    for x, y, label in points:
        value = output[round(y*(image.height-1)), round(x*(image.width-1))]
        if (label == 1 and value < 230) or (label == 0 and value > 25):
            raise ValueError("细节结果未满足提示点，原选区保留；请把点放在区域内部")
    result = empty_mask()
    result.update(bitmap=encode_bitmap(Image.fromarray(output), sampling="alpha", preserve_resolution=True),
                  label=mask["label"])
    if "edge_protection" in mask:
        result["edge_protection"] = mask["edge_protection"]
    model = engine or neural.backend()
    return result, {"backend": "EfficientSAM confidence + ViTMatte-S · ONNX", "provider": model.provider,
                    "elapsed_ms": round((perf_counter()-started)*1000, 1),
                    "guidance": "learned-confidence", "regions": regions, "tiles": tiles,
                    "unknown_pixels": unknown, "mask_size": list(image.size),
                    "reopened_pixels": int(((output >= 128) & (~hard)).sum()),
                    "removed_pixels": int(((output < 128) & hard).sum()),
                    "coverage": round(float((output > 0).mean())*100, 1),
                    "partial_pixels": int(((output > 0) & (output < 255)).sum()),
                    "warnings": [model.fallback] if getattr(model, "fallback", "") else []}
