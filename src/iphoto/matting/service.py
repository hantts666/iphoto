"""Mask refinement entry point; no Qt objects and no changes to source RGB."""

from copy import deepcopy
from time import perf_counter
import numpy as np
from PIL import Image

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from .trimap import make_trimap
from .solver import solve_alpha
from .metadata import copy_metadata


def refine_alpha(image, mask, radius=8, *, linear=True):
    started = perf_counter()
    # The source loader already supplies RGB for ordinary photos. Avoid a
    # second 180 MB image allocation on a 60 MP source.
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    seed = deepcopy(validate_mask(mask))
    # A user's feather is an adjustment envelope, not evidence about coverage.
    # Re-estimate the unfeathered edge and output it without extra blur.
    seed["feather"] = 0
    original = raster_mask(seed,rgb.size)
    trimap = make_trimap(original, radius)
    if seed.get("semantic_target") in ("face","face_skin","body_skin"):
        trimap[np.asarray(original)==0] = 0
    del original
    pixels, tiles = solve_alpha(rgb, trimap, byte_output=True, linear=linear)
    unknown_pixels = int(np.count_nonzero(trimap == 0.5))
    del trimap
    partial_pixels = int(np.count_nonzero((pixels > 0) & (pixels < 255)))
    result = copy_metadata(seed, empty_mask())
    result.update(
        bitmap=encode_bitmap(Image.fromarray(pixels), sampling="alpha", preserve_resolution=True),
        label=mask["label"],
    )
    return result, {
        "backend": "PyMatting · Closed-form",
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "radius": radius,
        "tiles": tiles,
        "mask_size": list(rgb.size),
        "partial_pixels": partial_pixels,
        "unknown_pixels": unknown_pixels,
        "warnings": ["已重新估计边缘透明度并取消额外羽化，请对照照片检查"],
    }
