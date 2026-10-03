"""Bounded group creation over contiguous, existing sibling identities."""

from .ai_layer_edits import validate_layer_controls
from .document import MAX_LAYERS
from .layer_tree import validate_hierarchy


GROUP_SCHEMA = {
    "anyOf": [{
        "type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 80},
            "layer_ids": {"type": "array", "minItems": 1, "maxItems": MAX_LAYERS - 1,
                          "items": {"type": "string"}},
            "visible": {"type": "boolean"},
            "opacity": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["name", "layer_ids", "visible", "opacity"],
    }, {"type": "null"}],
}


def validate_group_plan(value, offered):
    if not isinstance(value, dict) or set(value) != {"name", "layer_ids", "visible", "opacity"}:
        raise ValueError("编组需要名称、已有图层 id、显示状态和整体不透明度")
    name = value["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("图层组名称应为 1～80 字")
    ids = value["layer_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) < MAX_LAYERS:
        raise ValueError("编组需要 1～31 个已有图层")
    if any(not isinstance(lid, str) for lid in ids) or len(set(ids)) != len(ids):
        raise ValueError("编组目标 id 无效或重复")
    offered = offered or []
    by_id = {layer["id"]: layer for layer in offered}
    if any(lid not in by_id for lid in ids):
        raise ValueError("编组目标不存在，请使用现有图层 id")
    if len(offered) >= MAX_LAYERS:
        raise ValueError("图层与组最多 32 项，当前没有新组的位置")
    parents = {by_id[lid].get("parent_id", "") for lid in ids}
    if len(parents) != 1:
        raise ValueError("只能把同一父组下的图层编组，不能改变原来的嵌套范围")
    parent = parents.pop()
    siblings = [layer["id"] for layer in offered if layer.get("parent_id", "") == parent]
    positions = sorted(siblings.index(lid) for lid in ids)
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError("编组目标之间夹有其他图层，直接编组会改变叠加顺序；请先整理顺序")
    if value["visible"] is None or value["opacity"] is None:
        raise ValueError("新组必须指定显示状态和 0～1 的整体不透明度")
    visible, opacity = validate_layer_controls(value, {"visible": True, "opacity": 1.0})
    # Validate depth for whole subtrees without copying masks or recipes.
    group_id = "ai_new_group"
    while group_id in by_id:
        group_id += "_"
    nodes = [{k: layer[k] for k in ("id", "kind", "parent_id")} for layer in offered]
    for node in nodes:
        if node["id"] in ids:
            node["parent_id"] = group_id
    nodes.append({"id": group_id, "kind": "group", "parent_id": parent})
    validate_hierarchy(nodes)
    return {"name": name.strip(), "layer_ids": [siblings[p] for p in positions],
            "visible": visible, "opacity": opacity}
