"""Refine one face's internal skin features; retain the outer 19-class domain."""
import cv2
import numpy as np

FACE_CLASSES = (1, 2, 3, 4, 5, 10, 11, 12, 13)
OUTER_PROTECTED = (6, 9, 14, 15, 16, 17, 18)  # glasses/jewelry/neck/cloth/hair/hat
MAX_PIXELS = 16_000_000


def refine(original, basic, precise, skin_alpha, selected, context):
    arrays = (original, basic, precise, skin_alpha, context)
    if (not all(isinstance(value, np.ndarray) and value.ndim == 2
                and value.shape == original.shape and value.dtype == np.uint8 for value in arrays)
            or not isinstance(selected, np.ndarray) or selected.shape != original.shape
            or selected.dtype != bool or original.size > MAX_PIXELS
            or basic.max(initial=0) > 18 or precise.max(initial=0) > 18
            or np.any(skin_alpha[~np.isin(precise, (1, 10))])):
        raise ValueError('精细面部皮肤分区无效，保持基础范围')
    _, components = cv2.connectedComponents(np.isin(precise, FACE_CLASSES).astype(np.uint8), connectivity=8)
    # Both neural models must agree on the nose of this selected person.
    # Never associate an arbitrary skin blob or a second face in the crop.
    shared = np.unique(components[selected & (basic == 10) & (precise == 10) & (context > 0)])
    shared = shared[shared != 0]
    if len(shared) != 1:
        raise ValueError('精细五官与当前人脸对应不明确，保持基础范围')
    inner = (components == shared[0]) & selected & (context > 0) & np.isin(basic, FACE_CLASSES)
    if not np.any(skin_alpha[inner]):
        raise ValueError('精细模型未确认可见皮肤，保持基础范围')
    # LaPa has no independent hat/neck/cloth/glasses/earring classes. Those
    # regions and the 19-class person's outer boundary remain authoritative.
    guard = cv2.dilate(np.isin(basic, OUTER_PROTECTED).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    distance = cv2.distanceTransform((selected & ~guard).astype(np.uint8), cv2.DIST_L2, 5)
    outer = np.rint(np.minimum(distance / 2, 1) * 255).astype(np.uint8)
    result = original.copy()
    result[inner] = np.minimum(outer, skin_alpha)[inner]
    return result
