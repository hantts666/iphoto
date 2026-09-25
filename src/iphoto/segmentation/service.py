"""Image-to-mask pipeline. Call only in the image worker, never the UI thread."""

from time import perf_counter
import numpy as np
from PIL import Image

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from .efficient_sam import backend
from .edges import guided_edge
from .corrections import correct_negatives
from .prompts import from_hint, from_points, validate_points


def choose_candidate(logits, scores, coords, labels, hint=None):
    masks = logits > 0
    candidates = []
    for i, mask in enumerate(masks):
        coverage = float(mask.mean())
        if coverage < 0.0001 or coverage > 0.995:
            continue
        checks = []
        for (x, y), label in zip(coords, labels):
            if label in (0, 1):
                checks.append(
                    bool(
                        mask[
                            min(mask.shape[0] - 1, round(float(y))),
                            min(mask.shape[1] - 1, round(float(x))),
                        ]
                    )
                    == bool(label)
                )
        adherence = float(np.mean(checks)) if checks else 1
        if adherence < 1:
            continue
        overlap = 0.0
        if hint is not None:
            reference = np.asarray(hint) > 127
            overlap = float((mask & reference).sum() / max(1, (mask | reference).sum()))
            if overlap < 0.10:
                continue
        rank = (
            float(np.clip(scores[i], 0, 1)) * 0.60 + adherence * 0.25 + overlap * 0.15
        )
        candidates.append((rank, i, adherence, overlap))
    if not candidates:
        raise ValueError(
            "模型没有找到可靠目标，原选区保留；请框住目标或补充保留/排除点"
        )
    rank, index, adherence, overlap = max(candidates)
    if adherence < 1:
        raise ValueError("结果没有满足提示点，原选区保留；请把点放在目标或背景的内部")
    return masks[index], {
        "predicted_iou": round(float(scores[index]), 3),
        "hint_overlap": round(overlap, 3),
        "candidate": index,
        "warnings": ["模型对边界信心较低，请补充提示点"]
        if scores[index] < 0.85
        else [],
    }


def segment(image, hint=None, points=None, *, engine=None, soften=True):
    started = perf_counter()
    points = validate_points(points or [])
    proxy = image.convert("RGB")
    proxy.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    guide = raster_mask(validate_mask(hint), proxy.size) if hint is not None else None
    coords, labels = (
        from_hint(guide, points)
        if guide is not None
        else from_points(points, proxy.size)
    )
    logits, scores, timing = (engine or backend()).predict(proxy, coords, labels)
    corrected = {}
    if 0 in labels:
        logits = logits.copy()
        for i in range(len(logits)):
            hard, count = correct_negatives(proxy, logits[i] > 0, coords, labels)
            if count:
                logits[i][~hard] = np.minimum(logits[i][~hard], -1)
                corrected[i] = count
    hard, quality = choose_candidate(logits, scores, coords, labels, guide)
    if quality["candidate"] in corrected:
        quality["local_negative_pixels"] = corrected[quality["candidate"]]
        quality["warnings"].append("已按排除点局部修正，请检查颜色相近的边缘")
    alpha = (
        guided_edge(proxy, hard, radius=max(2, round(min(proxy.size) * 0.004)))
        if soften
        else Image.fromarray(hard.astype(np.uint8) * 255)
    )
    result = empty_mask()
    result.update(bitmap=encode_bitmap(alpha), label="像素贴边选区")
    if hint:
        result["label"] = (hint.get("label", "") + " · 像素贴边")[:200]
    if image.size != proxy.size:
        from ..matting.service import refine_alpha

        result, matte_quality = refine_alpha(image, result, radius=min(64, max(8, round(8 * max(image.size) / 1600))))
        quality["original_matting"] = matte_quality
    quality.update(
        timing,
        coverage=round(float((np.asarray(alpha) > 0).mean()) * 100, 1),
        elapsed_ms=round((perf_counter() - started) * 1000, 1),
        mask_size=[result["bitmap"]["width"], result["bitmap"]["height"]],
    )
    return result, quality
