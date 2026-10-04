"""One document-bound face inventory for shortcuts and AI planning."""
from copy import deepcopy

from ..ai_grounding import region_crop
from ..scene import with_local_faces


def current(editor):
    cached = getattr(editor, "_face_inventory_cache", None)
    if cached and cached[0] is editor._face_hints and cached[1] is editor._scene.catalog:
        return cached[2]
    local = [{"category": "人脸", **face} for face in editor._face_hints]
    catalog = with_local_faces(editor._scene.catalog, local)
    faces = []
    for obj in (catalog or {}).get("objects", []):
        # A cheek/skin patch is not a complete face identity. Do not offer it
        # as a whole-face shortcut or infer a second face from its category.
        if obj.get("mask_target") != "face" or "anchor" not in obj:
            continue
        face = deepcopy(obj)
        if "skin_crop" not in face:
            face["skin_crop"] = region_crop(face["mask"], (384, 384))
        faces.append(face)
    editor._face_inventory_cache = (editor._face_hints, editor._scene.catalog, faces)
    return faces


def spatial_context(editor):
    names = {item['id']:item['name'] for item in choices(editor)}
    return [{**{k: face[k] for k in ("id", "name", "anchor", "skin_crop")},
             'display_name':names[face['id']]} for face in current(editor)]


def choices(editor):
    """Locate a face in the menu without renaming its saved identity or layer."""
    faces = current(editor)
    if len(faces) < 2:
        return [{"id":face["id"],"name":face["name"]} for face in faces]
    points = [face['anchor'] for face in faces]
    width = (max(p[0] for p in points)-min(p[0] for p in points))*getattr(editor,'_width',1)
    height = (max(p[1] for p in points)-min(p[1] for p in points))*getattr(editor,'_height',1)
    axis = 0 if width >= height else 1
    order = sorted(range(len(faces)),key=lambda i:(points[i][axis],faces[i]['id']))
    ranks = {index:rank for rank,index in enumerate(order)}
    result = []
    for index,face in enumerate(faces):
        rank = ranks[index]
        if len(faces)==2:
            position = ('左侧','右侧')[rank] if axis==0 else ('上方','下方')[rank]
        else:
            position = f"从{'左' if axis==0 else '上'}第{rank+1}张"
        result.append({'id':face['id'],'name':face['name']+' · '+position})
    return result


def binding(editor, face):
    return {'face_id':face['id'],'source_sha256':editor._sha}


def _matches(editor, layer, face):
    mask = layer['mask']
    if (layer['kind']!='adjustment' or layer.get('inpaint') or layer.get('heal')
            or mask.get('semantic_target')!='face_skin' or mask['inverted']):
        return False
    if 'face_binding' in mask:
        return mask['face_binding']==binding(editor,face)
    return mask['label']==face['mask']['label']+' · 面部皮肤'


def layer_targets(editor):
    """Offer verified face associations without source hashes or mask payloads."""
    faces = current(editor)
    names = {item['id']:item['name'] for item in choices(editor)}
    targets = {}
    for layer in editor._layers:
        matches = [face for face in faces if _matches(editor,layer,face)]
        if len(matches)==1:
            face = matches[0]
            targets[layer['id']] = {'id':face['id'],'name':face['name'],
                                   'display_name':names[face['id']],'mask_target':'face_skin'}
    return targets


def retouch_layer(editor, face):
    """Prefer the chosen face layer; a name is not its persistent identity."""
    layers = [editor._layer(),*reversed(editor._layers)]
    for layer in layers:
        if _matches(editor,layer,face):
            return layer
    return None


def grounding_context(editor, anchor):
    """A unique face containing a point can guide a second visual lookup.

    This is only a crop proposal; it never supplies the edited pixel extent.
    """
    from ..document import raster_mask
    if anchor is None:
        return None
    x, y = (min(383, round(v * 384)) for v in anchor)
    matches = [face for face in current(editor)
               if raster_mask(face['mask'], (384, 384)).getpixel((x, y)) > 0]
    return matches[0] if len(matches) == 1 else None
