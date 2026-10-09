"""Single source of truth for selection UI state.

Owns the tool/mode/brush/tolerance/mask-view state that used to live in
Main.qml, a task state machine across the pixel/matte/refine/AI pipelines,
and semantic facades (chooseTool, refine, apply, selectByText) so every UI
entry point shares one implementation. Document mutations stay in the
existing controllers; this class never touches masks directly.
"""

from PySide6.QtCore import QObject, Property, Signal, Slot

from copy import deepcopy
import math
import re
from uuid import uuid4

from ..engine import LABELS, RANGES, Recipe, RECIPE_FIELDS, CURVE_FIELDS
from ..document import MIN_STROKE_RADIUS
from . import adjustment_review, heal, matting, objects, pixel_selections, selections

NAVIGATION_TOOLS = ("inspect", "hand", "zoom")
DRAFT_FREE_TOOLS = ("object", "smart", "heal", "transparency")
MODES = ("replace", "add", "subtract")
BRUSH_MAX = 0.15
HEAL_MAX = 0.025


class SelectionController(QObject):
    changed = Signal()
    layerFocusRequested = Signal(str)
    parameterFocusRequested = Signal(str, str)
    repairFocusRequested = Signal(str)
    toolChosen = Signal(str)
    resultApplied = Signal()
    draftBegan = Signal(str)  # "selection" | "region"
    draftEnded = Signal()

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor
        self._tool = "inspect"
        self._mode = "replace"
        self._brush_radius = 0.025
        self._heal_radius = 0.003
        self._transparency_profile = 'auto'
        self._wand_tolerance = 24.0
        self._show_mask = False
        self._draft_active = False
        self._draft_target_id = ""
        self._pending_cutout = False
        self._revision = 0
        self._picked = ""
        self._repair_review_key = None
        self._repair_review_index = -1
        self._reviewed_repair = None
        self._repair_target_cache_key = None
        self._repair_target_cache = None
        self._adjustment_review_key = None
        self._adjustment_review_index = -1
        editor.changed.connect(self._on_editor_changed)
        editor.ai.progressChanged.connect(self.changed.emit)
        editor.imageOpened.connect(self._on_image_opened)

    # -- inspector focus: picked layer => adjustments, otherwise range module --

    @Property(str, notify=changed)
    def pickedLayerId(self):
        return self._picked

    @Slot(str)
    def pickLayer(self, lid):
        if self._editor.hasRegionDraft or self._editor.hasSelectionDraft:
            return
        self._editor.selectLayer(lid)
        if self._editor.activeLayerId != lid:
            return
        if self._reveal_layer(lid):
            self._editor.changed.emit()
        if self._picked != lid:
            self._picked = lid
            self.changed.emit()
            if self._editor.activeRepairInfo['count']:
                self.repairFocusRequested.emit(lid)
        self.layerFocusRequested.emit(lid)

    def followActiveLayer(self):
        if self._picked:
            self.pickLayer(self._editor.activeLayerId)

    def _reveal_layer(self, lid):
        # Revealing the active row is navigation, without render or undo changes.
        editor = self._editor
        if editor.busy:
            return False
        by_id = {layer["id"]: layer for layer in editor._layers}
        parent = by_id.get(lid, {}).get("parent_id", "")
        expanded = False
        while parent:
            group = by_id[parent]
            if group.get("collapsed", False):
                group["collapsed"] = False
                expanded = True
            parent = group.get("parent_id", "")
        if expanded:
            editor._mark_dirty()
        return expanded

    @Slot()
    def clearPick(self):
        if self._picked != "":
            self._picked = ""
            self.changed.emit()

    def showAppliedResult(self, lid):
        """Reveal an applied result directly; no synthetic draft transitions."""
        self._pending_cutout = False
        self._show_mask = False
        if self._tool not in NAVIGATION_TOOLS:
            self._tool = "inspect"
        self.pickLayer(lid)
        self.resultApplied.emit()

    def focusChangedParameters(self, previous=None):
        """Expose an applied control once, without following later renders."""
        editor = self._editor
        if (editor.hasSelectionDraft or editor.hasRegionDraft or editor.activeIsGroup
                or not editor.activeDisplay['enabled']):
            return
        current = editor.parameters
        previous = previous or {}
        changed = [key for key in RECIPE_FIELDS if current[key] != previous.get(key, [] if key in CURVE_FIELDS else 0)]
        if not changed:
            return
        # Detail and colour controls live in closed sections. Prefer a changed
        # one there; unchanged existing effects must not redirect the result.
        priority = ('skin_smoothing', 'sharpness', 'softness', 'warmth', 'tint',
                    'saturation', 'vibrance')
        key = next((key for key in priority if key in changed), changed[0])
        self.pickLayer(editor.activeLayerId)
        self.parameterFocusRequested.emit(editor.activeLayerId, key)

    @Slot('QVariantList', result='QVariantList')
    def curveSamples(self, points):
        import numpy as np
        from ..tone_curves import validate, evaluate

        try:
            return (evaluate(np.arange(256, dtype=np.float32)/255, validate(points))*255).tolist()
        except ValueError:
            return []

    @Slot(str, str, int, str, 'QVariantList', result=bool)
    def applyCurve(self, layer_id, photo, generation, key, points):
        editor = self._editor
        if (not editor._can_edit() or editor.activeIsGroup or key not in CURVE_FIELDS
                or layer_id != editor.activeLayerId or photo != editor.originalUrl
                or type(generation) is not int or generation != editor.documentGeneration):
            return False
        try:
            recipe = Recipe.from_dict({**editor.parameters, key: points}).to_dict()
        except ValueError:
            return False
        if recipe != editor.parameters:
            editor._recipe = recipe
            editor._locked.add(key)
            editor._status = LABELS[key] + ' 已手动调整，后续描述保留此曲线'
            editor._change(parameter=True)
        return True

    @Slot(str, str, int, str, 'QVariantList', bool, result=bool)
    def cancelCurve(self, layer_id, photo, generation, key, points, was_locked):
        editor = self._editor
        if not self.applyCurve(layer_id, photo, generation, key, points):
            return False
        if not was_locked:
            editor._locked.discard(key)
            editor._sync_layer()
        editor.finishGesture()
        return True

    @Slot(str, str, int, str, str, result="QVariantMap")
    def applyParameterText(self, layer_id, photo, generation, key, text):
        editor = self._editor
        if (not editor._can_edit() or editor.activeIsGroup or layer_id != editor.activeLayerId
                or photo != editor.originalUrl or type(generation) is not int
                or generation != editor.documentGeneration or not isinstance(key, str) or key not in RANGES):
            return {"ok": False, "message": "此项输入已过期，请重新输入"}
        lo, hi = RANGES[key]
        hint = f"{LABELS[key]}：请输入 {lo:g}～{hi:g} 之间的" + ("数值" if key == "exposure" else "整数")
        try:
            if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 32
                    or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text.strip())):
                raise ValueError()
            value = float(text.strip())
            if (not math.isfinite(value) or not lo <= value <= hi
                    or key != "exposure" and not value.is_integer()):
                raise ValueError()
        except ValueError:
            return {"ok": False, "message": hint}
        if editor.parameters[key] != value:
            editor.setParameter(key, value)
            editor.finishGesture()
        return {"ok": True, "message": ""}

    @Slot(str, str, int, "QVariantList", float, result=bool)
    def paintRepair(self, layer_id, photo, generation, points, radius):
        editor = self._editor
        if (layer_id != editor.activeLayerId or photo != editor.originalUrl
                or type(generation) is not int or generation != editor.documentGeneration):
            return False
        return heal.paintRepair(editor, points, radius)

    @Slot("QVariantList", result=bool)
    def reviewAdjustments(self, layer_ids):
        editor = self._editor
        if not editor.hasImage or editor.busy or editor.hasSelectionDraft or editor.hasRegionDraft:
            return False
        targets = adjustment_review.targets(editor, layer_ids)
        if not targets:
            return False
        key = (editor._sha, [(layer['id'], point, versions) for layer, point, versions in targets])
        index = (self._adjustment_review_index + 1) % len(targets) if key == self._adjustment_review_key else 0
        layer, point, _ = targets[index]
        reveal_controls = key != self._adjustment_review_key or editor.activeLayerId != layer['id']
        self._adjustment_review_key, self._adjustment_review_index = key, index
        self.pickLayer(layer['id'])
        if reveal_controls:
            self.focusChangedParameters()
        self._show_mask = False
        self.chooseTool('inspect')
        editor.viewport.setZoom(1.)
        editor.viewport.centerOn(*point)
        note = '；此层效果未显示' if not editor.activeDisplay['enabled'] else ''
        next_note = '再次点击查看下一处，' if len(targets) > 1 else ''
        editor._notify(f"正在查看局部效果 {index+1}/{len(targets)} · {layer['name']} · 100%{note}；{next_note}可按住看原图比较")
        return True

    @Slot("QVariantList", result=bool)
    def reviewRepairs(self, layer_ids):
        editor = self._editor
        if (not editor.hasImage or editor.busy or editor.hasSelectionDraft or editor.hasRegionDraft):
            return False
        targets = heal.review_targets(editor, layer_ids)
        if not targets:
            return False
        key = (editor._sha, [(layer['id'], op) for layer, op in targets])
        index = (self._repair_review_index + 1) % len(targets) if key == self._repair_review_key else 0
        layer, op = targets[index]
        # Navigation follows live strokes. A changed/deleted result restarts the
        # cycle instead of reusing stale coordinates from the conversation.
        self._repair_review_key = deepcopy(key)
        self._repair_review_index = index
        self.pickLayer(layer['id'])
        local_index = sum(previous['id'] == layer['id'] for previous, _ in targets[:index])
        self._reviewed_repair = {
            'sha': editor._sha, 'layer_id': layer['id'], 'index': local_index, 'token': uuid4().hex,
            'ops': [stroke for lid, stroke in self._repair_review_key[1] if lid == layer['id']],
        }
        self._show_mask = False
        self.chooseTool('inspect')
        radius_x = op['radius'] * min(editor._width, editor._height) / editor._width
        radius_y = op['radius'] * min(editor._width, editor._height) / editor._height
        xs, ys = zip(*op['points'])
        editor.viewport.focusRegion(min(xs)-radius_x, min(ys)-radius_y,
                                    max(xs)+radius_x, max(ys)+radius_y)
        note = '；此层效果未显示' if not editor.activeDisplay['enabled'] else ''
        editor._notify(f"正在查看修复 {index+1}/{len(targets)} · {layer['name']}{note}；可按住看原图比较")
        self.changed.emit()
        return True

    def _reviewed_repair_target(self):
        editor, reference = self._editor, self._reviewed_repair
        if (not reference or reference['sha'] != editor._sha
                or reference['layer_id'] != editor.activeLayerId or self._picked != editor.activeLayerId):
            return None
        # Rendering/status notifications share the same document generation.
        # Check the live stroke list once per content change, without rasterizing.
        key = (reference['token'], editor.documentGeneration)
        if key != self._repair_target_cache_key:
            layer = editor._layer()
            matches = (layer.get('heal') or {}).get('ops', []) == reference['ops']
            self._repair_target_cache = (layer, reference['index'], reference['ops']) if matches else None
            self._repair_target_cache_key = key
        return self._repair_target_cache

    @Property('QVariantMap', notify=changed)
    def reviewedRepair(self):
        target = self._reviewed_repair_target()
        if not target:
            return {}
        layer, index, ops = target
        return {'layer_id': layer['id'], 'index': index+1, 'count': len(ops),
                'token': self._reviewed_repair['token'], 'removes_layer': heal.removes_empty_layer(self._editor, layer)}

    @Slot(str, result=bool)
    def deleteReviewedRepair(self, token):
        editor = self._editor
        if (editor.busy or not editor._can_edit() or not self._reviewed_repair
                or token != self._reviewed_repair['token']):
            return False
        target = self._reviewed_repair_target()
        if not target or not heal.removeStroke(editor, target[0]['id'], target[1], target[2]):
            return False
        self._reviewed_repair = None
        self._repair_review_key = None
        self._repair_review_index = -1
        self._repair_target_cache_key = None
        self._repair_target_cache = None
        self.changed.emit()
        return True

    # -- state synchronization ------------------------------------------------

    def _on_editor_changed(self):
        self._revision += 1
        self._sync_draft()
        editor = self._editor
        repair_target = ""
        if self._picked and self._picked != editor.activeLayerId:
            # An open inspector follows document selection, including undo and
            # deleted targets. An explicitly cleared pick stays in range mode.
            self._picked = editor.activeLayerId
            if self._reveal_layer(self._picked):
                editor._publish_layer_rows()
            if editor.activeRepairInfo['count']:
                repair_target = self._picked
        self.changed.emit()
        if repair_target:
            self.repairFocusRequested.emit(repair_target)

    def _sync_draft(self):
        editor = self._editor
        active = editor.hasSelectionDraft or editor.hasRegionDraft
        if active and not self._draft_active:
            self._draft_active = True
            self._draft_target_id = editor._selection_target_id
            self._show_mask = editor.maskView != "adjustment"
            self._picked = ""
            self.draftBegan.emit("region" if editor.hasRegionDraft else "selection")
            if self._pending_cutout:
                self._pending_cutout = False
                editor.selectionToLayer()
                self._picked = editor.activeLayerId
        elif not active and self._draft_active:
            self._draft_active = False
            if self._draft_target_id == editor.activeLayerId:
                self._picked = self._draft_target_id
            self._draft_target_id = ""
            self._pending_cutout = False
            self._show_mask = False
            if self._tool not in NAVIGATION_TOOLS:
                self._tool = "inspect"
            self.draftEnded.emit()

    def _reset_document_focus(self):
        self._draft_active = False
        self._draft_target_id = ""
        self._pending_cutout = False
        self._picked = ""
        self._repair_review_key = None
        self._repair_review_index = -1
        self._reviewed_repair = None
        self._repair_target_cache_key = None
        self._repair_target_cache = None
        self._adjustment_review_key = None
        self._adjustment_review_index = -1

    def _on_image_opened(self):
        self._tool = "inspect"
        self._reset_document_focus()
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
        lo, hi = self._brush_bounds()
        value = self._heal_radius if self._tool == "heal" else self._brush_radius
        return max(lo, min(hi, value))

    def _brush_bounds(self):
        short_side = max(1, min(self._editor._width, self._editor._height))
        hi = HEAL_MAX if self._tool == "heal" else BRUSH_MAX
        # The existing raster brush needs a one-pixel radius. Its minimum
        # diameter must not grow with the source photograph's dimensions.
        return min(hi, max(MIN_STROKE_RADIUS, 1 / short_side)), hi

    @Property(int, notify=changed)
    def brushDiameter(self):
        return max(1, round(2 * self.brushRadius * max(1, min(self._editor._width, self._editor._height))))

    @Property(int, notify=changed)
    def minBrushDiameter(self):
        lo, _ = self._brush_bounds()
        return max(1, round(2 * lo * max(1, min(self._editor._width, self._editor._height))))

    @Property(int, notify=changed)
    def maxBrushDiameter(self):
        _, hi = self._brush_bounds()
        return max(self.minBrushDiameter, round(2 * hi * max(1, min(self._editor._width, self._editor._height))))

    @Property(int, notify=changed)
    def brushDiameterStep(self):
        return max(1 if self._tool == "heal" else 2, round(self.brushDiameter * .1))

    @Property(float, notify=changed)
    def wandTolerance(self):
        return self._wand_tolerance

    @Property(bool, notify=changed)
    def showMask(self):
        return self._show_mask

    @Slot(str)
    def chooseTool(self, tool):
        editor = self._editor
        switching = tool != self._tool
        navigation = tool in NAVIGATION_TOOLS
        if not editor.hasImage or (
            not navigation and (editor.busy or editor.hasRegionDraft)
        ):
            return
        if tool == "transparency" and (not editor.hasSelectionDraft or not self.transparencyAvailable):
            return editor._notify("请先选择范围，并在图像能力中配置 AI 细节模型", True)
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
            if tool in ("smart", "object") and not editor.hasSelectionDraft:
                if switching:
                    self._show_mask = False
            else:
                self._show_mask = tool != "heal"
        self.changed.emit()
        self.toolChosen.emit(tool)

    @Property(bool, notify=changed)
    def transparencyAvailable(self):
        return any(c["id"] == "details" and c["available"] for c in self._editor.imageCapabilities)

    @Property(bool, notify=changed)
    def hairMattingAvailable(self):
        return any(c['id']=='hair_details' and c['available'] for c in self._editor.imageCapabilities)

    @Property(str, notify=changed)
    def transparencyProfile(self):
        return self._transparency_profile

    @Slot(str)
    def setTransparencyProfile(self, value):
        if self._editor.busy or value not in ('auto','hair','general'):
            return
        if value=='hair' and not self.hairMattingAvailable:
            self._editor._notify('人物发丝模型未配置：scripts/setup_hair_matting.py',True)
            return
        self._transparency_profile = value
        self.changed.emit()

    @Slot()
    def chooseTransparency(self):
        editor = self._editor
        if editor.busy or not editor.hasSelectionDraft or not self.transparencyAvailable:
            return
        self.chooseTool("transparency")
        if editor.maskView != "overlay":
            self.setMaskView("overlay")
        editor._status = "透明细化：在发丝或纱边涂抹，松开后 AI 处理；附近保留部分目标与背景作为参考"
        editor.changed.emit()

    @Slot(str, str, int, "QVariantList", float, result=bool)
    def paintTransparency(self, layer, photo, generation, points, radius):
        editor = self._editor
        if (layer != editor.activeLayerId or photo != editor.originalUrl or generation != editor._generation
                or self._tool != "transparency"):
            return False
        profile = self._transparency_profile
        if profile=='auto':
            label = (editor._candidate or {}).get('label','').lower()
            profile = 'hair' if self.hairMattingAvailable and not (editor._candidate or {}).get('semantic_target') and any(word in label for word in ('头发','发丝','hair')) else 'general'
        return matting.paint(editor, points, radius, profile=profile)

    @Slot()
    def reviewMask(self):
        editor = self._editor
        if not editor.hasImage or editor.busy or editor.hasRegionDraft:
            return
        if editor.hasSelectionDraft and not self.editingLayerMask:
            return
        editor.beginSelection("current")
        self._tool = "brush"
        self._mode = "add"
        self._show_mask = True
        self.changed.emit()
        self.toolChosen.emit("brush")

    @Slot(str, result=bool)
    def correctMask(self, mode):
        """Start a bound correction with the requested brush operation."""
        editor = self._editor
        if (mode not in ("add", "subtract") or not editor.hasImage or editor.busy
                or editor.hasRegionDraft or editor.hasSelectionDraft):
            return False
        self.reviewMask()
        if not self.editingLayerMask:
            return False
        self.setMaskView("overlay")
        self.setMode(mode)
        return True

    @Slot(str, result=bool)
    def correctDraft(self, mode):
        """Correct the visible draft without loading or rebinding a layer mask."""
        editor = self._editor
        if (mode not in ("add", "subtract") or not editor.hasImage or editor.busy
                or editor.hasRegionDraft or not editor.hasSelectionDraft):
            return False
        self.chooseTool("brush")
        self.setMode(mode)
        if editor.maskView != "overlay":
            self.setMaskView("overlay")
        editor._status = "补选模式：在漏选处拖动画笔，可逐笔撤销" if mode == "add" else "擦除模式：在误选处拖动画笔，可逐笔撤销"
        editor.changed.emit()
        return True

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
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        healing = self._tool == "heal"
        lo, hi = self._brush_bounds()
        value = max(lo, min(hi, value))
        if value != self.brushRadius:
            if healing:
                self._heal_radius = value
            else:
                self._brush_radius = value
            self.changed.emit()

    @Slot(int)
    def setBrushDiameter(self, value):
        short_side = max(1, min(self._editor._width, self._editor._height))
        self.setBrushRadius(value / (2 * short_side))

    @Slot(int)
    def adjustBrush(self, direction):
        diameter = self.brushDiameter + self.brushDiameterStep * (1 if direction > 0 else -1)
        lo, hi = self._brush_bounds()
        if diameter <= self.minBrushDiameter:
            self.setBrushRadius(lo)
        elif diameter >= self.maxBrushDiameter:
            self.setBrushRadius(hi)
        else:
            self.setBrushDiameter(diameter)

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
        if value not in ("overlay", "grayscale", "adjustment", "white", "black"):
            return
        self._editor.setMaskView(value)
        self._show_mask = value != "adjustment"
        self.changed.emit()

    # -- task state machine ---------------------------------------------------

    @Property(str, notify=changed)
    def taskKind(self):
        content_jobs = ([self._editor._active] if self._editor._active else []) + list(self._editor._queue)
        if any(job['op'] == 'foreground_import' and not job.get('cancelled') for job in content_jobs):
            return 'content'
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
        if (self._editor.ai.busy or self._editor.aiRepairPreparing or self._editor.aiObjectPreparing
                or getattr(self._editor, 'aiMaskPreparing', False) or getattr(self._editor, 'aiPhotoPreparing', False)
                or getattr(self._editor, 'aiChannelPreparing', False)):
            return "ai"
        if self._editor.photoPreparing:
            return "warm"
        return "none"

    @Property(str, notify=changed)
    def taskText(self):
        if self._editor._image_edit.busy:
            return self._editor._image_edit.progress
        if self._editor.ai.busy:
            return self._editor.ai.requestProgress
        if self.taskKind == "warm":
            return "正在准备照片以便点选，可继续浏览；点击目标后会排队处理…"
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
        return self.taskKind in ("pixel", "matte", "ai", "warm", "content")

    @Slot()
    def cancelTask(self):
        kind = self.taskKind
        editor = self._editor
        if kind == 'content':
            from .foreground_content import cancel
            return cancel(editor)
        if (getattr(editor, '_pending_request', None) or {}).get('channel_auto'):
            from .channel_auto import cancel as cancel_channel
            return cancel_channel(editor)
        if (getattr(editor, '_pending_request', None) or {}).get('photo_strategy'):
            from .photo_strategy import cancel
            return cancel(editor)
        if (getattr(editor, '_pending_request', None) or {}).get('mask_refinement'):
            from .mask_refinement import cancel

            return cancel(editor)
        if kind == "pixel":
            pixel_selections.cancel(editor)
        elif kind == "matte":
            matting.cancel(editor)
        elif kind == "ai":
            if editor.ai.busy:
                editor.ai.cancel()
            elif editor.aiObjectPreparing:
                editor.cancelObjectPreparation()
            else:
                editor.cancelRepairPreparation()
        elif kind == "warm":
            pixel_selections.cancel_preparation(editor)

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

    @Property("QVariantList",notify=changed)
    def faces(self):
        from .face_inventory import choices
        return choices(self._editor)

    @Slot(str,bool)
    def selectFace(self,lid,skin=False):
        editor=self._editor
        if not editor.hasImage or editor.busy or editor.hasRegionDraft:
            return
        from .face_inventory import current
        face=next((f for f in current(editor) if f["id"]==lid),None)
        if face is None:
            return editor._notify("没有可靠的人脸定位；可在范围中描述要选择的人脸，或框住可见人脸后描述",True)
        return pixel_selections.select_hint(editor,deepcopy(face["mask"]),anchor=face["anchor"],
                         mask_target="face_skin" if skin else "face",crop=face["skin_crop"],
                         recover_face_anchor=True,
                         features=face.get('face_features'),
                         face_binding={'face_id':face['id'],'source_sha256':editor._sha},
                         origin={"mode":"selection","model":"BiSeNet（本地 AI）"})

    @Slot(str, str)
    def selectFacePart(self, lid, part):
        editor = self._editor
        from ..segmentation.face_parts import PARTS
        if not editor.hasImage or editor.busy or editor.hasRegionDraft or part not in PARTS:
            return
        from .face_inventory import current
        face = next((f for f in current(editor) if f['id'] == lid), None)
        if face is None:
            return editor._notify("未可靠定位到该人脸，请重新识别", True)
        return pixel_selections.select_hint(
            editor, deepcopy(face['mask']), anchor=face['anchor'],
            mask_target=PARTS[part]['target'], crop=face['skin_crop'], recover_face_anchor=True,
            face_scope='region', face_context=face['mask'], face_part=part,
            features=face.get('face_features'),
            origin={"mode": "selection", "model": "BiSeNet（本地 AI）"},
        )

    @Slot(str, str)
    def retouchFace(self, lid, preset):
        editor = self._editor
        presets = {
            "smooth": {"skin_smoothing": 35},
            "rosy": {"exposure": .15, "warmth": 4, "tint": 2, "hsl_orange_saturation": 5},
            "refine": {"skin_smoothing": 25, "exposure": .12, "shadows": 8, "sharpness": 8},
        }
        if not editor._can_edit() or preset not in presets:
            return
        from .face_inventory import current, binding, retouch_layer
        face = next((f for f in current(editor) if f["id"] == lid), None)
        if face is None:
            return editor._notify("未可靠定位到该人脸，请重新识别", True)
        name = face["name"] + " · 面部调整"
        existing = retouch_layer(editor,face)
        if existing:
            editor.selectLayer(existing["id"])
            previous = dict(editor.parameters)
            for key, value in presets[preset].items():
                editor.setParameter(key, value)
            existing['mask']['face_binding'] = binding(editor,face)
            editor.finishGesture()
            self.focusChangedParameters(previous)
            return editor._notify("已调整已有面部图层，可继续微调或撤销" +
                                  ("；当前效果未显示" if not editor.activeDisplay["enabled"] else ""))
        region = {"name": name, "reason": "自然平滑与明暗调整；遮挡和五官边缘需检查",
                  "mask_target": "face_skin", "mask": deepcopy(face["mask"]),
                  "anchor": list(face["anchor"]), "skin_crop": list(face["skin_crop"]),
                  "recover_face_anchor": True,
                  "face_binding": binding(editor,face),
                  **({'face_features':deepcopy(face['face_features'])} if 'face_features' in face else {}),
                  "recipe": Recipe.from_dict(presets[preset]).to_dict()}
        return pixel_selections.select_regions(editor, [region], "仅调整目标人脸的皮肤", True,
                                               origin={"mode": "auto", "model": "BiSeNet（本地 AI）"})

    @Property(str, notify=changed)
    def autoRefineMethod(self):
        editor = self._editor
        caps = {c["id"]: c["available"] for c in editor.imageCapabilities}
        mask = (editor._region_candidate["layers"][editor._region_index]["mask"]
                if editor._region_candidate else editor._candidate)
        # A precise mask already defines the target. Refining its alpha must
        # not replace that target with another semantic prediction.
        if mask is not None and "bitmap" in mask and caps.get("details"):
            return "details"
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
        names = {"details": "AI 边缘", "sam": "重识轮廓", "grabcut": "经典优化", "matte": "透明边缘"}
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
                "id": "details",
                "name": "AI 细化边缘",
                "available": bool(caps.get("details", {}).get("available")),
                "description": "读取原图与已有范围，用 AI 估计边缘透明度；保留提示点和远离边缘的确定范围",
            },
            {
                "id": "matte",
                "name": "细化透明边缘",
                "available": bool(caps.get("matte", {}).get("available")),
                "description": "按原图估计连续透明度，保留发丝等半透明过渡",
            },
            {
                "id": "sam",
                "name": "重新识别轮廓",
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
        if method == "details":
            editor.refineDetails(radius)
        elif method == "matte":
            editor.refineMatte(radius)
        elif method == "sam":
            editor.pixelRefine()
        elif method in ("grabcut", "u2net"):
            editor.refineSelection(method)
        else:
            editor._notify("没有可用的细化方法，请在扩展 → 图像能力中查看配置", True)

    # -- draft output ---------------------------------------------------------

    @Property(bool, notify=changed)
    def editingLayerMask(self):
        editor = self._editor
        return editor.hasSelectionDraft and bool(editor._selection_target_id)

    @Property(str, notify=changed)
    def maskEditLayerName(self):
        editor = self._editor
        return next(
            (l["name"] for l in editor._layers if l["id"] == editor._selection_target_id),
            "",
        )

    @Slot()
    def applyDefault(self):
        self.apply("replace_mask" if self.editingLayerMask else "new_layer")

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
            if not self._editor.hasSelectionDraft:
                self._picked = self._editor.activeLayerId
        elif mode == "inpaint":
            selections.inpaintToLayer(self._editor)
            if not self._editor.hasSelectionDraft:
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
