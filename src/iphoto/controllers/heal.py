"""Healing brush strokes: non-destructive, per-layer content repair."""

from copy import deepcopy
from math import hypot

from ..document import empty_mask, validate_mask, validate_layers, new_layer, MAX_LAYERS, MIN_STROKE_RADIUS
from ..inpainting import available
from ..layer_tree import display_states, validate_hierarchy


def _stroke_mask(ops):
    return {**empty_mask(), "label": "修复笔画",
            "ops": [{**op, "kind": "brush", "mode": "add"} for op in ops]}


def _owns_stroke_mask(layer):
    mask = layer["mask"]
    if "bitmap" in mask:
        return False
    expected = _stroke_mask((layer.get("heal") or {}).get("ops", []))
    return ({k:v for k,v in mask.items() if k != "label"}
            == {k:v for k,v in expected.items() if k != "label"})


def removes_empty_layer(self, layer):
    return (len((layer.get("heal") or {}).get("ops", [])) == 1
            and not any(layer["recipe"].values()) and not layer.get("inpaint")
            and _owns_stroke_mask(layer)
            and any(other['id'] != layer['id'] and other['kind'] == 'adjustment' for other in self._layers))


def review_targets(self, layer_ids):
    """Read current strokes by stable IDs, never by an old message's names."""
    if not isinstance(layer_ids, list) or len(layer_ids) > MAX_LAYERS:
        return []
    by_id = {layer['id']: layer for layer in self._layers}
    return [(by_id[lid], op) for lid in dict.fromkeys(lid for lid in layer_ids if isinstance(lid, str))
            if lid in by_id and by_id[lid].get('kind') != 'group'
            for op in (by_id[lid].get('heal') or {}).get('ops', [])]


def activeRepairInfo(self):
    targets = review_targets(self, [self._selected])
    if not targets:
        return {'count': 0}
    state = display_states(self._layers)[self._selected]
    layer = targets[0][0]
    return {'count': len(targets), 'strength': self.layerOpacity,
            'displayed': state['enabled'], 'effective_strength': state['opacity'] * 100,
            'isolated': not any(layer['recipe'].values()) and not layer.get('inpaint')}


def _validate_stroke(points, radius):
    proxy = {
        "base": "empty",
        "inverted": False,
        "feather": 0.0,
        "label": "修复",
        "ops": [{"kind": "brush", "mode": "add", "points": points, "radius": radius}],
    }
    validate_mask(proxy)


def paintRepair(self, points, radius):
    """A user brush stroke owns a separate visible repair result.

    Existing document-level drawHeal still appends to an explicit layer. This
    intent chooses a repair-only root layer before making one atomic edit.
    """
    if self.busy:
        return False
    if self._candidate is not None:
        self._notify("请先完成或取消当前范围，再使用修复画笔", True)
        return False
    if not self._can_edit():
        return False
    try:
        if not available():
            raise ValueError("本地修复当前不可用，请在图像能力中检查 OpenCV")
        _validate_stroke(points, radius)
        self._sync_layer()
        current = self._layer()
        strokes = (current.get("heal") or {}).get("ops", [])
        mask = current["mask"]
        plain_mask = ("bitmap" not in mask and not mask["ops"]
                      and not mask["feather"] and not mask["inverted"])
        reuse = (current.get("kind") == "adjustment" and not current.get("parent_id")
                 and current["visible"] and current["opacity"] > 0
                 and not any(self.parameters.values()) and not current.get("inpaint")
                 and 0 < len(strokes) < 60 and (plain_mask or _owns_stroke_mask(current)))
        if not reuse and len(self._layers) >= MAX_LAYERS:
            raise ValueError("修复层位置已满，请先移除不需要的图层或组")
        name = ("修复 · " + current["name"][:80] if not strokes
                else "修复 " + str(1+sum(bool(layer.get("heal")) for layer in self._layers)))
        target = deepcopy(current) if reuse else new_layer(name)
        ops = deepcopy(strokes) if reuse else []
        ops.append({"kind": "heal", "points": [[float(x), float(y)] for x, y in points],
                    "radius": float(radius)})
        target["heal"] = {"ops": ops}
        # Keep the range thumbnail consistent with the actual repaired strokes.
        target["mask"] = _stroke_mask(ops)
        clean = validate_layers([target])[0]
    except (ValueError, TypeError) as exc:
        self._notify(str(exc), True)
        return False
    self._commit()
    self._history[self._cursor] = self._snapshot()
    if reuse:
        index = next(i for i, layer in enumerate(self._layers) if layer["id"] == clean["id"])
        self._layers[index] = clean
    else:
        self._layers.append(clean)
    self._selected = clean["id"]
    self._load_layer()
    self._commit()
    self._change()
    self._selection.pickLayer(clean["id"])
    self._notify("已在独立修复层修复这一笔；可调整图层强度或撤销")
    return True


