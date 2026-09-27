"""Single source of truth for selection UI state.

Owns the tool/mode/brush/tolerance/mask-view state that used to live in
Main.qml, a task state machine across the pixel/matte/refine/AI pipelines,
and semantic facades (chooseTool, refine, apply, selectByText) so every UI
entry point shares one implementation. Document mutations stay in the
existing controllers; this class never touches masks directly.
"""

from PySide6.QtCore import QObject, Property, Signal, Slot

from copy import deepcopy

from ..engine import RANGES
from . import matting, objects, pixel_selections, selections

NAVIGATION_TOOLS = ("inspect", "hand", "zoom")
DRAFT_FREE_TOOLS = ("object", "smart", "heal")
MODES = ("replace", "add", "subtract")
BRUSH_MIN, BRUSH_MAX, BRUSH_STEP = 0.003, 0.15, 0.005


class SelectionController(QObject):
    changed = Signal()
    toolChosen = Signal(str)
    draftBegan = Signal(str)  # "selection" | "region"
    draftEnded = Signal()

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor
        self._tool = "inspect"
        self._mode = "replace"
        self._brush_radius = 0.025
        self._wand_tolerance = 24.0
        self._show_mask = False
        self._draft_active = False
        self._pending_cutout = False
        self._revision = 0
        self._picked = ""
        editor.changed.connect(self._on_editor_changed)
        editor.imageOpened.connect(self._on_image_opened)

    # -- inspector focus: picked layer => adjustments, otherwise range module --

    @Property(str, notify=changed)
    def pickedLayerId(self):
        return self._picked

    @Slot(str)
    def pickLayer(self, lid):
        if self._editor.hasRegionDraft:
            return
        self._editor.selectLayer(lid)
        if self._picked != lid:
            self._picked = lid
            self.changed.emit()

    @Slot()
    def clearPick(self):
        if self._picked != "":
            self._picked = ""
            self.changed.emit()

    # -- state synchronization ------------------------------------------------

    def _on_editor_changed(self):
        self._revision += 1
        self._sync_draft()
        self.changed.emit()

    def _sync_draft(self):
        editor = self._editor
        active = editor.hasSelectionDraft or editor.hasRegionDraft
        if active and not self._draft_active:
            self._draft_active = True
            self._show_mask = editor.maskView != "adjustment"
            self._picked = ""
            self.draftBegan.emit("region" if editor.hasRegionDraft else "selection")
            if self._pending_cutout:
                self._pending_cutout = False
                editor.selectionToLayer()
                self._picked = editor.activeLayerId
        elif not active and self._draft_active:
            self._draft_active = False
            self._pending_cutout = False
            self._show_mask = False
            if self._tool not in NAVIGATION_TOOLS:
                self._tool = "inspect"
            self.draftEnded.emit()

    def _on_image_opened(self):
        self._tool = "inspect"
        self._draft_active = False
        self._sync_draft()
        if not self._draft_active:
            self._show_mask = False
        self.changed.emit()

    # -- tool state -----------------------------------------------------------

    @Property(str, notify=changed)
    def tool(self):
        return self._tool

    @Property(bool, notify=changed)
    def navigationTool(self):
        return self._tool in NAVIGATION_TOOLS

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(float, notify=changed)
    def brushRadius(self):
        return self._brush_radius

    @Property(float, notify=changed)
    def wandTolerance(self):
        return self._wand_tolerance

    @Property(bool, notify=changed)
    def showMask(self):
        return self._show_mask

    @Slot(str)
    def chooseTool(self, tool):
        editor = self._editor
        navigation = tool in NAVIGATION_TOOLS
        if not editor.hasImage or (
            not navigation and (editor.busy or editor.hasRegionDraft)
        ):
            return
        if tool == "brush" and self._tool != "brush":
            self._mode = "add"
        if tool == "smart" and self._tool != "smart":
            editor.startPixelSelection(False)
            pixel_selections.warm(editor)
        if tool in ("smart", "object"):
            # These tools can wait for a background encode before creating a
            # draft. Keep their progress and cancellation controls in view.
            self._picked = ""
        self._pending_cutout = False
        self._tool = tool
        if not navigation:
            if tool not in DRAFT_FREE_TOOLS:
                editor.beginSelection("empty")
            self._show_mask = True
        self.changed.emit()
        self.toolChosen.emit(tool)

    @Slot()
    def reviewMask(self):
        self._editor.beginSelection("current")
        self._tool = "brush"
        self._mode = "add"
        self._show_mask = True
        self.changed.emit()
        self.toolChosen.emit("brush")

    @Slot(str)
    def setMode(self, mode):
        if mode in MODES and mode != self._mode:
            self._mode = mode
            self.changed.emit()

    @Slot()
    def toggleMode(self):
        self.setMode("add" if self._mode == "subtract" else "subtract")

    @Slot(float)
    def setBrushRadius(self, value):
        value = max(BRUSH_MIN, min(BRUSH_MAX, value))
        if value != self._brush_radius:
            self._brush_radius = value
            self.changed.emit()

    @Slot(int)
    def adjustBrush(self, direction):
        self.setBrushRadius(self._brush_radius + BRUSH_STEP * (1 if direction > 0 else -1))

    @Slot(float)
    def setWandTolerance(self, value):
        value = max(0.0, min(100.0, value))
        if value != self._wand_tolerance:
            self._wand_tolerance = value
            self.changed.emit()

    @Slot()
    def toggleShowMask(self):
        self._show_mask = not self._show_mask
        self.changed.emit()

    @Slot(str)
    def setMaskView(self, value):
        if value not in ("overlay", "grayscale", "adjustment"):
            return
        self._editor.setMaskView(value)
        self._show_mask = value != "adjustment"
        self.changed.emit()

    # -- task state machine ---------------------------------------------------

    @Property(str, notify=changed)
    def taskKind(self):
        if getattr(self._editor, "_matte_active", None) or getattr(self._editor, "_matte_pending", None):
            return "matte"
        active = self._editor._pixel_active or self._editor._active
        if active:
            op = active["op"]
            if op == "segment" and active.get("priority") != "low":
                return "pixel"
            if op == "matte":
                return "matte"
            if op == "selection":
                return "refine"
        if any(
            request["op"] == "segment" and request.get("priority") != "low"
            for request in list(self._editor._queue) + list(self._editor._pixel_queue)
        ):
            return "pixel"
        if self._editor.ai.busy:
            return "ai"
        return "none"

    @Property(str, notify=changed)
    def taskText(self):
        return self._editor.status

    @Property(bool, notify=changed)
    def queuedPixelTask(self):
        active = self._editor._pixel_active or self._editor._active or {}
        return not (
            active.get("op") == "segment" and active.get("priority") != "low"
        ) and any(
            request["op"] == "segment" and request.get("priority") != "low"
            for request in list(self._editor._queue) + list(self._editor._pixel_queue)
        )

    @Property(bool, notify=changed)
    def taskCancellable(self):
        return self.taskKind in ("pixel", "matte", "ai")

    @Slot()
    def cancelTask(self):
        kind = self.taskKind
        editor = self._editor
        if kind == "pixel":
            pixel_selections.cancel(editor)
        elif kind == "matte":
            matting.cancel(editor)
        elif kind == "ai":
            editor.ai.cancel()

    # -- target acquisition ---------------------------------------------------

    @Slot(str)
    def selectByText(self, text):
        objects.selectByDescription(self._editor, text)

    @Slot(str)
    def selectByTextDirect(self, text):
        self._editor.sendMessage(text, "selection")

    @Slot()
    def newPixelTarget(self):
        self._editor.startPixelSelection(False)

    @Slot()
    def refinePixelPoints(self):
        self.chooseTool("smart")
        self._editor.startPixelSelection(True)

    @Slot(str, str)
    def rowSelect(self, lid, mode):
        """Element-row acquisition: hover previews, click/＋/－ stack into one range."""
        editor = self._editor
        if editor.busy or editor.hasRegionDraft or not editor.hasImage:
            return
        if mode not in ("replace", "add", "subtract"):
            return
        if mode != "replace" and editor._candidate is None:
            editor.beginSelection("empty")
        pixel_selections.select_objects(editor, [lid], mode)

    @Property(int, notify=changed)
    def revision(self):
        return self._revision

    @Slot(str, result="QVariantMap")
    def layerRecipe(self, lid):
        layer = next((l for l in self._editor._layers if l["id"] == lid), None)
        if layer is None or layer.get("kind") == "group":
            return {}
        return dict(layer["recipe"])

    @Slot(str, str, float)
    def setLayerParameter(self, lid, key, value):
        """Adjustments belong to a layer: edit any layer's recipe without selecting it."""
        editor = self._editor
        if key not in RANGES or editor.busy or editor.hasRegionDraft:
            return
        if lid == editor._selected:
            editor.setParameter(key, value)
            return
        layer = next((l for l in editor._layers if l["id"] == lid), None)
        if layer is None or layer.get("kind") == "group":
            return
        lo, hi = RANGES[key]
        layer["recipe"][key] = round(max(lo, min(hi, value)), 2)
        if key not in layer.setdefault("locked", []):
            layer["locked"].append(key)
        editor._mark_dirty()
        editor._generation += 1
        editor._schedule_render()
        editor.changed.emit()

    @Slot(str)
    def finishLayerGesture(self, lid):
        editor = self._editor
        if lid == editor._selected:
            editor.finishGesture()
            return
        editor._commit()
        editor._change()

    @Slot()
    def cutoutSubject(self):
        editor = self._editor
        if not editor.hasImage or editor.busy or editor.hasRegionDraft:
            return
        if editor.hasSelectionDraft:
            return editor._notify("请先输出或取消当前选区，再一键抠出主体", True)
        if not any(
            c["id"] == "u2net" and c["available"] for c in editor.imageCapabilities
        ):
            return editor._notify("主体模型未配置；请在扩展 → 图像能力中查看", True)
        self._pending_cutout = True
        editor._notify("正在识别主体并创建抠出图层…")
        editor._status = "正在本地计算选区…"
        # Unlike refineSelection, subject detection needs no intermediate
        # draft: requesting directly keeps the chain to a single draftBegan.
        editor._request(
            "selection", plugin="u2net", mask=deepcopy(editor._layer()["mask"])
        )

    # -- edge refinement ------------------------------------------------------

    @Property(str, notify=changed)
    def autoRefineMethod(self):
        editor = self._editor
        caps = {c["id"]: c["available"] for c in editor.imageCapabilities}
        if editor._candidate is not None and caps.get("pixels"):
            return "sam"
        if caps.get("grabcut"):
            return "grabcut"
        if caps.get("matte"):
            return "matte"
        return ""

    @Property("QVariantList", notify=changed)
    def refineMethods(self):
        caps = {c["id"]: c for c in self._editor.imageCapabilities}
        names = {"sam": "重识轮廓", "grabcut": "经典优化", "matte": "透明边缘"}
        auto = self.autoRefineMethod
        rows = [
            {
                "id": "auto",
                "name": "智能细化",
                "available": bool(auto),
                "description": "自动选择当前可用的最佳方法"
                + (f"（当前：{names[auto]}）" if auto else "；请先在图像能力中配置"),
            },
            {
                "id": "matte",
                "name": "细化透明边缘",
                "available": bool(caps.get("matte", {}).get("available")),
                "description": "按原图估计连续透明度，保留发丝等半透明过渡",
            },
            {
                "id": "sam",
                "name": "重识轮廓",
                "available": bool(caps.get("pixels", {}).get("available")),
                "description": "以当前选区为提示，用像素模型重新分割一遍",
            },
            {
                "id": "u2net",
                "name": "选择主体",
                "available": bool(caps.get("u2net", {}).get("available")),
                "description": "本地主体/背景分割；不理解文字指定的任意目标",
            },
            {
                "id": "grabcut",
                "name": "经典边缘优化",
                "available": bool(caps.get("grabcut", {}).get("available")),
                "description": "按照片颜色边界收紧已有选区",
            },
        ]
        for row in rows:
            if not row["available"] and row["id"] in caps:
                row["description"] += " · " + caps[row["id"]].get("status", "")
        return rows

    @Slot(str, int)
    def refine(self, method, radius=8):
        editor = self._editor
        if method == "auto":
            method = self.autoRefineMethod
        if method == "matte":
            editor.refineMatte(radius)
        elif method == "sam":
            editor.pixelRefine()
        elif method in ("grabcut", "u2net"):
            editor.refineSelection(method)
        else:
            editor._notify("没有可用的细化方法，请在扩展 → 图像能力中查看配置", True)

    # -- draft output ---------------------------------------------------------

    @Property(bool, notify=changed)
    def inpaintAvailable(self):
        return any(
            c["id"] == "inpaint" and c["available"]
            for c in self._editor.imageCapabilities
        )

    @Slot(str)
    def apply(self, mode):
        self._pending_cutout = False
        if mode == "new_layer":
            self._editor.selectionToLayer()
            if not self._editor.hasSelectionDraft:
                self._picked = self._editor.activeLayerId
        elif mode == "replace_mask":
            self._editor.acceptSelection()
            self._picked = self._editor.activeLayerId
        elif mode == "inpaint":
            selections.inpaintToLayer(self._editor)
            self._picked = self._editor.activeLayerId
        else:
            self._editor._notify("未知的输出方式", True)
        self.changed.emit()

    @Slot()
    def applyRegions(self):
        self._editor.acceptRegions()
        self._picked = self._editor.activeLayerId
        self.changed.emit()

    @Slot()
    def discard(self):
        self._pending_cutout = False
        editor = self._editor
        if editor.hasRegionDraft:
            editor.discardRegions()
        else:
            editor.discardSelection()
