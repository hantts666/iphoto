"""Bounded color-connected correction for small negative prompt regions.

A neural mask can fill a small hole even after a negative prompt. Only remove
the connected, seed-colored part inside its existing support. Never expand the
mask, cross positive prompts, or use a large flood as a segmentation fallback.
"""

import cv2
import numpy as np


def correct_negatives(image, hard, coords, labels):
    height, width = hard.shape
    points = [
        (min(width - 1, round(float(x))), min(height - 1, round(float(y))), label)
        for (x, y), label in zip(coords, labels)
        if label in (0, 1)
    ]
    if any(not hard[y, x] for x, y, label in points if label == 1):
        return hard, 0
    out = hard.copy()
    rgb = np.asarray(image.convert("RGB")).copy()
    removed = 0
    budget = min(hard.size * 0.05, hard.sum() * 0.20)
    for x, y, label in points:
        if label != 0 or not out[y, x]:
            continue
        buffer = np.pad((~out).astype(np.uint8), 1, constant_values=1)
        cv2.floodFill(
            rgb,
            buffer,
            (x, y),
            0,
            (16,) * 3,
            (16,) * 3,
            4 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (255 << 8),
        )
        region = buffer[1:-1, 1:-1] == 255
        count = int(region.sum())
        if count < 4 or removed + count > budget:
            continue
        if any(region[py, px] for px, py, lab in points if lab == 1):
            continue
        out[region] = False
        removed += count
    return out, removed