def removeStroke(self, layer_id, index, expected_ops):
    """Remove the explicitly reviewed stroke as one document transaction."""
    if self.busy or not self._can_edit():
        return False
    self._sync_layer()
    layer = self._layer()
    ops = (layer.get('heal') or {}).get('ops', [])
    if (layer['id'] != layer_id or type(index) is not int or not 0 <= index < len(ops)
            or not isinstance(expected_ops, list) or ops != expected_ops):
        return False
    try:
        remove_layer = removes_empty_layer(self, layer)
        if remove_layer:
            candidate = [item for item in self._layers if item['id'] != layer_id]
            validate_hierarchy(candidate)
        else:
            prepared = deepcopy(layer)
            remaining = ops[:index] + ops[index+1:]
            if remaining:
                prepared['heal'] = {'ops': deepcopy(remaining)}
            else:
                prepared.pop('heal', None)
            if not any(layer['recipe'].values()) and not layer.get('inpaint') and _owns_stroke_mask(layer):
                prepared['mask'] = _stroke_mask(remaining)
            candidate = [prepared if item['id'] == layer_id else item for item in self._layers]
            clean = validate_layers(candidate)
            prepared = next(item for item in clean if item['id'] == layer_id)
            candidate = [prepared if item['id'] == layer_id else item for item in self._layers]
    except (ValueError, TypeError) as exc:
        self._notify(str(exc), True)
        return False
    self._commit()
    self._history[self._cursor] = self._snapshot()
    self._layers = candidate
    if remove_layer:
        self._selected = candidate[-1]['id']
    self._load_layer()
    self._commit()
    self._change()
    self._selection.followActiveLayer()
    note = '已删除最后一笔，空修复层已移除' if remove_layer else f'已删除第 {index+1} 笔修复'
    self._notify(note + '；可撤销')
    return True


def drawHeal(self, points, radius):
    if self._candidate is not None:
        return self._notify("请先输出或取消选区，再使用修复画笔", True)
    if not self._can_edit():
        return
    if self.activeIsGroup:
        return self._notify("图层组不直接修复；请在组内调整层上使用修复画笔", True)
    try:
        _validate_stroke(points, radius)
    except (ValueError, TypeError) as exc:
        return self._notify(str(exc), True)
    layer = self._layer()
    heal = deepcopy(layer.get("heal") or {"ops": []})
    if len(heal["ops"]) >= 60:
        return self._notify("修复笔画已达 60 条上限；请新建图层继续", True)
    heal["ops"].append(
        {
            "kind": "heal",
            "points": [[float(x), float(y)] for x, y in points],
            "radius": float(radius),
        }
    )
    layer["heal"] = heal
    self._commit()
    self._change()
    self._notify("已修复笔画范围；删除该图层或撤销可还原")


def applyAIRepairs(self, repairs, expected):
    """Validate every mapped stroke before adding a single undo transaction."""
    if not self._can_edit() or not available():
        raise ValueError("本地修复当前不可用，照片未改变")
    if not isinstance(repairs, list) or not 1 <= len(repairs) <= 3:
        raise ValueError("AI 局部修复需要 1～3 个部位")
    if len(self._layers) + len(repairs) > MAX_LAYERS:
        raise ValueError("修复图层位置不足，照片未改变")
    self._sync_layer()
    content = lambda records: [{k: v for k, v in layer.items() if k != "collapsed"} for layer in records]
    if not isinstance(expected, list) or content(self._layers) != content(expected):
        raise ValueError("图层在等待期间已变化，过期修复未应用")
    prepared, circles = [], []
    for repair in repairs:
        if not isinstance(repair, dict) or set(repair) != {"name", "mask", "heal"}:
            raise ValueError("AI 修复图层字段无效")
        layer = new_layer(repair["name"])
        layer["mask"], layer["heal"] = deepcopy(repair["mask"]), deepcopy(repair["heal"])
        clean = validate_layers([layer])[0]
        ops = clean["heal"]["ops"]
        if not 1 <= len(ops) <= 8 or any(len(op["points"]) != 1 or not MIN_STROKE_RADIUS <= op["radius"] <= .01 for op in ops):
            raise ValueError("AI 修复只能包含小范围单点笔画")
        expected_mask = {"base": "empty", "inverted": False, "feather": 0.,
                         "ops": [{**op, "kind": "brush", "mode": "add"} for op in ops]}
        if {k: v for k, v in clean["mask"].items() if k != "label"} != expected_mask:
            raise ValueError("AI 修复层范围必须与笔画一致")
        for op in ops:
            x, y = op["points"][0]
            px, py = x * self._width, y * self._height
            radius = op["radius"] * min(self._width, self._height)
            if any(hypot(px - cx, py - cy) < radius + cr for cx, cy, cr in circles):
                raise ValueError("多个修复部位的点位重叠，未重复修复照片")
            circles.append((px, py, radius))
        prepared.append(clean)
    self._commit()
    self._history[self._cursor] = self._snapshot()
    self._layers.extend(prepared)
    self._selected = prepared[-1]["id"]
    self._load_layer()
    self._commit()
    self._change()
    self._selection.pickLayer(self._selected)
    return [layer["id"] for layer in prepared]
