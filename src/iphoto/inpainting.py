"""Content-aware fill via OpenCV inpainting; optional, capability-gated."""

from PIL import Image


def available():
    from importlib.util import find_spec

    return find_spec("cv2") is not None


def inpaint_image(image, region, params):
    """Synthesize `region` content from surrounding pixels; non-destructive."""
    import cv2
    import numpy as np

    rgb = np.asarray(image.convert("RGB"))
    bgr = rgb[..., ::-1].copy()
    mask = np.asarray(region.convert("L"), dtype=np.uint8)
    mask = np.where(mask > 8, 255, 0).astype(np.uint8)
    if not mask.any():
        return image.copy()
    flags = cv2.INPAINT_NS if params.get("method") == "ns" else cv2.INPAINT_TELEA
    radius = float(params.get("radius", 5))
    out = cv2.inpaint(bgr, mask, radius, flags)
    result = Image.fromarray(out[..., ::-1])
    if image.mode == "RGBA":
        result.putalpha(image.getchannel("A"))
    return result
