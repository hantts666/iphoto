"""Content-aware fill via OpenCV inpainting; optional, capability-gated."""

from PIL import Image
from math import ceil


def available():
    from importlib.util import find_spec

    return find_spec("cv2") is not None


def inpaint_image(image, region, params):
    """Synthesize `region` content from surrounding pixels; non-destructive."""
    import cv2
    import numpy as np

    if region.size != image.size:
        raise ValueError("修复范围尺寸必须与照片一致")
    binary = region.convert("L").point([0] * 9 + [255] * 247)
    bounds = binary.getbbox()
    if bounds is None:
        return image.copy()
    flags = cv2.INPAINT_NS if params.get("method") == "ns" else cv2.INPAINT_TELEA
    radius = float(params.get("radius", 5))
    # The solver needs the hole and nearby gradient/sampling context. A small
    # stroke must not allocate and solve a full source-size RGB array.
    margin = max(4, ceil(radius) * 2 + 4)
    left, top, right, bottom = bounds
    box = (max(0, left - margin), max(0, top - margin),
           min(image.width, right + margin), min(image.height, bottom + margin))
    full = box == (0, 0, image.width, image.height)
    source = image if full else image.crop(box)
    rgb = np.asarray(source.convert("RGB"))
    bgr = rgb[..., ::-1].copy()
    mask = np.asarray(binary if full else binary.crop(box), dtype=np.uint8)
    out = cv2.inpaint(bgr, mask, radius, flags)
    patch = Image.fromarray(out[..., ::-1])
    if image.mode == "RGBA":
        patch.putalpha(source.getchannel("A"))
    if full:
        return patch
    result = image.copy() if image.mode == "RGBA" else image.convert("RGB")
    result.paste(patch, box[:2])
    return result
