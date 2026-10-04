"""Interactive neural corrections inside an existing semantic partition.

The parser's excluded classes stay excluded. Point corrections use native RGB
near the points, retaining the rest of the mask and its person association.
"""

from time import perf_counter

import cv2
import numpy as np
from PIL import Image

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from .edges import guided_edge
from .prompts import from_hint, validate_points


def supported(hint):
    return bool(isinstance(hint, dict) and hint and not hint.get("inverted")
                and hint.get("semantic_target") in ("face", "face_skin", "body_skin"))


def refine(image, hint, points, *, engine=None, progress=None):
    from .service import choose_candidate
    from .efficient_sam import backend

    started = perf_counter()
    hint = validate_mask(hint)
    points = validate_points(points or [])
    if not supported(hint):
        raise ValueError("当前范围没有可保护的语义分区")
    original = raster_mask(hint, image.size)
    bounds = original.getbbox()
    if bounds is None:
        raise ValueError("当前范围为空，请先选择目标")
    width, height = image.size
    locations = [(min(width-1, round(x*(width-1))), min(height-1, round(y*(height-1))), label)
                 for x, y, label in points]
    for x, y, label in locations:
        if label and original.getpixel((x, y)) <= 127:
            raise ValueError("保留点需落在已有范围内部；扩大范围请点“补选”，五官保护保持不变")
    if locations:
        # Model input keeps source details. Far-apart points share a bounded
        # encoding; pixels beyond this union never undergo a new segmentation.
        radius = min(512, max(96, round(min(image.size)*.064)))
        box = (max(0, min(x for x, y, label in locations)-radius),
               max(0, min(y for x, y, label in locations)-radius),
               min(width, max(x for x, y, label in locations)+radius+1),
               min(height, max(y for x, y, label in locations)+radius+1))
    else:
        box = (max(0, bounds[0]-64), max(0, bounds[1]-64),
               min(width, bounds[2]+64), min(height, bounds[3]+64))
    # Small parts need the original detail at a useful model scale. Keep the
    # point neighborhood for large skin partitions, but trim unused space
    # around a lip/nose partition when every explicit point stays inside.
    tight = (max(box[0], bounds[0]-64), max(box[1], bounds[1]-64),
             min(box[2], bounds[2]+64), min(box[3], bounds[3]+64))
    if all(tight[0] <= x < tight[2] and tight[1] <= y < tight[3] for x, y, label in locations):
        box = tight
    if (box[2]-box[0])*(box[3]-box[1]) > 16_000_000:
        raise ValueError("修正点跨越的范围过大，请分次修正邻近部位，原范围保留")
    patch = image.crop(box).convert("RGB")
    local = original.crop(box)
    proxy = patch.copy()
    proxy.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    guide = local.resize(proxy.size, Image.Resampling.BILINEAR)
    mapped = [[(x-box[0])/max(1, patch.width-1), (y-box[1])/max(1, patch.height-1), label]
              for x, y, label in locations]
    # A semantic prior is already a target. Alt can be the first correction;
    # its inferred keep point is never added to the user's prompt history.
    if not any(point[2] for point in mapped):
        if len(mapped) > 5:
            raise ValueError("最多连续放置 5 个排除点；请退一点或添加一个保留点")
        hard = (np.asarray(guide)>127).astype(np.uint8)
        for x, y, label in mapped:
            cv2.circle(hard, (round(x*(proxy.width-1)), round(y*(proxy.height-1))), 3, 0, -1)
        distance = cv2.distanceTransform(np.pad(hard, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
        _, value, _, strongest = cv2.minMaxLoc(distance)
        if value <= 0:
            raise ValueError("排除点附近没有可保留的目标，请调整点位")
        mapped = [[strongest[0]/max(1, proxy.width-1), strongest[1]/max(1, proxy.height-1), 1], *mapped]
    coords, labels = from_hint(guide, mapped)
    fallback_warning = None
    from . import precise_sam
    precise = (engine is None and hint.get("face_part") in ("nose", "lips")
               and any(label == 0 for label in labels) and precise_sam.available())
    if precise:
        try:
            logits, scores, timing = precise_sam.backend(progress=progress).predict_with_prior(
                proxy, coords, labels, guide, progress=progress)
        except Exception:
            # Optional model failures preserve the existing neural route. Its
            # result still has to pass the same prompt/semantic constraints.
            fallback_warning = "精细提示点模型未能完成，已尝试基础神经分割"
            precise = False
    if not precise:
        if progress is not None:
            progress("semantic_points")
        logits, scores, timing = (engine or backend()).predict(proxy, coords, labels)
    hard, quality = choose_candidate(logits, scores, coords, labels, guide)
    alpha = guided_edge(proxy, hard, radius=2).resize(patch.size, Image.Resampling.BILINEAR)
    prior = np.asarray(local)
    corrected = np.minimum(np.asarray(alpha), prior)
    if locations:
        # Fade at an internal crop boundary, avoiding a rectangular seam in a
        # continuous cheek. Every explicit point is well inside this border.
        yy, xx = np.ogrid[:patch.height, :patch.width]
        x_distance = np.minimum(xx+1 if box[0] else 32,
                                patch.width-xx if box[2] < width else 32)
        y_distance = np.minimum(yy+1 if box[1] else 32,
                                patch.height-yy if box[3] < height else 32)
        distance = np.minimum(x_distance, y_distance)
        blend = np.minimum(distance/32, 1)
        corrected = np.rint(prior+(corrected.astype(np.float32)-prior)*blend).astype(np.uint8)
    result_pixels = original.copy()
    result_pixels.paste(Image.fromarray(corrected), box[:2])
    for x, y, label in locations:
        if (result_pixels.getpixel((x, y))>127) != bool(label):
            raise ValueError("局部结果未满足保留/排除点，原范围保留；请调整点位或用画笔修正")
    if not result_pixels.getbbox():
        raise ValueError("修正后的范围为空，原范围保留")
    result = empty_mask()
    result.update(label=hint["label"], semantic_target=hint["semantic_target"],
                  bitmap=encode_bitmap(result_pixels, sampling="alpha", preserve_resolution=True))
    if "face_binding" in hint:
        result["face_binding"] = hint["face_binding"]
    if "face_part" in hint:
        result["face_part"] = hint["face_part"]
    quality.update(timing, model=timing.get("model", "EfficientSAM-S")+" · 语义范围保护", resolution="source",
                   semantic_target=hint["semantic_target"], crop_box=list(box), crop_size=list(patch.size),
                   mask_size=list(image.size), elapsed_ms=round((perf_counter()-started)*1000, 1))
    quality["warnings"].append("已保留原分区的五官保护；扩大范围可用“补选”")
    if fallback_warning:
        quality["warnings"].append(fallback_warning)
    return validate_mask(result), quality
