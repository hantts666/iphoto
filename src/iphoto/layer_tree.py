"""Layer hierarchy over stable flat records. Sibling order is bottom to top.

Parent IDs avoid recursive project JSON and keep layer lookup/undo cheap. Rendering
and the sidebar derive the same forest, so a visual group is a real composite.
"""

from copy import deepcopy
from uuid import uuid4

MAX_DEPTH = 4


def validate_hierarchy(layers):
    by_id = {layer["id"]: layer for layer in layers}
    for layer in layers:
        if layer.get("kind", "adjustment") not in ("adjustment", "group"):
            raise ValueError("未知图层类型")
        parent = layer.get("parent_id", "")
        if not isinstance(parent, str) or len(parent) > 64:
            raise ValueError("图层父组无效")
        if not isinstance(layer.get("collapsed", False), bool):
            raise ValueError("图层组折叠状态无效")
        seen = {layer["id"]}
        depth = 0
        while parent:
            if parent in seen:
                raise ValueError("图层组不能循环嵌套")
            if parent not in by_id or by_id[parent].get("kind") != "group":
                raise ValueError("图层父组不存在")
            seen.add(parent)
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("最多支持四级图层组嵌套")
            parent = by_id[parent].get("parent_id", "")
        if layer.get("kind") == "group" and depth >= MAX_DEPTH:
            raise ValueError("最多支持四级图层组嵌套")
    return layers


def forest(layers):
    nodes = {l["id"]: {"layer": l, "children": []} for l in layers}
    roots = []
    for layer in layers:
        parent = layer.get("parent_id", "")
        (nodes[parent]["children"] if parent else roots).append(nodes[layer["id"]])
    return roots


def descendants(layers, lid):
    result = {lid}
    for _ in range(MAX_DEPTH + 1):
        result.update(l["id"] for l in layers if l.get("parent_id") in result)
    return result


def display_rows(layers):
    rows = []

    def visit(nodes, depth, ancestors_visible):
        for node in reversed(nodes):
            layer = node["layer"]
            effective = ancestors_visible and layer["visible"]
            rows.append(
                {
                    **{k: layer[k] for k in ("id", "name", "visible", "opacity")},
                    "kind": layer.get("kind", "adjustment"),
                    "parent_id": layer.get("parent_id", ""),
                    "collapsed": layer.get("collapsed", False),
                    "depth": depth,
                    "effectiveVisible": effective,
                    "mask": {"label": layer["mask"]["label"]},
                    "childCount": len(node["children"]),
                }
            )
            if not layer.get("collapsed", False):
                visit(node["children"], depth + 1, effective)

    visit(forest(layers), 0, True)
    return rows


def duplicate_subtree(layers, lid):
    ids = descendants(layers, lid)
    mapping = {old: uuid4().hex for old in ids}
    copies = []
    for layer in layers:
        if layer["id"] not in ids:
            continue
        clone = deepcopy(layer)
        clone["id"] = mapping[layer["id"]]
        clone["parent_id"] = mapping.get(
            layer.get("parent_id", ""), layer.get("parent_id", "")
        )
        if layer["id"] == lid:
            clone["name"] = layer["name"][:75] + " 副本"
        copies.append(clone)
    return copies, mapping[lid]
