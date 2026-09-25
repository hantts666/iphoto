"""Build explicit foreground / unknown / background constraints."""

import cv2
import numpy as np


def make_trimap(alpha, radius):
    """Re-open a narrow boundary band while retaining distant known pixels.

    Radius is measured in input pixels, not a feather amount. Existing partial
    coverage is unknown even if it is farther from the thresholded contour.
    """
    if type(radius) is not int or not 1 <= radius <= 64:
        raise ValueError("边缘范围应为 1～64 像素")
    alpha = np.asarray(alpha, dtype=np.uint8)
    if alpha.ndim != 2 or min(alpha.shape) < 8:
        raise ValueError("照片太小，无法细化透明边缘")
    hard = (alpha >= 128).astype(np.uint8)
    if not hard.any() or hard.all():
        raise ValueError("先选中一部分目标；全选或空选区无法细化边缘")
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)
    foreground = cv2.erode(hard, kernel, borderType=cv2.BORDER_REPLICATE) > 0
    background = cv2.erode(1 - hard, kernel, borderType=cv2.BORDER_REPLICATE) > 0
    foreground &= alpha == 255
    background &= alpha == 0
    # A small, already definite hole must not disappear just because the
    # erosion kernel is wider than it. Keep its deepest existing known pixel.
    for known, definite in ((foreground, alpha == 255), (background, alpha == 0)):
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            definite.astype(np.uint8), connectivity=8
        )
        for label in range(1, count):
            x, y, w, h, area = stats[label]
            if area < 4:
                continue
            component = labels[y : y + h, x : x + w] == label
            if np.any(known[y : y + h, x : x + w] & component):
                continue
            distance = cv2.distanceTransform(
                np.pad(component.astype(np.uint8), 1), cv2.DIST_L2, 5
            )[1:-1, 1:-1]
            cy, cx = np.unravel_index(distance.argmax(), distance.shape)
            known[y + cy, x + cx] = True
    if not foreground.any() or not background.any():
        raise ValueError("缺少可靠的内部或背景，请先补选、减选后再细化")
    trimap = np.full(alpha.shape, 0.5, dtype=np.float32)
    trimap[background] = 0
    trimap[foreground] = 1
    if np.count_nonzero(trimap == 0.5) > 4_000_000:
        raise ValueError("待判断的边缘过宽，请减小边缘范围或先修正选区")
    return trimap
