"""Conservative local RGB-guided alpha edges; not a hair-matting model."""

import cv2
import numpy as np
from PIL import Image


def guided_edge(image, hard, radius=4):
    """Only change the uncertain boundary band; preserve holes and known support.

    Local foreground/background colors estimate opacity near existing edges.
    Ambiguous/similar colors retain the neural binary mask instead of inventing
    a confident transparent edge. Does not move distant boundaries or fill holes.
    """
    hard = np.asarray(hard, np.uint8)
    if hard.max(initial=0) > 1:
        hard = (hard > 127).astype(np.uint8)
    rgb = np.asarray(image.convert("RGB"), np.float32) / 255
    radius = max(1, min(12, int(radius)))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
    )
    foreground = cv2.erode(hard, kernel, borderType=cv2.BORDER_REPLICATE).astype(
        np.float32
    )
    support = cv2.dilate(hard, kernel, borderType=cv2.BORDER_REPLICATE)
    background = (1 - support).astype(np.float32)
    window = (radius * 6 + 1,) * 2

    def mean_color(weight):
        count = cv2.boxFilter(
            weight, -1, window, normalize=False, borderType=cv2.BORDER_REFLECT
        )
        mean = cv2.boxFilter(
            rgb * weight[:, :, None],
            -1,
            window,
            normalize=False,
            borderType=cv2.BORDER_REFLECT,
        ) / np.maximum(count[:, :, None], 1)
        return mean, count

    fg, fc = mean_color(foreground)
    bg, bc = mean_color(background)
    delta = fg - bg
    separation = (delta * delta).sum(axis=2)
    estimate = ((rgb - bg) * delta).sum(axis=2) / np.maximum(separation, 1e-6)
    band = (
        (support > 0) & (foreground == 0) & (fc > 2) & (bc > 2) & (separation > 0.015)
    )
    alpha = hard.astype(np.float32)
    alpha[band] = np.clip(estimate[band], 0, 1)
    alpha[foreground > 0] = 1
    alpha[support == 0] = 0
    return Image.fromarray(np.rint(alpha * 255).astype(np.uint8))


def native_edge(image, mask, radius=4):
    """Source RGB edges with bounded working RGB buffers and overlapping context.

    This preserves the rough topology. It is a fallback when alpha matting
    cannot be solved, not evidence that missing hairs or holes were recovered.
    """
    from ..document import empty_mask, raster_mask
    from ..masks import encode_bitmap

    radius = max(1, min(12, int(radius)))
    seed = raster_mask(mask, image.size)
    pixels = np.empty((image.height, image.width), dtype=np.uint8)
    # The color window and morphological band fit inside this halo even at
    # radius 12. Retain only the core, avoiding artificial tile-edge colors.
    halo, tile_size = 64, 512
    for y in range(0, image.height, tile_size):
        for x in range(0, image.width, tile_size):
            right, bottom = min(x + tile_size, image.width), min(y + tile_size, image.height)
            left, top = max(0, x - halo), max(0, y - halo)
            far_right, far_bottom = min(image.width, right + halo), min(image.height, bottom + halo)
            box = (left, top, far_right, far_bottom)
            hard = np.asarray(seed.crop(box)) > 127
            if not hard.any() or hard.all():
                pixels[y:bottom, x:right] = hard[y-top:bottom-top, x-left:right-left].astype(np.uint8)*255
                continue
            local = guided_edge(image.crop(box), hard, radius)
            pixels[y:bottom, x:right] = np.asarray(local)[y-top:bottom-top, x-left:right-left]
    result = empty_mask()
    result.update(bitmap=encode_bitmap(Image.fromarray(pixels), sampling="alpha", preserve_resolution=True),
                  label=mask["label"])
    return result
