"""Convert coarse semantic hints to bounded model prompts, never final masks."""

import cv2
import numpy as np


def validate_points(points):
    if not isinstance(points, list) or len(points) > 6:
        raise ValueError("一次最多 6 个正/负提示点；可移除旧点后继续修正")
    clean = []
    for point in points:
        if (
            not isinstance(point, list)
            or len(point) != 3
            or any(type(v) not in (int, float) or not np.isfinite(v) for v in point)
            or not 0 <= point[0] <= 1
            or not 0 <= point[1] <= 1
            or point[2] not in (0, 1)
        ):
            raise ValueError("分割提示点无效")
        clean.append([float(point[0]), float(point[1]), int(point[2])])
    return clean


def from_hint(hint, points=None):
    """Six tokens maximum: box corners + robust interior/negative anchors.

    Model-generated polygons are only hints. No intersection with their outline
    is applied to the prediction, so real boundaries can grow beyond a bad hint.
    """
    hard = np.asarray(hint) > 127
    height, width = hard.shape
    ys, xs = np.where(hard)
    if not len(xs):
        raise ValueError("没有可定位的目标范围，请框选或点击目标")
    points = validate_points(points or [])
    if points:
        # User corrections take precedence over inferred anchors. Box keeps the
        # intended target's scale when fewer than five user points are present.
        coords = [[p[0] * (width - 1), p[1] * (height - 1)] for p in points]
        labels = [p[2] for p in points]
    else:
        # Image borders count as boundaries too. Without the zero padding a
        # sky hint touching two edges can place its anchor in the top corner.
        padded = np.pad(hard.astype(np.uint8), 1)
        distance = cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1]
        _, _, _, strongest = cv2.minMaxLoc(distance)
        coords, labels = [list(strongest)], [1]
    if len(coords) <= 4:
        # A modest box margin compensates for imprecise VLM localization.
        pad = max(3, round(min(height, width) * 0.015))
        positive = [p for p, label in zip(coords, labels) if label == 1]
        left = min([int(xs.min())] + [p[0] for p in positive])
        top = min([int(ys.min())] + [p[1] for p in positive])
        right = max([int(xs.max())] + [p[0] for p in positive])
        bottom = max([int(ys.max())] + [p[1] for p in positive])
        coords += [
            [max(0, left - pad), max(0, top - pad)],
            [min(width - 1, right + pad), min(height - 1, bottom + pad)],
        ]
        labels += [2, 3]
    return np.asarray(coords, np.float32), np.asarray(labels, np.float32)


def from_points(points, size):
    points = validate_points(points)
    if not points or not any(p[2] for p in points):
        raise ValueError("请先在目标内部添加一个保留点")
    width, height = size
    return (
        np.asarray(
            [[p[0] * (width - 1), p[1] * (height - 1)] for p in points], np.float32
        ),
        np.asarray([p[2] for p in points], np.float32),
    )
