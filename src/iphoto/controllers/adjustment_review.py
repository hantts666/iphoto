"""Read live local masks for inspection navigation, without editing documents."""
from PIL import Image, ImageChops

from ..document import raster_mask_cached


def reviewable(layer):
    mask = layer['mask']
    full = not mask['ops'] and 'bitmap' not in mask and (mask['base'] == 'full') != mask['inverted']
    return (layer.get('kind') == 'adjustment' and not layer.get('heal')
            and (bool(layer.get('pixel_patch')) or not full and any(layer['recipe'].values())))


def content_scale(editor, layer):
    """Inspect applied generated pixels without magnifying a low-resolution patch.

    Generated detail is bounded by the final source canvas as well as its own
    raster. This is a navigation scale, not an assertion of original fidelity.
    """
    patch = layer.get('pixel_patch')
    if not patch:
        return 1., None
    from ..pixel_patch import _decode
    image = _decode(patch['png'])
    width = (patch['box'][2]-patch['box'][0])*editor._width/patch['canvas_size'][0]
    height = (patch['box'][3]-patch['box'][1])*editor._height/patch['canvas_size'][1]
    return min(1.,image.width/width,image.height/height), image.size


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
        patch = layer.get('pixel_patch')
        if patch:
            from ..pixel_patch import _decode
            pixels = _decode(patch['png'])
            box = [round(v*size[i%2]/patch['canvas_size'][i%2]) for i,v in enumerate(patch['box'])]
            if box[0] >= box[2] or box[1] >= box[3]:
                continue
            scope = Image.new('L',size)
            scope.paste(255,tuple(box))
            alpha = ImageChops.multiply(alpha,scope)
            versions.append(('pixels',patch['sha256'],tuple(patch['canvas_size']),tuple(patch['box'])))
            if patch.get('compositing') == 'replace_rgba':
                support = Image.new('L',size)
                support.paste(pixels.getchannel('A').resize((box[2]-box[0],box[3]-box[1]),Image.Resampling.LANCZOS),box[:2])
                visible = ImageChops.multiply(alpha,support)
                values = np.asarray(visible)
                # Almost invisible alpha speckles can extend across the full
                # canvas. Ignore them only for navigation; never alter alpha.
                navigation = values >= 16 if np.any(values >= 16) else values > 0
                count, labels, stats, _ = cv2.connectedComponentsWithStats(navigation.astype(np.uint8),connectivity=8)
                if count > 1:
                    component = 1+int(stats[1:,cv2.CC_STAT_AREA].argmax())
                    left,top,width,height,_ = stats[component]
                    # These are only navigation stops on the actual silhouette,
                    # not semantic hair/skin detections or quality evidence.
                    points = []
                    for fraction in (.25,.5):
                        y = min(int(top+height-1),round(float(top+height*fraction)))
                        row = np.flatnonzero(labels[y] == component)
                        for x in (int(row[0]),int(row[-1])) if row.size else ():
                            point = ((x+.5)/size[0],(y+.5)/size[1])
                            if point not in points:
                                points.append(point)
                    if points:
                        result.extend((layer,point,tuple(versions)) for point in points)
                        continue
                # A completely transparent generated result still needs a
                # way to inspect its affected canvas and undo it.
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
