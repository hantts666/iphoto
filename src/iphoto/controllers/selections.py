"""Selections actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
from ..document import new_layer, empty_mask, validate_mask, raster_mask, MAX_LAYERS
from ..plugins import capabilities, assess


def _quality_text(mask):
    quality = assess(mask)
    small_part = quality['coverage'] < .1 and mask.get("semantic_target") in ("face", "face_skin", "body_skin")
    return ("局部分区" if small_part else f"覆盖约 {quality['coverage']:g}%") + (
        " · " + "；".join(quality["warnings"])
        if quality["warnings"]
        else " · 请检查边缘与漏选"
    )


def _set_candidate(self, mask, record=True):
    self._pixel_points, self._pixel_hint = [], None
    self._candidate = validate_mask(mask)
    if record:
        self._record_draft()
    self._selection_quality = self._quality_text(mask)
    self._mask_url = ""
    self._generation += 1
    self._mark_dirty()
    self._schedule_render()
    self.changed.emit()


def _record_draft(self):
    if (
        self._draft_history
        and self._draft_history[self._draft_cursor] == self._candidate
    ):
        return
    self._draft_history = (
        self._draft_history[: self._draft_cursor + 1] + [deepcopy(self._candidate)]
    )[-24:]
    self._draft_cursor = len(self._draft_history) - 1


def beginSelection(self, source="empty"):
    if not self.hasImage or self.busy or self.hasRegionDraft or self.hasSelectionDraft:
        return
    self._commit()
    self._draft_history, self._draft_cursor = [], 0
    # A correction belongs to its original layer, even across project reloads.
    self._selection_target_id = self._selected if source == "current" else ""
    self._set_candidate(
        deepcopy(self._layer()["mask"]) if source == "current" else empty_mask()
    )
    self._notify(
        f"正在修正“{self.activeLayerName}”的范围；完成后点击“保存范围修改”"
        if self._selection_target_id
        else "选好范围后点击“开始调整此范围”，即可调整照片",
        scope="draft",
    )


def drawDraft(self, kind, mode, points, radius):
    if self.busy or self.hasRegionDraft or not self.hasImage:
        return
    if self._candidate is None:
        self.beginSelection("empty")
    try:
        if mode not in ("replace", "add", "subtract"):
            raise ValueError("选区模式无效")
        mask = empty_mask() if mode == "replace" else deepcopy(self._candidate)
        effective_mode = "add" if mode == "replace" else mode
        if mask["inverted"]:
            effective_mode = "subtract" if effective_mode == "add" else "add"
        op = {"kind": kind, "mode": effective_mode, "points": points}
        if kind == "brush":
            op["radius"] = radius
        mask["ops"].append(op)
        mask["label"] = "手动选区"
        self._set_candidate(mask)
    except (ValueError, TypeError) as exc:
        self._notify(str(exc), True)


def draftAction(self, action):
    if self.busy or self.hasRegionDraft or not self.hasImage:
        return
    if self._candidate is None:
        self.beginSelection("empty")
    mask = deepcopy(self._candidate)
    if action in ("all", "clear"):
        mask = empty_mask(action == "all")
    elif action == "invert":
        mask["inverted"] = not mask["inverted"]
        mask["label"] = "反选选区"
    else:
        return
    self._set_candidate(mask)


def setDraftFeather(self, value):
    if self._candidate is None or self.busy:
        return
    mask = deepcopy(self._candidate)
    mask["feather"] = round(max(0, min(5, value)) / 100, 4)
    self._set_candidate(mask, False)


def finishSelectionGesture(self):
    if self._candidate is not None:
        self._record_draft()
        self.changed.emit()


def setMaskView(self, value):
    if value not in ("overlay", "grayscale", "adjustment"):
        return
    self._mask_view = value
    self._mask_url = ""
    self._generation += 1
    self._schedule_render()
    self.changed.emit()


def refineSelection(self, plugin="grabcut"):
    if self.busy or not self.hasImage:
        return
    capability = next((c for c in capabilities() if c["id"] == plugin), None)
    if not capability or not capability["available"]:
        return self._notify(
            (capability["status"] if capability else "未知选区能力")
            + "；请打开图像能力查看配置",
            True,
        )
    if self._region_candidate:
        mask = self._region_candidate["layers"][self._region_index]["mask"]
    else:
        if self._candidate is None:
            self.beginSelection("current")
        mask = self._candidate
    self._status = "正在本地计算选区…"
    self._request("selection", plugin=plugin, mask=deepcopy(mask))


def wandSelection(self, point, tolerance, mode):
    if self.busy or self.hasRegionDraft or not self.hasImage:
        return
    if self._candidate is None:
        self.beginSelection("empty")
    self._request(
        "selection",
        plugin="wand",
        mask=deepcopy(self._candidate),
        options={"point": point, "tolerance": tolerance, "mode": mode},
    )


def selectionToLayer(self):
    if self._candidate is None or self.busy:
        return
    if len(self._layers) >= MAX_LAYERS:
        return self._notify("图层与组最多 32 项", True)
    if not raster_mask(self._candidate, (256, 256)).getbbox():
        return self._notify("选区为空，请先选中需要调整的区域", True)
    self._sync_layer()
    layer = new_layer(f"选区调整 {len(self._layers)}")
    layer["parent_id"] = self._selected if self.activeIsGroup else self.activeParentId
    layer["mask"] = deepcopy(self._candidate)
    self._layers.append(layer)
    self._selected = layer["id"]
    self._candidate = None
    self._selection_target_id = ""
    self._pixel_points, self._pixel_hint = [], None
    self._draft_history = []
    self._load_layer()
    for message in reversed(self._conversation):
        if message["state"] == "draft":
            message["state"] = "confirmed"
            break
    self._message("event", "已从选区建立独立调整层。调整参数只影响此层蒙版内的内容。")
    self._commit()
    self._change()
    self._notify("已开始局部调整；右侧可调参数，点图层蒙版缩略图可修边")


def inpaintToLayer(self):
    if self._candidate is None or self.busy:
        return
    capability = next((c for c in capabilities() if c["id"] == "inpaint"), None)
    if not capability or not capability["available"]:
        return self._notify("内容感知填充需要 OpenCV；请在扩展 → 图像能力中查看", True)
    if len(self._layers) >= MAX_LAYERS:
        return self._notify("图层与组最多 32 项", True)
    if not raster_mask(self._candidate, (256, 256)).getbbox():
        return self._notify("选区为空，请先选中要移除的内容", True)
    self._sync_layer()
    layer = new_layer(f"内容感知填充 {len(self._layers)}")
    layer["parent_id"] = self._selected if self.activeIsGroup else self.activeParentId
    layer["mask"] = deepcopy(self._candidate)
    layer["inpaint"] = {"method": "telea", "radius": 5}
    self._layers.append(layer)
    self._selected = layer["id"]
    self._candidate = None
    self._selection_target_id = ""
    self._pixel_points, self._pixel_hint = [], None
    self._draft_history = []
    self._load_layer()
    for message in reversed(self._conversation):
        if message["state"] == "draft":
            message["state"] = "confirmed"
            break
    self._message(
        "event",
        "已从选区创建内容感知填充层：用周围内容合成填充，原图未改变；删除该层即可还原。",
    )
    self._commit()
    self._change()
    self._notify("已创建内容感知填充层；可继续调整，Ctrl+Z 可撤销")


def selectRegion(self, index):
    if (
        self.busy
        or not self._region_candidate
        or not 0 <= index < len(self._region_candidate["layers"])
    ):
        return
    self._region_index = index
    self._mask_url = ""
    self._generation += 1
    self._schedule_render()
    self.changed.emit()


def toggleRegion(self, index):
    if (
        self.busy
        or not self._region_candidate
        or not 0 <= index < len(self._region_candidate["layers"])
    ):
        return
    layer = self._region_candidate["layers"][index]
    layer["visible"] = not layer["visible"]
    self._mark_dirty()
    self._generation += 1
    self._schedule_render()
    self.changed.emit()


def acceptRegions(self):
    if self.busy or not self._region_candidate:
        return
    layers = [deepcopy(l) for l in self._region_candidate["layers"] if l["visible"]]
    if not layers:
        return self._notify("请至少保留一个分区", True)
    if len(self._layers) + len(layers) > MAX_LAYERS:
        return self._notify("图层数量超限，请先取消部分区域", True)
    self._sync_layer()
    self._layers.extend(layers)
    self._selected = layers[-1]["id"]
    self._region_candidate = None
    self._load_layer()
    for message in reversed(self._conversation):
        if message["state"] == "region_draft":
            message["state"] = "applied"
            break
    self._message(
        "event", f"已创建 {len(layers)} 个分区调整层，整组可一步撤销。", state="applied"
    )
    self._commit()
    self._change()
    self._notify(f"已开始 {len(layers)} 个分区调整；右侧可调参数，Ctrl+Z 可撤销")


def discardRegions(self):
    if self.busy or not self._region_candidate:
        return
    self._region_candidate = None
    for message in reversed(self._conversation):
        if message["state"] == "region_draft":
            message["state"] = "discarded"
            break
    self._message("event", "已放弃分区方案，原有图层未改变。")
    self._generation += 1
    self._schedule_render()
    self.changed.emit()
    self._notify("已取消分区方案，原有图层保留")


def acceptSelection(self):
    if self._candidate is None or self.busy:
        return
    if self._selection_target_id and self._selection_target_id != self._selected:
        return self._notify("原图层已变化，未保存范围修改", True)
    self._layer()["mask"], self._candidate = self._candidate, None
    self._selection_target_id = ""
    self._pixel_points, self._pixel_hint = [], None
    self._draft_history = []
    for message in reversed(self._conversation):
        if message["state"] == "draft":
            message["state"] = "confirmed"
            break
    self._message(
        "event",
        "已将选区应用为当前图层蒙版。后续修改仅在此范围内生效。",
        state="applied",
    )
    self._commit()
    self._change()
    self._notify("已保存此层的范围，可继续调整照片；Ctrl+Z 可撤销")


def discardSelection(self):
    if self._candidate is None or self.busy:
        return
    self._candidate = None
    self._selection_target_id = ""
    self._pixel_points, self._pixel_hint = [], None
    self._draft_history = []
    for message in reversed(self._conversation):
        if message["state"] == "draft":
            message["state"] = "discarded"
            break
    self._message("event", "已取消选区草稿，原图层蒙版保持不变。")
    self._generation += 1
    self._schedule_render()
    self.changed.emit()
    self._notify("已放弃草稿，保留原选区")


def setEdgeProtection(self, value):
    if self._candidate is None or self.busy:
        return
    mask = deepcopy(self._candidate)
    mask["edge_protection"] = round(max(0, min(100, value)) / 100, 2)
    self._set_candidate(mask, False)


def setEdgeShift(self, value):
    if self._candidate is None or self.busy:
        return
    mask = deepcopy(self._candidate)
    mask["edge_shift"] = int(max(-5, min(5, round(value))))
    self._set_candidate(mask, False)
