"""Layers actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
import base64
from io import BytesIO
from ..engine import LABELS, PRESETS, RANGES, Recipe
from ..document import new_layer, raster_mask, MAX_LAYERS
from ..layer_tree import (
    display_rows,
    descendants,
    duplicate_subtree,
    validate_hierarchy,
)


def _base_setParameter(self, key, value):
    if key not in RANGES or not self.hasImage or self.busy:
        return
    lo, hi = RANGES[key]
    value = round(max(lo, min(hi, value)), 2 if key == "exposure" else 0)
    if self._recipe[key] == value:
        return
    self._recipe = {**self._recipe, key: value}
    self._locked.add(key)
    self._status = f"{LABELS[key]} 已手动调整，后续描述保留此参数"
    self._change()


def finishGesture(self):
    self._commit()
    self.changed.emit()


def unlock(self, key):
    if self.busy:
        return
    self._locked.discard(key)
    self._commit()
    self.changed.emit()


def _base_applyPreset(self, name):
    if not self.hasImage or self.busy or name not in PRESETS:
        return
    self._recipe = Recipe.from_dict(PRESETS[name]).to_dict()
    self._locked.clear()
    self._summary = "已应用本地调色预设。所有参数都可以继续微调；原图始终保留。"
    self._commit()
    self._change()


def _base_reset(self):
    if self.hasImage and not self.busy:
        self._recipe, self._locked = Recipe().to_dict(), set()
        self._summary = "已恢复原始参数。"
        self._commit()
        self._change()


def _layer(self):
    return next(l for l in self._layers if l["id"] == self._selected)


def _sync_layer(self):
    if self._layer().get("kind") != "group":
        self._layer()["recipe"] = dict(self._recipe)
        self._layer()["locked"] = sorted(self._locked)


def _load_layer(self):
    self._recipe = dict(self._layer()["recipe"])
    self._locked = set(self._layer()["locked"])


def _snapshot(self):
    return (deepcopy(self._layers), self._selected)


def _publish_layer_rows(self):
    # The sidebar needs summaries, not potentially large brush/recipe payloads.
    rows = display_rows(self._layers)
    if rows != self._layer_rows:
        self._layer_rows = rows
        self.layersChanged.emit()


def layerMaskThumbnail(self, lid):
    layer = next((l for l in self._layers if l["id"] == lid), None)
    if layer is None:
        return ""
    cached = self._mask_thumbnails.get(lid)
    if cached and cached[0] == layer["mask"]:
        return cached[1]
    output = BytesIO()
    raster_mask(layer["mask"], (48, 32)).save(output, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    self._mask_thumbnails[lid] = (deepcopy(layer["mask"]), url)
    return url


def _commit(self):
    self._sync_layer()
    snapshot = self._snapshot()
    # Selecting a layer is navigation, not an edit that should truncate redo.
    content = lambda layers: [
        {k: v for k, v in l.items() if k != "collapsed"} for l in layers
    ]
    if content(snapshot[0]) == content(self._history[self._cursor][0]):
        return
    self._history = (self._history[: self._cursor + 1] + [snapshot])[-60:]
    self._cursor = len(self._history) - 1
    self._mark_dirty()


def _mark_dirty(self):
    self._dirty = True
    self._autosave.start()


def _change(self):
    self._sync_layer()
    self._mask_url = ""
    self._mark_dirty()
    self._base_change()


def selectLayer(self, lid):
    if (
        self.busy
        or self._candidate is not None
        or self._region_candidate is not None
        or lid == self._selected
    ):
        return
    if lid not in [l["id"] for l in self._layers]:
        return
    self._commit()
    self._selected = lid
    self._mask_url = ""
    self._load_layer()
    self._mark_dirty()
    self._generation += 1
    self._schedule_render()
    self.changed.emit()


def _can_edit(self):
    return (
        self.hasImage
        and not self.busy
        and self._candidate is None
        and self._region_candidate is None
    )


def addGlobalLayer(self):
    if not self._can_edit():
        return
    if len(self._layers) >= MAX_LAYERS:
        return self._notify("图层与组最多 32 项", True)
    self._sync_layer()
    layer = new_layer(f"调整层 {len(self._layers)}", True)
    layer["parent_id"] = self._selected if self.activeIsGroup else self.activeParentId
    self._layers.append(layer)
    self._selected = layer["id"]
    self._load_layer()
    self._commit()
    self._change()


def addLayer(self):
    if not self._can_edit():
        return
    if len(self._layers) >= MAX_LAYERS:
        return self._notify("图层与组最多 32 项", True)
    self._sync_layer()
    layer = new_layer(f"局部调整 {len(self._layers)}")
    layer["parent_id"] = self._selected if self.activeIsGroup else self.activeParentId
    self._layers.append(layer)
    self._selected = layer["id"]
    self._load_layer()
    self._commit()
    self._change()
    self._notify("新图层为空选区：先画选区或用 AI 生成")


def duplicateLayer(self):
    if not self._can_edit():
        return
    self._sync_layer()
    copies, selected = duplicate_subtree(self._layers, self._selected)
    if len(self._layers) + len(copies) > MAX_LAYERS:
        return self._notify("图层与组最多 32 项", True)
    index = self._layers.index(self._layer()) + 1
    self._layers[index:index] = copies
    self._selected = selected
    self._load_layer()
    self._commit()
    self._change()


def deleteLayer(self):
    if not self._can_edit():
        return
    removed = descendants(self._layers, self._selected)
    remaining = [l for l in self._layers if l["id"] not in removed]
    if not any(l.get("kind", "adjustment") == "adjustment" for l in remaining):
        return self._notify("至少保留一个调整图层；删除组也会删除组内图层")
    self._layers = remaining
    self._selected = remaining[-1]["id"]
    self._load_layer()
    self._commit()
    self._change()


def renameLayer(self, name):
    if self._can_edit() and name.strip():
        self._layer()["name"] = name.strip()[:80]
        self._commit()
        self.changed.emit()


def toggleLayer(self, lid):
    if not self._can_edit():
        return
    for layer in self._layers:
        if layer["id"] == lid:
            layer["visible"] = not layer["visible"]
    self._commit()
    self._change()


def moveLayer(self, direction):
    if not self._can_edit():
        return
    index = self._layers.index(self._layer())
    siblings = [
        i
        for i, l in enumerate(self._layers)
        if l.get("parent_id", "") == self.activeParentId
    ]
    position = siblings.index(index) + (1 if direction > 0 else -1)
    if 0 <= position < len(siblings):
        dest = siblings[position]
        self._layers[index], self._layers[dest] = (
            self._layers[dest],
            self._layers[index],
        )
        self._commit()
        self._change()


def groupLayer(self):
    if not self._can_edit():
        return
    if len(self._layers) >= MAX_LAYERS:
        return self._notify("图层与组最多 32 项", True)
    self._sync_layer()
    candidate = deepcopy(self._layers)
    target = next(l for l in candidate if l["id"] == self._selected)
    group = new_layer("新建图层组", True, target.get("parent_id", ""), "group")
    target["parent_id"] = group["id"]
    candidate.insert(candidate.index(target) + 1, group)
    try:
        validate_hierarchy(candidate)
    except ValueError as exc:
        return self._notify(str(exc), True)
    self._layers = candidate
    self._selected = group["id"]
    self._load_layer()
    self._commit()
    self._change()


def moveToGroup(self, parent_id):
    if not self._can_edit() or parent_id == self.activeParentId:
        return
    self._sync_layer()
    candidate = deepcopy(self._layers)
    next(l for l in candidate if l["id"] == self._selected)["parent_id"] = parent_id
    try:
        validate_hierarchy(candidate)
    except ValueError as exc:
        return self._notify(str(exc), True)
    for l in candidate:
        if l["id"] == parent_id:
            l["collapsed"] = False
    self._layers = candidate
    self._commit()
    self._change()


def moveOutOfGroup(self):
    if not self.activeParentId:
        return
    parent = next(l for l in self._layers if l["id"] == self.activeParentId)
    self.moveToGroup(parent.get("parent_id", ""))


def toggleGroup(self, lid):
    if self.busy or not self.hasImage:
        return
    group = next((l for l in self._layers if l["id"] == lid and l.get("kind") == "group"), None)
    if group:
        group["collapsed"] = not group.get("collapsed", False)
        self._mark_dirty()
        self.changed.emit()


def setOpacity(self, value):
    if self._can_edit():
        self._layer()["opacity"] = round(max(0, min(100, value)) / 100, 2)
        self._change()


def undo(self):
    if self._candidate is not None and not self.busy:
        if self.canUndo:
            self._draft_cursor -= 1
            self._set_candidate(
                deepcopy(self._draft_history[self._draft_cursor]), False
            )
        return
    if not self._can_edit():
        return
    self._commit()
    if self.canUndo:
        self._cursor -= 1
        self._layers, self._selected = deepcopy(self._history[self._cursor])
        self._load_layer()
        self._change()


def redo(self):
    if self._candidate is not None and not self.busy:
        if self.canRedo:
            self._draft_cursor += 1
            self._set_candidate(
                deepcopy(self._draft_history[self._draft_cursor]), False
            )
        return
    if not self._can_edit():
        return
    if self.canRedo:
        self._cursor += 1
        self._layers, self._selected = deepcopy(self._history[self._cursor])
        self._load_layer()
        self._change()


def setParameter(self, key, value):
    if self._can_edit() and not self.activeIsGroup:
        self._base_setParameter(key, value)


def applyPreset(self, name):
    if self._can_edit() and not self.activeIsGroup:
        self._base_applyPreset(name)


def reset(self):
    if self._can_edit():
        self._base_reset()
