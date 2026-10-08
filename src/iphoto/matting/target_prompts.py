"""Tell the trimap model which visible parts do not belong to the hair target."""
import cv2
import numpy as np


def part_exclusions(labels, points):
    """Use bounded internal prompts for deep nonhair interiors, never edges.

    A positive hair point with only background negatives can describe the
    entire person. Head parsing supplies a few negative part observations to
    resolve that ambiguity before the learned uncertainty is computed. These
    observations guide the model; they do not expand hard exclusion masks.
    Explicit P/N/T points retain their order, roles and eight-point budget.
    Up to three internal observations have a separate bounded model budget.
    """
    candidates = []
    for label in (*range(1, 17), 18):
        part = (labels == label).astype(np.uint8)
        if not part.any():
            continue
        distance = cv2.distanceTransform(np.pad(part, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
        y, x = np.unravel_index(distance.argmax(), distance.shape)
        # Keep generated observations away from uncertain part transitions,
        # crop borders and every explicit observation (including fine wisps).
        if distance[y, x] < 12 or any((x-px)**2 + (y-py)**2 < 32**2
                                     for px, py, _ in points):
            continue
        candidates.append((int(part.sum()), int(x), int(y)))
    candidates.sort(reverse=True)
    return [[x, y, 0] for _, x, y in candidates[:3]]
