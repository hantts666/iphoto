"""Mask refinement entry point; no Qt objects and no changes to source RGB."""

from copy import deepcopy
from time import perf_counter
import numpy as np
from PIL import Image

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from .trimap import make_trimap
from .solver import solve_alpha


def refine_alpha(image, mask, radius=8):
    started = perf_counter()
    proxy = image.convert("RGB")
    seed = deepcopy(validate_mask(mask))
    # A user's feather is an adjustment envelope, not evidence about coverage.
    # Re-estimate the unfeathered edge and output it without extra blur.
    seed["feather"] = 0
    trimap = make_trimap(raster_mask(seed, proxy.size), radius)
    alpha, tiles = solve_alpha(proxy, trimap)
    pixels = np.rint(alpha * 255).astype(np.uint8)
    result = empty_mask()
    if "edge_protection" in mask:
        result["edge_protection"] = mask["edge_protection"]
    result.update(
        bitmap=encode_bitmap(Image.fromarray(pixels), sampling="alpha", preserve_resolution=True),
        label="透明边缘 · " + mask["label"][:180],
    )
    return result, {
        "backend": "PyMatting · Closed-form",
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "radius": radius,
        "tiles": tiles,
        "mask_size": list(proxy.size),
        "partial_pixels": int(((pixels > 0) & (pixels < 255)).sum()),
        "unknown_pixels": int((trimap == 0.5).sum()),
        "warnings": ["已重新估计边缘透明度并取消额外羽化，请对照照片检查"],
    }
