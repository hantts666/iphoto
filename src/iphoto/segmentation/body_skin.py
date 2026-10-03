"""Separately localized body parts, inferred on source crops and merged once.

Localization identifies bare skin; EfficientSAM separates each local target.
This adapter is not a whole-body anatomical or skin-color classifier.
"""

from time import perf_counter

from PIL import Image, ImageChops

from ..ai_grounding import crop_pixels, region_crop
from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from .prompts import validate_points


def segment(image, parts, *, engine=None, progress=None):
    from .service import segment as segment_target

    started = perf_counter()
    if not isinstance(parts, list) or not 1 <= len(parts) <= 4:
        raise ValueError("身体皮肤一次需要1～4个独立定位部位，照片未改变")
    combined = Image.new("L", image.size)
    qualities = []
    label = "身体皮肤"
    for index, part in enumerate(parts):
        hint = validate_mask(part["hint"])
        points = validate_points(part.get("points") or [])
        if len(points) != 1 or points[0][2] != 1:
            raise ValueError("每个身体部位需要一个内部保留点，照片未改变")
        box = crop_pixels(part.get("skin_crop") or region_crop(hint, (384, 384)), image.size)
        left, top, right, bottom = box
        patch = image.crop(box).convert("RGB")
        patch.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        local_point = [(points[0][0] * image.width - left) / (right - left),
                       (points[0][1] * image.height - top) / (bottom - top), 1]
        validate_points([local_point])
        # Rasterize the guide in source coordinates before cropping, including
        # its holes. A normalized whole-photo bitmap must not be reused as-is.
        local_guide = raster_mask(hint, image.size).crop(box).resize(patch.size, Image.Resampling.NEAREST)
        local_hint = {**empty_mask(), "label": hint["label"], "bitmap": encode_bitmap(local_guide)}
        if progress:
            progress(index + 1, len(parts))
        # Bare-part crops keep their dedicated protocol. The generic object
        # detail adapter cannot infer anatomy from these reduced patches.
        mask, quality = segment_target(patch, local_hint, [local_point], engine=engine,native_detail=False)
        alpha = raster_mask(mask, patch.size)
        support = alpha.point(lambda v: 255 if v else 0).resize((right-left, bottom-top), Image.Resampling.NEAREST)
        alpha = alpha.resize(support.size, Image.Resampling.BILINEAR)
        alpha.paste(0, mask=ImageChops.invert(support))
        # Overlapping parts form one range: the effect is not applied twice.
        combined.paste(ImageChops.lighter(combined.crop(box), alpha), (left, top))
        qualities.append({**quality, "crop_box": list(box), "crop_size": list(patch.size)})
        label = hint["label"]
    if not combined.getbbox():
        raise ValueError("身体皮肤范围为空，照片未改变")
    result = empty_mask()
    result.update(bitmap=encode_bitmap(combined, sampling="alpha", preserve_resolution=True),
                  label=(label + " · 身体局部")[:200])
    quality = {
        "model": "EfficientSAM-S · 原图局部", "semantic_target": "body_skin",
        "parts": qualities, "part_count": len(parts),
        "predicted_iou": min(q["predicted_iou"] for q in qualities),
        "warnings": list(dict.fromkeys(w for q in qualities for w in q.get("warnings", []))),
        "coverage": round((image.width*image.height-combined.histogram()[0])/(image.width*image.height)*100, 2),
        "elapsed_ms": round((perf_counter()-started)*1000, 1), "mask_size": list(image.size),
    }
    return result, quality
