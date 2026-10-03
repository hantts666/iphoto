"""Crop coordinates for close-up grounding of skin regions and small objects."""

from copy import deepcopy
import math

from .document import raster_mask, validate_mask


def object_crop(mask, size):
    """Bounded UI geometry; full-resolution pixels are cropped in the worker."""
    bounds = raster_mask(mask, (384, 384)).getbbox()
    if bounds is None:
        raise ValueError("目标范围为空，原范围保留")
    left, top, right, bottom = (value / 384 for value in bounds)
    pad_x, pad_y = max((right-left)*.25, .02), max((bottom-top)*.25, .02)
    crop = [max(0, left-pad_x), max(0, top-pad_y),
            min(1, right+pad_x), min(1, bottom+pad_y)]
    crop_pixels(crop, size)
    return crop


def needs_object_detail(mask, quality):
    """Only a small, weak model result warrants another foreground request."""
    score = quality.get("predicted_iou")
    if (quality.get("detail_grounded") or isinstance(score, bool)
            or not isinstance(score, (int, float)) or not math.isfinite(score)
            or not 0 <= score < .85):
        return False
    bounds = raster_mask(mask, (384, 384)).getbbox()
    return bounds is not None and 0 < (bounds[2]-bounds[0])*(bounds[3]-bounds[1])/384**2 <= .1


def region_crop(mask, size):
    width, height = size
    bounds = raster_mask(mask, size).getbbox()
    if bounds is None:
        raise ValueError("AI 返回了空的皮肤范围，照片未改变")
    left, top, right, bottom = bounds
    pad_x = max(round((right - left) * .25), round(width * .02))
    pad_y = max(round((bottom - top) * .25), round(height * .02))
    return [
        max(0, left - pad_x) / width,
        max(0, top - pad_y) / height,
        min(width, right + pad_x) / width,
        min(height, bottom + pad_y) / height,
    ]


def crop_pixels(box, size):
    if (
        not isinstance(box, (list, tuple)) or len(box) != 4
        or any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or not 0 <= v <= 1 for v in box)
        or box[0] >= box[2] or box[1] >= box[3]
    ):
        raise ValueError("局部定位的图片范围无效")
    width, height = size
    rect = tuple(round(v * (width if i % 2 == 0 else height)) for i, v in enumerate(box))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ValueError("局部定位的图片范围过小")
    return rect


def map_grounding(result, crop, size, region):
    """Map validated crop-local geometry back to the full photo, preserving edits."""
    left, top, right, bottom = crop_pixels(crop, size)
    width, height = size

    def point(p):
        return [(left + p[0] * (right - left)) / width,
                (top + p[1] * (bottom - top)) / height]

    mask = deepcopy(result["mask"])
    if mask.get("bitmap") or mask["base"] != "empty" or mask["inverted"]:
        raise ValueError("局部定位返回了无效的范围，照片未改变")
    for op in mask["ops"]:
        op["points"] = [point(p) for p in op["points"]]
    mask["label"] = region["mask"]["label"]
    if "anchor" not in result:
        raise ValueError("局部定位缺少目标内部点，照片未改变")
    return {**deepcopy(region), "mask": validate_mask(mask), "anchor": point(result["anchor"])}
