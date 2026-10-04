"""Painted unknown regions with native AI alpha and exact outside coverage."""

from copy import deepcopy
from time import perf_counter

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from ..segmentation.prompts import validate_points
from . import neural
from .metadata import copy_metadata

MAX_ROI = 16_000_000


def stroke_mask(stroke, size):
    if not isinstance(stroke, dict) or set(stroke) != {"points", "radius"}:
        raise ValueError("透明细化笔触格式无效")
    mask = empty_mask()
    mask["ops"] = [{"kind": "brush", "mode": "add", **deepcopy(stroke)}]
    return raster_mask(validate_mask(mask), size)


def refine(image, mask, stroke, *, points=None, progress=None, engine=None):
    started = perf_counter()
    mask = validate_mask(mask)
    points = validate_points([] if points is None else points)
    scope_image = stroke_mask(stroke, image.size)
    bounds = scope_image.getbbox()
    if bounds is None:
        raise ValueError("请在需要细化的区域涂抹")
    left, top, right, bottom = bounds
    box = (max(0, left-neural.HALO), max(0, top-neural.HALO),
           min(image.width, right+neural.HALO), min(image.height, bottom+neural.HALO))
    if (box[2]-box[0]) * (box[3]-box[1]) > MAX_ROI:
        raise ValueError("笔触跨度过大，请分开涂抹需要细化的区域")
    scope = np.asarray(scope_image.crop(box)) > 0
    del scope_image
    original = raster_mask(mask, image.size)
    previous = np.asarray(original.crop(box))
    protected = not mask["inverted"] and mask.get("semantic_target") in ("face", "face_skin", "body_skin")
    if protected:
        scope &= previous > 0
    if not scope.any():
        raise ValueError("笔触位于受保护区域，请在已有范围内细化；补选可使用补选画笔")
    if int(scope.sum()) > neural.MAX_UNKNOWN:
        raise ValueError("涂抹范围过大，请分区域细化")
    # Existing continuous edges remain unknown context. Only painted pixels
    # are committed; context predictions cannot change unpainted coverage.
    trimap = np.where(previous == 255, 255, np.where(previous == 0, 0, 128)).astype(np.uint8)
    trimap[scope] = 128
    if protected:
        trimap[previous == 0] = 0
    if not np.any(trimap == 255) or not np.any(trimap == 0):
        raise ValueError("附近缺少确定的目标或背景，请沿问题边缘涂抹并保留部分内部作为参考")
    anchors = {}
    for x, y, label in points:
        cy, cx = round(y*(image.height-1))-box[1], round(x*(image.width-1))-box[0]
        if not (0 <= cy < trimap.shape[0] and 0 <= cx < trimap.shape[1]):
            continue
        value = 255 if label else 0
        if (cy, cx) in anchors and anchors[cy, cx] != value:
            raise ValueError("保留点和排除点重叠，请先修正提示点")
        if trimap[cy, cx] not in (128, value):
            raise ValueError("提示点与已有确定范围冲突，请先修正轮廓")
        anchors[cy, cx] = value
        trimap[cy, cx] = value
    if int((trimap == 128).sum()) > neural.MAX_UNKNOWN:
        raise ValueError("附近待判断的透明范围过大，请分区域细化")
    engine = engine or neural.backend()
    pixels, tiles = neural.solve(image.crop(box), trimap, engine=engine, progress=progress)
    # Join to the existing range inside the painted footprint. A hard patch
    # boundary creates a visible arc when the model recovers missing hair.
    # The outermost unpainted pixel remains byte-exact, including image edges.
    join_width = max(2, min(32, stroke["radius"] * min(image.size) * .5))
    weight = np.clip(distance_transform_edt(scope) / join_width, 0, 1)
    weight = weight * weight * (3 - 2 * weight)
    pixels = np.rint(previous + weight * (pixels.astype(np.float32) - previous)).astype(np.uint8)
    for (cy, cx), value in anchors.items():
        if scope[cy, cx]:
            pixels[cy, cx] = value
    if protected and mask.get("face_part_scope"):
        ceiling = np.asarray(raster_mask(mask["face_part_scope"], image.size).crop(box))
        pixels = np.minimum(pixels, ceiling)
    changed = scope & (pixels != previous)
    changed_count = int(changed.sum())
    quality = {"backend": "ViTMatte-S · ONNX", "provider": engine.provider,
               "elapsed_ms": round((perf_counter()-started)*1000, 1),
               "tiles": tiles, "mask_size": list(image.size), "local_refinement": True,
               "scope_pixels": int(scope.sum()), "unknown_pixels": int((trimap == 128).sum()),
               "changed_pixels": changed_count, "scope_box": list(box), "join_width": round(join_width, 1),
               "warnings": [engine.fallback] if engine.fallback else []}
    if not changed_count:
        quality["partial_pixels"] = int(((previous>0)&(previous<255)).sum())
        return deepcopy(mask), quality
    output = np.array(original)
    output[box[1]:box[3],box[0]:box[2]][changed] = pixels[changed]
    quality["partial_pixels"] = int(((output>0)&(output<255)).sum())
    result = copy_metadata(mask, empty_mask())
    result.update(bitmap=encode_bitmap(Image.fromarray(output), sampling="alpha", preserve_resolution=True), label=mask["label"])
    return result, quality
