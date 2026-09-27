"""Healing brush strokes: non-destructive, per-layer content repair."""

from copy import deepcopy

from ..document import validate_mask


def _validate_stroke(points, radius):
    proxy = {
        "base": "empty",
        "inverted": False,
        "feather": 0.0,
        "label": "修复",
        "ops": [{"kind": "brush", "mode": "add", "points": points, "radius": radius}],
    }
    validate_mask(proxy)


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
