"""Native Qt workspace: document state, worker orchestration and AI conversations."""

from __future__ import annotations
from collections import deque
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QProcess, QTimer, Signal, Slot

from .engine import LABELS, RANGES, Recipe
from .ai import AIController
from .document import (
    new_layer,
)
from .plugins import capabilities
from .scene import SceneIndex
from .layer_tree import descendants
from .viewport import Viewport

from .paths import ROOT
from .controllers import (
    worker_bridge,
    layers,
    selections,
    objects,
    conversation,
    session,
    pixel_selections,
    matting,
)


class Editor(QObject):
    changed = Signal()
    layersChanged = Signal()
    conversationChanged = Signal()

    notification = Signal(str, bool)

    imageOpened = Signal()

    aiSettingsRequested = Signal()
    recoverySaveFailed = Signal()

    def _base_init(self, ai_store=None):
        super().__init__()
        self._viewport = Viewport(self)
        self._recipe = Recipe().to_dict()
        self._locked = set()
        self._history = [(dict(self._recipe), set())]
        self._cursor = 0
        self._original = self._preview = self._path = self._name = self._sha = ""
        self._width = self._height = 0
        self._histogram = []
        self._summary = "写下想调整的地方，让照片更接近你眼中的样子。"
        self._status = "正在启动本地引擎…"
        self._elapsed = 0
        self._generation = self._serial = 0
        self._active = None
        self._queue = deque()
        self._pending_render = None
        self._buffer = b""
        self._sample = False
        self._restore = None
        self._closing = False
        self._ai = AIController(self, store=ai_store)
        self._ai.changed.connect(self.changed.emit)
        self._ai.planReady.connect(self._cloud_plan)
        self._ai.failure.connect(lambda message: self._notify(message, True))
        self._cache = tempfile.TemporaryDirectory(
            prefix="iphoto-", ignore_cleanup_errors=True
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._schedule_render)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._finished)
        self.process.started.connect(self._pump)
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self.process.start(
            str(interpreter), [str(ROOT / "run.py"), "--worker", self._cache.name]
        )

    @Property(str, notify=changed)
    def originalUrl(self):
        return self._original

    @Property(QObject, constant=True)
    def viewport(self):
        return self._viewport

    @Property(QObject, constant=True)
    def ai(self):
        return self._ai

    @Property(str, notify=changed)
    def previewUrl(self):
        return self._preview or self._original

    @Property(str, notify=changed)
    def imageName(self):
        return "山湖之间" if self._sample else self._name

    @Property(str, notify=changed)
    def imageInfo(self):
        return (
            f"{self._width:,} × {self._height:,}  ·  sRGB"
            if self._width
            else "JPEG / PNG"
        )

    @Property(float, notify=changed)
    def imageRatio(self):
        return self._width / self._height if self._height else 1.5

    @Property(bool, notify=changed)
    def hasImage(self):
        return bool(self._original)

    @Property(bool, notify=changed)
    def isSample(self):
        return self._sample

    @Property("QVariantMap", notify=changed)
    def parameters(self):
        return self._recipe

    @Property("QVariantList", notify=changed)
    def lockedFields(self):
        return sorted(self._locked)

    @Property("QVariantList", notify=changed)
    def histogram(self):
        return self._histogram

    @Property(str, notify=changed)
    def summary(self):
        return self._summary

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(str, notify=changed)
    def renderTime(self):
        return f"{self._elapsed} ms" if self._elapsed else "—"

    @Property(bool, notify=changed)
    def busy(self):
        operations = list(self._queue) + ([self._active] if self._active else [])
        return self._ai.busy or any(
            request["op"] in {"open", "export", "interpret", "selection", "segment", "matte"}
            for request in operations
        )

    @Property(bool, notify=changed)
    def rendering(self):
        return bool(self._active and self._active["op"] == "render")

    @Property(bool, notify=changed)
    def canUndo(self):
        return (
            self._draft_cursor > 0 if self._candidate is not None else self._cursor > 0
        )

    @Property(bool, notify=changed)
    def canRedo(self):
        return (
            self._draft_cursor < len(self._draft_history) - 1
            if self._candidate is not None
            else self._cursor < len(self._history) - 1
        )

    @Property(str, notify=changed)
    def historyLabel(self):
        return f"编辑步骤 {self._cursor}"

    def _base_notify(self, message, error=False):
        self._status = message
        self.changed.emit()
        self.notification.emit(message, error)

    def _request(self, op, **data):
        return worker_bridge._request(self, op, **data)

    def _pump(self):
        return worker_bridge._pump(self)

    def _stderr(self):
        return worker_bridge._stderr(self)

    def _read(self):
        return worker_bridge._read(self)

    def _process_error(self, _):
        return worker_bridge._process_error(self, _)

    def _finished(self, *_):
        return worker_bridge._finished(self, *_)

    def _base_change(self):
        self._generation += 1
        self._timer.start()
        self.changed.emit()

    @Slot(str)
    def _base_openImage(self, url):
        return session._base_openImage(self, url)

    @Slot()
    def loadDemo(self):
        return session.loadDemo(self)

    @Slot(str, float)
    def _base_setParameter(self, key, value):
        return layers._base_setParameter(self, key, value)

    @Slot()
    def finishGesture(self):
        return layers.finishGesture(self)

    @Slot(str)
    def unlock(self, key):
        return layers.unlock(self, key)

    @Slot(str)
    def _base_applyPreset(self, name):
        return layers._base_applyPreset(self, name)

    @Slot()
    def _base_reset(self):
        return layers._base_reset(self)

    def _base_close(self):
        return session._base_close(self)

    def __init__(self, ai_store=None):
        self._base_init(ai_store)
        self._layers = [new_layer("全图调整", True)]
        self._selected = self._layers[0]["id"]
        self._layer_rows = []
        self.changed.connect(self._publish_layer_rows)
        self._publish_layer_rows()
        self._conversation = []
        self._mask_url = ""
        self._candidate = None
        self._pending_request = None
        self._draft_history, self._draft_cursor = [], 0
        self._selection_quality = ""
        self._mask_view = "overlay"
        self._region_candidate, self._region_index = None, 0
        self._capabilities = capabilities()
        self._auto_refine = False
        self._pixel_points = []
        self._pixel_hint = None
        self._scene = SceneIndex()
        self._scene_followup = ""
        self._mask_thumbnails = {}
        self._pending_project = None
        self._project_path = ""
        self._dirty = False
        self._recovery_error = False
        self._recovery_dir = self._ai.store.directory / "recovery"
        self._recovery_path = self._recovery_dir / (uuid4().hex + ".iphoto")
        self._history = [self._snapshot()]
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(600)
        self._autosave.timeout.connect(self._save_recovery)
        self.imageOpened.connect(self._opened)

    def _layer(self):
        return layers._layer(self)

    def _sync_layer(self):
        return layers._sync_layer(self)

    def _load_layer(self):
        return layers._load_layer(self)

    def _snapshot(self):
        return layers._snapshot(self)

    def _publish_layer_rows(self):
        return layers._publish_layer_rows(self)

    @Property("QVariantList", notify=layersChanged)
    def layers(self):
        # _publish_layer_rows rebuilds this list on every change, so the property
        # can hand it out directly instead of deepcopying on each access.
        return self._layer_rows

    @Property(str, notify=changed)
    def activeLayerId(self):
        return self._selected

    @Property(str, notify=changed)
    def activeLayerName(self):
        return self._layer()["name"]

    @Property(bool, notify=changed)
    def activeIsGroup(self):
        return self._layer().get("kind") == "group"

    @Property(str, notify=changed)
    def activeParentId(self):
        return self._layer().get("parent_id", "")

    @Property("QVariantList", notify=changed)
    def groupTargets(self):
        excluded = descendants(self._layers, self._selected)
        return [{"id": "", "name": "文档根目录"}] + [
            {"id": l["id"], "name": l["name"]}
            for l in self._layers
            if l.get("kind") == "group" and l["id"] not in excluded
        ]

    @Property("QVariantList", notify=changed)
    def sceneObjects(self):
        return self._scene.rows()

    @Property(bool, notify=changed)
    def pixelAvailable(self):
        return any(c["id"] == "pixels" and c["available"] for c in self._capabilities)

    @Property(bool, notify=changed)
    def pixelBusy(self):
        return bool(self._active and self._active["op"] == "segment")

    @Property(bool, notify=changed)
    def matteAvailable(self):
        return any(c["id"] == "matte" and c["available"] for c in self._capabilities)

    @Property(bool, notify=changed)
    def matteBusy(self):
        return bool(self._active and self._active["op"] == "matte")

    @Slot(int)
    def refineMatte(self, radius=8):
        return matting.start(self, radius)

    @Slot()
    def cancelMatte(self):
        return matting.cancel(self)

    @Property("QVariantList", notify=changed)
    def pixelPoints(self):
        return deepcopy(self._pixel_points)

    @Slot(bool)
    def startPixelSelection(self, refine=False):
        return pixel_selections.reset_prompts(self, refine)

    @Slot("QVariantList", bool)
    def pixelPoint(self, point, positive):
        return pixel_selections.point(self, point, positive)

    @Slot()
    def undoPixelPoint(self):
        return pixel_selections.undo_point(self)

    @Slot()
    def cancelPixelSelection(self):
        return pixel_selections.cancel(self)

    @Slot()
    def pixelRefine(self):
        if self.busy or not self.hasImage or self.hasRegionDraft:
            return
        hint = self._candidate or self._layer()["mask"]
        return pixel_selections.select_hint(self, deepcopy(hint))

    @Property("QVariantList", notify=changed)
    def sceneCategories(self):
        return list(
            dict.fromkeys(
                o["category"] for o in (self._scene.catalog or {}).get("objects", [])
            )
        )

    @Property(str, notify=changed)
    def sceneSummary(self):
        return (self._scene.catalog or {}).get(
            "summary", "先分析画面，得到可以点击和组合的对象清单。"
        )

    @Property(int, notify=changed)
    def checkedObjectCount(self):
        return len(self._scene.selected)

    def _scene_key(self):
        return objects._scene_key(self)

    @Slot(bool)
    def analyzeScene(self, refresh=False):
        return objects.analyzeScene(self, refresh)

    @Slot(str)
    def selectByDescription(self, text):
        return objects.selectByDescription(self, text)

    @Slot(str, bool)
    def checkSceneObject(self, lid, checked):
        return objects.checkSceneObject(self, lid, checked)

    @Slot(str, bool)
    def checkSceneCategory(self, category, checked):
        return objects.checkSceneCategory(self, category, checked)

    @Slot()
    def clearObjectChecks(self):
        return objects.clearObjectChecks(self)

    def _objects_mask(self, ids, mode="replace", base=None):
        return objects._objects_mask(self, ids, mode, base)

    @Slot(str)
    def combineObjects(self, mode):
        return objects.combineObjects(self, mode)

    @Slot(float, float, result=str)
    def objectAt(self, x, y):
        return objects.objectAt(self, x, y)

    @Slot("QVariantList", str)
    def clickObject(self, point, mode):
        return objects.clickObject(self, point, mode)

    @Property(float, notify=changed)
    def layerOpacity(self):
        return self._layer()["opacity"] * 100

    @Property(str, notify=changed)
    def selectionLabel(self):
        return self._layer()["mask"]["label"]

    @Property(float, notify=changed)
    def feather(self):
        return self._layer()["mask"]["feather"] * 100

    @Property(str, notify=changed)
    def maskView(self):
        return self._mask_view

    @Property(float, notify=changed)
    def edgeProtection(self):
        mask = self._candidate or self._layer()["mask"]
        return mask.get("edge_protection", 0) * 100

    @Slot(float)
    def setEdgeProtection(self, value):
        return selections.setEdgeProtection(self, value)

    @Slot(str, int)
    def exportWithQuality(self, url, quality):
        return session.exportImage(self, url, quality)

    @Property(str, notify=changed)
    def maskUrl(self):
        return self._mask_url

    @Property(bool, notify=changed)
    def hasSelectionDraft(self):
        return self._candidate is not None

    @Property(str, notify=changed)
    def draftLabel(self):
        return (
            self._candidate["label"]
            if self._candidate is not None
            else "没有待应用的选区"
        )

    @Property(float, notify=changed)
    def draftFeather(self):
        return self._candidate["feather"] * 100 if self._candidate else 0

    @Property(str, notify=changed)
    def selectionQuality(self):
        return self._selection_quality

    @Property(bool, notify=changed)
    def hasRegionDraft(self):
        return self._region_candidate is not None

    @Property("QVariantList", notify=changed)
    def regionDrafts(self):
        if not self._region_candidate:
            return []
        return [
            {
                "name": l["name"],
                "visible": l["visible"],
                "index": i,
                "selected": i == self._region_index,
                "changes": " / ".join(
                    f"{LABELS[k]} {v:+g}" for k, v in l["recipe"].items() if v
                ),
                "quality": self._quality_text(l["mask"]),
            }
            for i, l in enumerate(self._region_candidate["layers"])
        ]

    @Property(str, notify=changed)
    def regionSummary(self):
        return self._region_candidate["summary"] if self._region_candidate else ""

    @Property("QVariantList", notify=changed)
    def imageCapabilities(self):
        return self._capabilities

    @Slot()
    def refreshCapabilities(self):
        self._capabilities = capabilities()
        self.changed.emit()

    @Slot(bool)
    def setAutoRefine(self, enabled):
        return selections.setAutoRefine(self, enabled)

    @Slot(str, result=str)
    def layerMaskThumbnail(self, lid):
        return layers.layerMaskThumbnail(self, lid)

    @Property("QVariantList", notify=conversationChanged)
    def conversation(self):
        return self._conversation

    @Property(bool, notify=changed)
    def dirty(self):
        return self._dirty

    @Property(str, notify=changed)
    def projectPath(self):
        return self._project_path

    @Property("QVariantList", constant=True)
    def tools(self):
        return [
            {
                "key": k,
                "label": LABELS[k],
                "from": lo,
                "to": hi,
                "step": 0.01 if k == "exposure" else 1,
            }
            for k, (lo, hi) in RANGES.items()
        ]

    @Property(bool, notify=changed)
    def canRecover(self):
        return bool(self._recovery_files())

    def _opened(self):
        return session._opened(self)

    def _commit(self):
        return layers._commit(self)

    def _mark_dirty(self):
        return layers._mark_dirty(self)

    def _change(self):
        return layers._change(self)

    def _schedule_render(self):
        return worker_bridge._schedule_render(self)

    def _notify(self, message, error=False):
        if error and getattr(self, "_pending_request", None):
            self._scene_followup = ""
            self._message("error", message, state="failed")
            self._pending_request = None
        self._base_notify(message, error)

    @Slot(str)
    def openImage(self, url):
        return session.openImage(self, url)

    @Slot(str)
    def selectLayer(self, lid):
        return layers.selectLayer(self, lid)

    def _can_edit(self):
        return layers._can_edit(self)

    @Slot()
    def addGlobalLayer(self):
        return layers.addGlobalLayer(self)

    @staticmethod
    def _quality_text(mask):
        return selections._quality_text(mask)

    def _set_candidate(self, mask, record=True):
        return selections._set_candidate(self, mask, record)

    def _record_draft(self):
        return selections._record_draft(self)

    @Slot(str)
    def beginSelection(self, source="empty"):
        return selections.beginSelection(self, source)

    @Slot(str, str, "QVariantList", float)
    def drawDraft(self, kind, mode, points, radius):
        return selections.drawDraft(self, kind, mode, points, radius)

    @Slot(str)
    def draftAction(self, action):
        return selections.draftAction(self, action)

    @Slot(float)
    def setDraftFeather(self, value):
        return selections.setDraftFeather(self, value)

    @Slot()
    def finishSelectionGesture(self):
        return selections.finishSelectionGesture(self)

    @Slot(str)
    def setMaskView(self, value):
        return selections.setMaskView(self, value)

    @Slot(str)
    def refineSelection(self, plugin="grabcut"):
        return selections.refineSelection(self, plugin)

    @Slot("QVariantList", float, str)
    def wandSelection(self, point, tolerance, mode):
        return selections.wandSelection(self, point, tolerance, mode)

    @Slot()
    def selectionToLayer(self):
        return selections.selectionToLayer(self)

    @Slot(int)
    def selectRegion(self, index):
        return selections.selectRegion(self, index)

    @Slot(int)
    def toggleRegion(self, index):
        return selections.toggleRegion(self, index)

    @Slot()
    def acceptRegions(self):
        return selections.acceptRegions(self)

    @Slot()
    def discardRegions(self):
        return selections.discardRegions(self)

    @Slot()
    def addLayer(self):
        return layers.addLayer(self)

    @Slot()
    def duplicateLayer(self):
        return layers.duplicateLayer(self)

    @Slot()
    def deleteLayer(self):
        return layers.deleteLayer(self)

    @Slot(str)
    def renameLayer(self, name):
        return layers.renameLayer(self, name)

    @Slot(str)
    def toggleLayer(self, lid):
        return layers.toggleLayer(self, lid)

    @Slot(int)
    def moveLayer(self, direction):
        return layers.moveLayer(self, direction)

    @Slot()
    def groupLayer(self):
        return layers.groupLayer(self)

    @Slot(str)
    def moveToGroup(self, parent_id):
        return layers.moveToGroup(self, parent_id)

    @Slot()
    def moveOutOfGroup(self):
        return layers.moveOutOfGroup(self)

    @Slot(str)
    def toggleGroup(self, lid):
        return layers.toggleGroup(self, lid)

    @Slot(float)
    def setOpacity(self, value):
        return layers.setOpacity(self, value)

    @Slot(str)
    def selectionAction(self, action):
        return selections.selectionAction(self, action)

    @Slot(float)
    def setFeather(self, value):
        return selections.setFeather(self, value)

    @Slot(str, str, "QVariantList", float)
    def drawSelection(self, kind, mode, points, radius):
        return selections.drawSelection(self, kind, mode, points, radius)

    @Slot()
    def acceptSelection(self):
        return selections.acceptSelection(self)

    @Slot()
    def discardSelection(self):
        return selections.discardSelection(self)

    @Slot()
    def undo(self):
        return layers.undo(self)

    @Slot()
    def redo(self):
        return layers.redo(self)

    @Slot(str, float)
    def setParameter(self, key, value):
        return layers.setParameter(self, key, value)

    @Slot(str)
    def applyPreset(self, name):
        return layers.applyPreset(self, name)

    @Slot()
    def reset(self):
        return layers.reset(self)

    def _safe_text(self, text):
        return conversation._safe_text(self, text)

    def _document_signature(self):
        return conversation._document_signature(self)

    def _message(self, role, text, mode="", state="", recipe=None, origin=None):
        return conversation._message(self, role, text, mode, state, recipe, origin)

    @Slot(str)
    def applyDescription(self, text):
        return conversation.applyDescription(self, text)

    @Slot(str, str)
    def sendMessage(self, text, mode):
        return conversation.sendMessage(self, text, mode)

    def _local_result(self, summary):
        return conversation._local_result(self, summary)

    def _cloud_plan(self, result, generation):
        return conversation._cloud_plan(self, result, generation)

    @Slot(str)
    def applyAdvice(self, message_id):
        return conversation.applyAdvice(self, message_id)

    @Slot(str)
    def copyMessage(self, message_id):
        return conversation.copyMessage(self, message_id)

    @Slot(str)
    def exportConversation(self, url):
        return conversation.exportConversation(self, url)

    def _payload(self):
        return session._payload(self)

    @Slot(str)
    def saveProject(self, url):
        return session.saveProject(self, url)

    @Slot(str)
    def openProject(self, url):
        return session.openProject(self, url)

    def _save_recovery(self):
        return session._save_recovery(self)

    def _recovery_files(self):
        return session._recovery_files(self)

    @Slot()
    def recoverLatest(self):
        return session.recoverLatest(self)

    @Slot(result=bool)
    def prepareClose(self):
        return session.prepareClose(self)

    @Slot(str)
    def exportImage(self, url):
        return session.exportImage(self, url)

    def close(self):
        return session.close(self)
