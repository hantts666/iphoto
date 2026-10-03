"""Read live local masks for inspection navigation, without editing documents."""
from PIL import ImageChops

from ..document import raster_mask_cached


def reviewable(layer):
    mask = layer['mask']
    full = not mask['ops'] and 'bitmap' not in mask and (mask['base'] == 'full') != mask['inverted']
    return (layer.get('kind') == 'adjustment' and not full and not layer.get('heal')
            and any(layer['recipe'].values()))


def live_layers(editor, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 4:
        return []
    by_id = {layer['id']: layer for layer in editor._layers}
    return [by_id[lid] for lid in dict.fromkeys(lid for lid in ids if isinstance(lid, str))
            if lid in by_id and reviewable(by_id[lid])]


def targets(editor, ids):
    """At most four interior inspection points per requested live layer.

    Connected ranges are navigation targets, not anatomical classifications.
    Small speckles are omitted; the original masks remain untouched.
    """
    import cv2
    import numpy as np

    by_id = {layer['id']: layer for layer in editor._layers}
    scale = 512 / max(editor._width, editor._height)
    size = (max(1, round(editor._width * scale)), max(1, round(editor._height * scale)))
    result = []
    for layer in live_layers(editor, ids):
        alpha = raster_mask_cached(layer['mask'], size)
        versions = [(layer['id'], id(layer['mask']))]
        parent = layer.get('parent_id', '')
        while parent:
            group = by_id[parent]
            alpha = ImageChops.multiply(alpha, raster_mask_cached(group['mask'], size))
            versions.append((parent, id(group['mask'])))
            parent = group.get('parent_id', '')
        hard = (np.asarray(alpha) > 0).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(hard, connectivity=8)
        if count <= 1:
            continue
        largest = int(stats[1:, cv2.CC_STAT_AREA].max())
        candidates = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= max(1, largest * .01)]
        candidates.sort(key=lambda i: (-stats[i, cv2.CC_STAT_AREA], i))
        for i in candidates[:4]:
            # The most interior point avoids centering on an eye/phone hole or
            # on empty space between disconnected parts. Padding also handles
            # a range touching every true photo edge.
            component = np.pad((labels == i).astype(np.uint8), 1)
            distance = cv2.distanceTransform(component, cv2.DIST_L2, 5)[1:-1, 1:-1]
            y, x = np.unravel_index(distance.argmax(), distance.shape)
            # Python numeric scalars are required at the QObject boundary.
            # NumPy scalars can become opaque QVariant values in visibleRect.
            result.append((layer, ((float(x) + .5) / size[0], (float(y) + .5) / size[1]), tuple(versions)))
    return result
