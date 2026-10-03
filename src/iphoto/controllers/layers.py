"""Layers actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
import base64
from io import BytesIO
from ..engine import LABELS, PRESETS, RANGES, Recipe
from ..document import new_layer, raster_mask, raster_mask_cached, validate_layers, MAX_LAYERS
from ..ai_layer_edits import validate_layer_controls
from ..ai_layer_groups import validate_group_plan
from ..layer_tree import (
    display_rows,
    display_states,
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
    self._change(parameter=True)


def finishGesture(self):
    self._commit()
    if self._timer.isActive():
        # Release submits the final snapshot without another cadence wait.
        self._timer.stop()
        self._schedule_render()
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
        self._layer_rows_model.replace(rows)
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
    self._edit_revision += 1
    self._autosave.start()


def _change(self, *, parameter=False):
    self._sync_layer()
    self._mask_url = ""
    self._mark_dirty()
    self._base_change(parameter=parameter)


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


def addGlobalLayer(self, recipe=None, at_root=False):
    if not self._can_edit():
        return
    if len(self._layers) >= MAX_LAYERS:
        return self._notify("图层与组最多 32 项", True)
    self._sync_layer()
    name = f"全图调整 {len(self._layers)}" if at_root else f"调整层 {len(self._layers)}"
    layer = new_layer(name, True)
    if not at_root:
        layer["parent_id"] = self._selected if self.activeIsGroup else self.activeParentId
    if recipe is not None:
        layer["recipe"] = Recipe.from_dict(recipe).to_dict()
    self._layers.append(layer)
    self._selected = layer["id"]
    self._load_layer()
    self._commit()
    self._change()
    self._selection.pickLayer(layer["id"])
    return layer["id"]


def _consume_selection(self, state):
    if self._candidate is not None:
        for message in reversed(self._conversation):
            if message["state"] == "draft":
                message["state"] = state
                self._conversation_model.refresh(message["id"])
                break
    self._candidate = None
    self._selection_target_id = ""
    self._pixel_points, self._pixel_hint = [], None
    self._draft_history, self._draft_cursor = [], 0


def addLocalLayers(self, proposed, *, consume_selection=False, selection_state="discarded"):
    """Apply complete automatic results once, without a temporary UI draft."""
    if (not self.hasImage or self.busy or self.hasRegionDraft
            or self.hasSelectionDraft and not consume_selection):
        raise ValueError("当前不能建立局部调整层，已有图层与范围保留")
    if not isinstance(proposed, list) or not 1 <= len(proposed) <= 4:
        raise ValueError("局部调整需要 1～4 个完整图层")
    if len(self._layers) + len(proposed) > MAX_LAYERS:
        raise ValueError("图层与组最多 32 项，局部调整未应用")
    prepared = validate_layers(proposed)
    existing = {layer["id"] for layer in self._layers}
    for layer in prepared:
        if (layer["kind"] != "adjustment" or layer["parent_id"]
                or layer["id"] in existing or not layer["visible"]
                or layer["opacity"] <= 0 or "heal" in layer or "inpaint" in layer):
            raise ValueError("局部调整必须是独立可见的调整层")
        if not raster_mask_cached(layer["mask"], (512, 512)).getbbox():
            raise ValueError("局部范围为空，已有图层与范围保留")
    # End any preceding parameter gesture before this independent command.
    self._commit()
    self._layers.extend(prepared)
    self._selected = prepared[-1]["id"]
    if consume_selection:
        _consume_selection(self, selection_state)
    self._load_layer()
    self._commit()
    self._change()
    self._selection.showAppliedResult(self._selected)
    self._selection.focusChangedParameters()
    return prepared


def applySelectionRecipe(self, recipe, expected):
    """Adopt precisely the requested range and recipe in one transaction."""
    if not self.hasImage or self.busy or self.hasRegionDraft or not self.hasSelectionDraft:
        raise ValueError("当前范围不可应用，原有图层与范围保留")
    self._sync_layer()
    if (self._candidate != expected["mask"]
            or self._selection_target_id != expected["target_id"]
            or self._layers != expected["layers"]):
        raise ValueError("范围或图层已变化，过期调整未应用")
    validated = Recipe.from_dict(recipe).to_dict()
    mask = deepcopy(self._candidate)
    if not raster_mask_cached(mask, (512, 512)).getbbox():
        raise ValueError("当前范围为空，原有图层与范围保留")
    target = self._selection_target_id
    if not target:
        if not any(validated.values()):
            raise ValueError("AI 没有给出调整效果，当前范围保留")
        layer = new_layer(f"AI 范围调整 {len(self._layers)}", True)
        layer["mask"], layer["recipe"] = mask, validated
        addLocalLayers(self, [layer], consume_selection=True, selection_state="confirmed")
        return layer["id"]
    if target != self._selected or self.activeIsGroup:
        raise ValueError("原图层已变化，未保存范围与调整")
    if not display_states(self._layers)[target]["enabled"]:
        raise ValueError("当前图层效果不可见，范围与参数保留；请显示此层后重试")
    layer = self._layer()
    for key in layer["locked"]:
        validated[key] = layer["recipe"][key]
    prepared = deepcopy(layer)
    previous_recipe = dict(layer['recipe'])
    prepared["mask"], prepared["recipe"] = mask, validated
    validate_layers([prepared if item["id"] == target else item for item in self._layers])
    self._commit()
    layer["mask"], layer["recipe"] = mask, validated
    _consume_selection(self, "confirmed")
    self._load_layer()
    self._commit()
    self._change()
    self._selection.showAppliedResult(target)
    self._selection.focusChangedParameters(previous_recipe)
    return target


def applyLayerEdits(self, edits, expected):
    """Prepare every target before committing one undoable transaction."""
    if not self._can_edit():
        raise ValueError("当前不能修改图层，照片未改变")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 4:
        raise ValueError("已有图层修改需要 1～4 个目标")
    self._sync_layer()
    live = {l["id"]: l for l in self._layers}
    baseline = {l["id"]: l for l in expected}
    prepared, seen = [], set()
    for edit in edits:
        lid = edit["layer_id"]
        layer = live.get(lid)
        if layer is None or layer["kind"] not in ("adjustment", "group") or lid in seen:
            raise ValueError("修改目标已变化，已有图层未修改")
        seen.add(lid)
        old = baseline.get(lid)
        keys = ("id", "name", "kind", "parent_id", "visible", "opacity", "mask", "recipe", "locked")
        if old is None or any(layer[k] != old[k] for k in keys):
            raise ValueError("目标图层在等待期间已变化，过期修改未应用")
        proposed = edit.get("recipe")
        if layer["kind"] == "group" and proposed is not None:
            raise ValueError("图层组不能修改调色参数，已有图层未修改")
        if proposed is None:
            proposed = layer["recipe"]
        if not isinstance(proposed, dict) or set(proposed) != set(RANGES):
            raise ValueError("图层修改参数不完整，已有图层未修改")
        recipe = Recipe.from_dict(proposed).to_dict()
        visible, opacity = validate_layer_controls(edit, layer)
        for key in layer["locked"]:
            recipe[key] = layer["recipe"][key]
        if recipe != layer["recipe"] or visible != layer["visible"] or opacity != layer["opacity"]:
            prepared.append((layer, recipe, visible, opacity))
    if not prepared:
        return []
    self._commit()
    self._history[self._cursor] = self._snapshot()
    for layer, recipe, visible, opacity in prepared:
        layer["recipe"] = recipe
        layer["visible"], layer["opacity"] = visible, opacity
    ids = [layer["id"] for layer, *_ in prepared]
    if self._selected not in ids:
        self._selected = ids[0]
    self._load_layer()
    self._commit()
    self._change()
    return ids


def applyGroupPlan(self, plan, expected):
    """Wrap an adjacent sibling block without changing its composite order."""
    if not self._can_edit():
        raise ValueError("当前不能编组，照片未改变")
    self._sync_layer()
    # Order, sibling effects and ancestor masks also affect the new composite.
    content = lambda records: [{k: v for k, v in layer.items() if k != "collapsed"} for layer in records]
    if not isinstance(expected, list) or content(self._layers) != content(expected):
        raise ValueError("图层在等待期间已变化，过期编组未应用")
    clean = validate_group_plan(plan, self._layers)
    target_ids = set(clean["layer_ids"])
    first = next(layer for layer in self._layers if layer["id"] == clean["layer_ids"][0])
    group = new_layer(clean["name"], True, first["parent_id"], "group")
    group["visible"], group["opacity"] = clean["visible"], clean["opacity"]
    # Records contain large immutable bitmap strings. Copy only records whose
    # parent changes, retaining payloads and all untouched records as they are.
    candidate = [({**layer, "parent_id": group["id"]} if layer["id"] in target_ids else layer)
                 for layer in self._layers]
    candidate.insert(self._layers.index(first), group)
    validate_hierarchy(candidate)
    self._commit()
    self._history[self._cursor] = self._snapshot()
    self._layers = candidate
    self._selected = group["id"]
    self._load_layer()
    self._commit()
    self._change()
    self._selection.pickLayer(group["id"])
    return group["id"]


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
    self._selection.pickLayer(selected)


def deleteLayer(self, target_id=None):
    if not self._can_edit():
        return
    removed = descendants(self._layers, target_id or self._selected)
    remaining = [l for l in self._layers if l["id"] not in removed]
    if not any(l.get("kind", "adjustment") == "adjustment" for l in remaining):
        return self._notify("至少保留一个调整图层；删除组也会删除组内图层")
    self._layers = remaining
    if self._selected in removed:
        self._selected = remaining[-1]["id"]
    self._load_layer()
    self._commit()
    self._change()
    self._selection.followActiveLayer()


def layerContext(self, lid):
    """Read-only availability for one stable target, independent of selection."""
    actions = ("pick", "range", "rename", "duplicate", "toggle", "up", "down", "group", "ungroup", "move", "delete")
    context = {action: False for action in actions}
    context.update({"id": lid, "exists": False, "name": "图层已不存在", "groups": []})
    target = next((layer for layer in self._layers if layer["id"] == lid), None)
    if target is None:
        return context
    context.update({"exists": True, "name": target["name"]})
    if not self._can_edit():
        return context
    excluded = descendants(self._layers, lid)
    # Menu validation needs hierarchy metadata, never large masks or recipes.
    nodes = [{"id": l["id"], "kind": l.get("kind", "adjustment"),
              "parent_id": l.get("parent_id", ""), "collapsed": l.get("collapsed", False)}
             for l in self._layers]

    def valid_parent(parent):
        candidate = [{**n, "parent_id": parent if n["id"] == lid else n["parent_id"]} for n in nodes]
        try:
            validate_hierarchy(candidate)
        except ValueError:
            return False
        return True

    parent = target.get("parent_id", "")
    groups = ([{"id": "", "name": "最外层"}] if parent else []) + [
        {"id": l["id"], "name": l["name"]} for l in self._layers
        if l.get("kind") == "group" and l["id"] not in excluded
        and l["id"] != parent and valid_parent(l["id"])
    ]
    siblings = [l["id"] for l in self._layers if l.get("parent_id", "") == parent]
    position = siblings.index(lid)
    group = new_layer(kind="group", parent_id=parent)
    grouped = [{**n, "parent_id": group["id"] if n["id"] == lid else n["parent_id"]} for n in nodes] + [group]
    can_group = len(self._layers) < MAX_LAYERS
    if can_group:
        try:
            validate_hierarchy(grouped)
        except ValueError:
            can_group = False
    context.update({"pick": True, "range": True, "rename": True, "toggle": True,
                    "duplicate": len(self._layers) + len(excluded) <= MAX_LAYERS,
                    "up": position < len(siblings) - 1, "down": position > 0,
                    "group": can_group, "ungroup": bool(parent), "move": bool(groups),
                    "delete": any(l["id"] not in excluded and l.get("kind", "adjustment") == "adjustment" for l in self._layers),
                    "groups": groups})
    return context


def runLayerAction(self, lid, action, parent_id=""):
    """Validate the clicked target before changing selection or document state."""
    context = layerContext(self, lid)
    allowed = {"duplicate", "toggle", "up", "down", "group", "ungroup", "move", "delete"}
    if action not in allowed or not context.get(action, False):
        self._notify("此图层当前不能执行该操作，图层未改变")
        return False
    if action == "move" and parent_id not in {g["id"] for g in context["groups"]}:
        self._notify("目标组不可用或已变化，图层未移动")
        return False
    self._commit()
    # Keep the pre-command navigation in its existing frame, without a new step.
    self._history[self._cursor] = self._snapshot()
    if action == "toggle":
        toggleLayer(self, lid)
    elif action == "delete":
        deleteLayer(self, lid)
    else:
        # Select inside this transaction; avoid a navigation render before it.
        self._selected = lid
        self._load_layer()
        if action == "duplicate":
            duplicateLayer(self)
        elif action == "group":
            groupLayer(self)
        elif action in {"up", "down"}:
            moveLayer(self, 1 if action == "up" else -1)
        elif action == "ungroup":
            moveOutOfGroup(self)
        else:
            moveToGroup(self, parent_id)
    return True


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


def activeDisplay(self):
    state = display_states(self._layers)[self._selected]
    reason = ""
    if not state["enabled"]:
        blocker = next(l for l in self._layers if l["id"] == state["blockers"][-1])
        target = "当前图层" if blocker["id"] == self._selected else f"所属组“{blocker['name']}”"
        cause = "已隐藏" if not blocker["visible"] else "不透明度为 0%"
        reason = f"{target}{cause}，当前效果未显示。调整参数会保留。"
    return {**state, "reason": reason}


def restoreLayerDisplay(self):
    """Restore only blocking display controls, as one undoable edit."""
    if not self._can_edit():
        return False
    blockers = set(display_states(self._layers)[self._selected]["blockers"])
    if not blockers:
        return False
    # Finish any preceding slider gesture so undo keeps those parameters.
    self._commit()
    for layer in self._layers:
        if layer["id"] in blockers:
            layer["visible"] = True
            if layer["opacity"] == 0:
                layer["opacity"] = 1.0
    self._status = "已恢复当前层及所属组的显示；可一步撤销"
    self._commit()
    self._change()
    return True


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
        self._selection.followActiveLayer()


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
    self._selection.pickLayer(group["id"])


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
    self._selection.followActiveLayer()


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
        self._selection.followActiveLayer()


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
        self._selection.followActiveLayer()


def setParameter(self, key, value):
    if self._can_edit() and not self.activeIsGroup:
        self._base_setParameter(key, value)


def applyPreset(self, name):
    if self._can_edit() and not self.activeIsGroup:
        self._base_applyPreset(name)


def reset(self):
    if self._can_edit():
        self._base_reset()
