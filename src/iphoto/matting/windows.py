"""Bounded native context for unknown pixels whose tile lacks known references."""
import cv2
import numpy as np

REFERENCE_RADIUS = 4
_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))


def _has_reference(area, value):
    # An isolated prompt or a thin sliver is not an opaque colour reference.
    return bool(cv2.erode((area == value).astype(np.uint8), _KERNEL,
                          borderType=cv2.BORDER_CONSTANT, borderValue=0).any())


def context_box(trimap, core, unknown_box, *, limit, halo):
    """Keep ordinary halos, or move a native window to include both known classes.

    Every unknown core pixel must fit in the window. Search is the union of
    feasible windows, at most (2*limit-1)^2 pixels even for a large photograph.
    No new constraints are created; an unavailable reference keeps the old box.
    """
    height, width = trimap.shape
    x, y, x1, y1 = core
    original = (max(0, x-halo), max(0, y-halo),
                min(width, x1+halo), min(height, y1+halo))
    area = trimap[original[1]:original[3], original[0]:original[2]]
    foreground, background = _has_reference(area, 255), _has_reference(area, 0)
    if foreground and background:
        return original

    left, top, right, bottom = unknown_box
    xmin, xmax = max(0, right-limit), min(left, max(0, width-limit))
    ymin, ymax = max(0, bottom-limit), min(top, max(0, height-limit))
    search = trimap[ymin:min(height, ymax+limit), xmin:min(width, xmax+limit)]
    hard = cv2.erode((search == (255 if not foreground else 0)).astype(np.uint8),
                    _KERNEL, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    rows, columns = np.nonzero(hard)
    columns, rows = columns+xmin, rows+ymin
    radius = REFERENCE_RADIUS
    allowed = ((columns >= xmin+radius) & (columns < xmax+limit-radius)
               & (rows >= ymin+radius) & (rows < ymax+limit-radius))
    columns, rows = columns[allowed], rows[allowed]
    if not columns.size:
        return original
    nearest = np.argmin((columns-(left+right-1)/2)**2
                        + (rows-(top+bottom-1)/2)**2)
    fx, fy = int(columns[nearest]), int(rows[nearest])
    start_x = int(np.clip(round((min(left, fx-radius)+max(right, fx+radius+1)-limit)/2),
                          max(xmin, fx+radius+1-limit), min(xmax, fx-radius)))
    start_y = int(np.clip(round((min(top, fy-radius)+max(bottom, fy+radius+1)-limit)/2),
                          max(ymin, fy+radius+1-limit), min(ymax, fy-radius)))
    shifted = (start_x, start_y, min(width, start_x+limit), min(height, start_y+limit))
    area = trimap[shifted[1]:shifted[3], shifted[0]:shifted[2]]
    return shifted if _has_reference(area, 255) and _has_reference(area, 0) else original
