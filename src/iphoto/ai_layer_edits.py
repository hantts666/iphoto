"""Validate edits to offered layer identities, recipes and appearance controls."""

from math import isfinite

from .engine import Recipe, RANGES

LAYER_EDITS_SCHEMA = {
    "type": "array", "maxItems": 4,
    "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "layer_id": {"type": "string"},
            "face_id": {"type": ["string", "null"]},
            "recipe": {"anyOf": [{
                "type": "object", "additionalProperties": False,
                "properties": {k: {"type": "number", "minimum": lo, "maximum": hi} for k, (lo, hi) in RANGES.items()},
                "required": list(RANGES),
            }, {"type": "null"}]},
            "visible": {"type": ["boolean", "null"]},
            "opacity": {"anyOf": [{"type": "number", "minimum": 0, "maximum": 1}, {"type": "null"}]},
        },
        "required": ["layer_id", "face_id", "recipe", "visible", "opacity"],
    },
}


def validate_layer_controls(edit, layer):
    visible = edit.get("visible")
    if visible is None:
        visible = layer["visible"]
    if not isinstance(visible, bool):
        raise ValueError("图层显示状态必须是 true、false 或 null")
    opacity = edit.get("opacity")
    if opacity is None:
        opacity = layer["opacity"]
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValueError("图层不透明度必须是 0～1 的有限数值；50% 应写为 0.5")
    return visible, float(opacity)


def validate_layer_edits(value, offered):
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError("已有图层修改需要 1～4 个明确目标")
    targets = {l["id"]: l for l in (offered or []) if l.get("kind", "adjustment") in ("adjustment", "group") and "id" in l}
    seen, result = set(), []
    for edit in value:
        if not isinstance(edit, dict) or "layer_id" not in edit or set(edit) - {"layer_id", "face_id", "recipe", "visible", "opacity"}:
            raise ValueError("已有图层的修改字段无效")
        if all(edit.get(key) is None for key in ("recipe", "visible", "opacity")):
            raise ValueError("已有图层修改没有指定参数、显示状态或不透明度")
        lid = edit["layer_id"]
        if not isinstance(lid, str) or lid not in targets or lid in seen:
            raise ValueError("修改目标不存在或重复；请使用现有图层 id")
        seen.add(lid)
        layer = targets[lid]
        face_id = edit.get('face_id')
        face_target = layer.get('face_target')
        if face_target:
            if not isinstance(face_id,str) or face_id != face_target['id']:
                raise ValueError('人脸身份与已有图层不匹配；请同时使用该层的 layer_id 和 face_target.id')
        elif face_id is not None:
            raise ValueError('该图层没有已验证的人脸关联，face_id 必须为 null')
        recipe = edit.get("recipe")
        if layer.get("kind") == "group":
            if recipe is not None:
                raise ValueError("图层组只能修改显示状态和整体不透明度，recipe 必须为 null")
            visible, opacity = validate_layer_controls(edit, layer)
            result.append({"layer_id": lid, "recipe": None, "visible": visible,
                           "opacity": opacity, "preserved_locked": [],
                           "requested_controls": [k for k in ("visible", "opacity") if edit.get(k) is not None]})
            continue
        if recipe is None:
            recipe = layer["recipe"]
        if not isinstance(recipe, dict) or set(recipe) != set(RANGES):
            raise ValueError("已有图层的修改参数不完整")
        validated = Recipe.from_dict(recipe).to_dict()
        visible, opacity = validate_layer_controls(edit, layer)
        kept = []
        for key in layer.get("locked", []):
            if key in RANGES:
                if validated[key] != layer["recipe"][key]:
                    kept.append(key)
                validated[key] = layer["recipe"][key]
        result.append({"layer_id": lid, "recipe": validated, "visible": visible,
                       "opacity": opacity, "preserved_locked": kept,
                       "requested_controls": [k for k in ("visible", "opacity") if edit.get(k) is not None]})
    return result
