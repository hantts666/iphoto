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
