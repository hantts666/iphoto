"""Native Qt workspace: document state, worker orchestration and AI conversations."""

from __future__ import annotations
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QProcess, QTimer, Signal, Slot

from .engine import LABELS, RANGES, Recipe, CURVE_FIELDS
from .ai import AIController
from .document import (
    new_layer,
)
from .plugins import capabilities
from .scene import SceneIndex
from .layer_tree import descendants
from .layer_rows_model import LayerRowsModel
from .scene_rows_model import SceneRowsModel
from .conversation_rows_model import ConversationRowsModel
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
    auto_adjust,
    heal,
    detail_tiles,
    matte_process,
    export_process,
)
from .controllers.selection_controller import SelectionController


class Editor(QObject):
    @Property(str, constant=True)
    def applicationVersion(self):
        from . import __version__
        return __version__

    changed = Signal()
    layersChanged = Signal()
    conversationChanged = Signal()
    conversationDraftChanged = Signal()
    conversationExportCompleted = Signal(str, str)  # destination, error (empty on success)
    exportCompleted = Signal(str, str)  # destination, error (empty on success)
    projectSaveCompleted = Signal(str, str)  # destination, error (empty on success)

    notification = Signal(str, bool)

    imageOpened = Signal()
    sourceRelinkRequested = Signal(str, str, str)  # original path, project path, reason
    sourceRelinkFailed = Signal(str)
    sourceRelinkCompleted = Signal()

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
        self._preview_generation = -1
        self._width = self._height = 0
        self._histogram = []
        self._stats = None
        self._face_hints = []
        self._summary = "写下想调整的地方，让照片更接近你眼中的样子。"
        self._status = "正在启动本地引擎…"
        self._notification_scope = ""
        self._status_epoch = 0
        self._elapsed = 0
        self._last_render_metrics = {}
        self._generation = self._serial = 0
        self._parameter_preview_span = None
        self._active = None
        self._queue = deque()
        self._pixel_active = None
        self._pixel_queue = deque()
        self._pixel_buffer = b""
        self._pixel_aborting = False
        self._detail_active = self._detail_pending = None
        self._detail_buffer = b""
        self._detail_url = ""
        self._detail_mask_url = ""
        self._detail_original_url = ""
        self._detail_box = None
        self._detail_generation = -1
        self._detail_frame_version = -1
        self._detail_failed_generation = -1
        self._detail_version = 0
        self._detail_serial = 0
        self._detail_aborting = False
        self._matte_active = self._matte_pending = None
        self._matte_buffer = b""
        self._matte_aborting = False
        self._matte_fresh = False
        self._export_request = self._export_cancelled = None
        self._export_buffer = b""
        self._export_line_offset = 0
        self._export_phase = 0
        self._export_final_seen = False
        self._export_aborting = False
        self._export_serial = 0
        self._export_cleanup_pending = []
        self._pending_render = None
        self._buffer = b""
        self._warm_sha = self._warm_ready_sha = ""
        self._warm_abandoned = False
        self._sample = False
        self._restore = None
        self._closing = False
        self._ai = AIController(self, store=ai_store)
        self._ai.changed.connect(self.changed.emit)
        self._ai.planReady.connect(self._cloud_plan)
        self._ai.failure.connect(lambda message: self._notify(message, True))
        self._ai.maskPointsUnavailable.connect(self._mask_points_unavailable)
        self._cache = tempfile.TemporaryDirectory(
            prefix="iphoto-", ignore_cleanup_errors=True
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._schedule_render)
        self._detail_idle_timer = QTimer(self)
        self._detail_idle_timer.setSingleShot(True)
        self._detail_idle_timer.setInterval(20000)
        self._detail_idle_timer.timeout.connect(self._park_detail)
        self.process = QProcess(self)
        self._pixel_process = QProcess(self)
        self._pixel_process.started.connect(self._pump_pixel)
        self._pixel_process.readyReadStandardOutput.connect(self._pixel_read)
        self._pixel_process.readyReadStandardError.connect(self._pixel_stderr)
        self._pixel_process.finished.connect(self._pixel_finished)
        self._pixel_process.errorOccurred.connect(self._pixel_error)
        self._detail_process = QProcess(self)
        self._detail_process.started.connect(self._pump_detail)
        self._detail_process.readyReadStandardOutput.connect(self._detail_read)
        self._detail_process.readyReadStandardError.connect(self._detail_stderr)
        self._detail_process.finished.connect(self._detail_finished)
        self._detail_process.errorOccurred.connect(self._detail_error)
        self._matte_process = QProcess(self)
        self._matte_process.started.connect(self._pump_matte)
        self._matte_process.readyReadStandardOutput.connect(self._matte_read)
        self._matte_process.readyReadStandardError.connect(self._matte_stderr)
        self._matte_process.finished.connect(self._matte_finished)
        self._matte_process.errorOccurred.connect(self._matte_error)
        self._export_process = QProcess(self)
        self._export_process.started.connect(self._export_started)
        self._export_process.readyReadStandardOutput.connect(self._export_read)
        self._export_process.readyReadStandardError.connect(self._export_stderr)
        self._export_process.finished.connect(self._export_finished)
        self._export_process.errorOccurred.connect(self._export_error)
        self._export_cleanup_timer = QTimer(self)
        self._export_cleanup_timer.setSingleShot(True)
        self._export_cleanup_timer.setInterval(100)
        self._export_cleanup_timer.timeout.connect(self._export_retry_cleanup)
        self._warm_process = QProcess(self)
        self._warm_process.finished.connect(self._warm_finished)
        self._warm_process.errorOccurred.connect(self._warm_error)
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

    @Property(int, notify=changed)
    def previewGeneration(self):
        return self._preview_generation

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
    def imageWorkBusy(self):
        operations = (
            list(self._queue)
            + list(self._pixel_queue)
            + ([self._active] if self._active else [])
            + ([self._pixel_active] if self._pixel_active else [])
            + ([self._matte_active] if self._matte_active else [])
            + ([self._matte_pending] if self._matte_pending else [])
            + ([self._export_request] if self._export_request else [])
        )
        return self._export_aborting or self.aiRepairPreparing or self.aiObjectPreparing or self.aiMaskPreparing or self.aiPhotoPreparing or self.aiChannelPreparing or any(
            (request["op"] in ("photo_candidate", "generative_crop", "generative_align", "foreground_import") and not request.get("cancelled"))
            or request["op"] in {"open", "export", "interpret", "selection", "matte", "repair_crop", "object_crop", "mask_refinement_crop", "mask_refinement_apply"}
            or (request["op"] == "segment" and request.get("priority") != "low")
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

    @Property(str, notify=changed)
    def notificationScope(self):
        return self._notification_scope

    def _base_notify(self, message, error=False, *, scope=""):
        # Errors always remain ordinary notices, independent of draft endings.
        self._notification_scope = "" if error else scope
        self._status = message
        self.changed.emit()
        self.notification.emit(message, error)

    def _request(self, op, **data):
        return worker_bridge._request(self, op, **data)

    def _pump(self):
        return worker_bridge._pump(self)

    def _pump_pixel(self):
        return worker_bridge._pump_pixel(self)

    def _pixel_read(self):
        return worker_bridge._pixel_read(self)

    def _pixel_stderr(self):
        return worker_bridge._pixel_stderr(self)

    def _pixel_finished(self, code, status):
        return worker_bridge._pixel_finished(self, code, status)

    def _pixel_error(self, error):
        return worker_bridge._pixel_error(self, error)

    def _stop_pixel(self):
        return worker_bridge._stop_pixel(self)

    def _pump_detail(self):
        return detail_tiles.pump(self)

    def _detail_read(self):
        return detail_tiles.read(self)

    def _detail_stderr(self):
        return detail_tiles.stderr(self)

    def _detail_finished(self, *args):
        return detail_tiles.finished(self, *args)

    def _detail_error(self, error):
        return detail_tiles.error(self, error)

    def _stop_detail(self):
        return detail_tiles.stop(self)

    def _pump_matte(self):
        return matte_process.pump(self)

    def _matte_read(self):
        return matte_process.read(self)

    def _matte_stderr(self):
        return matte_process.stderr(self)

    def _matte_finished(self, *args):
        return matte_process.finished(self, *args)

    def _matte_error(self, reason):
        return matte_process.error(self, reason)

    def _stop_matte(self):
        return matte_process.stop(self)

    def _park_detail(self):
        return detail_tiles.park(self)

    @Slot()
    def requestDetail(self):
        return detail_tiles.request(self)

    @Property(str, notify=changed)
    def detailUrl(self):
        return self._detail_url

    @Property(str, notify=changed)
    def detailMaskUrl(self):
        return self._detail_mask_url

    @Property(bool, notify=changed)
    def detailLoading(self):
        return bool(self._detail_active or self._detail_pending)

    @Property(int, notify=changed)
    def documentGeneration(self):
        return self._generation

    @Property(int, notify=changed)
    def detailGeneration(self):
        return self._detail_generation

    @Property(int, notify=changed)
    def detailRevision(self):
        return self._detail_frame_version

    @Property(int, notify=changed)
    def detailVersion(self):
        return self._detail_version

    @Property(bool, notify=changed)
    def detailFailed(self):
        return self._detail_failed_generation == (self._generation, self._detail_version)

    @Slot(float, "QVariantList", result=bool)
    def wantsDetail(self, zoom, visible_rect):
        return detail_tiles.wanted(self, zoom, visible_rect)

    @Property("QVariantList", notify=changed)
    def detailRect(self):
        if self._detail_box is None or not self._width or not self._height:
            return [0, 0, 0, 0]
        left, top, right, bottom = self._detail_box
        return [left / self._width, top / self._height,
                (right - left) / self._width, (bottom - top) / self._height]

    @Property(str, notify=changed)
    def detailOriginalUrl(self):
        return self._detail_original_url

    def _start_warm(self):
        return worker_bridge._start_warm(self)

    @Property(bool, notify=changed)
    def photoPreparing(self):
        return (
            self.hasImage and self._warm_sha == self._sha and not self._warm_abandoned
            and self._warm_process.state() != QProcess.NotRunning
        )

    @Property(bool, notify=changed)
    def busy(self):
        return self._ai.busy or self._image_edit.busy or self.imageWorkBusy

    def _stop_warm(self):
        return worker_bridge._stop_warm(self)

    def _warm_finished(self, code, status):
        return worker_bridge._warm_finished(self, code, status)

    def _warm_error(self, error):
        return worker_bridge._warm_error(self, error)

    def _stderr(self):
        return worker_bridge._stderr(self)

    def _read(self):
        return worker_bridge._read(self)

    def _process_error(self, _):
        return worker_bridge._process_error(self, _)

    def _finished(self, *_):
        return worker_bridge._finished(self, *_)

    def _export_started(self):
        return export_process.started(self)

    def _export_read(self):
        return export_process.read(self)

    def _export_stderr(self):
        return export_process.stderr(self)

    def _export_finished(self, code, status):
        return export_process.finished(self, code, status)

    def _export_error(self, reason):
        return export_process.error(self, reason)

    def _export_retry_cleanup(self):
        return export_process.retry_cleanup(self)

    def _base_change(self, *, parameter=False):
        from .controllers import preview_updates

        return preview_updates.changed(self, parameter=parameter)

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
    def autoAdjust(self):
        return auto_adjust.autoAdjust(self)

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
        self._layer_rows_model = LayerRowsModel(self)
        self.changed.connect(self._publish_layer_rows)
        self._publish_layer_rows()
        self._conversation = []
        self._conversation_model = ConversationRowsModel(self)
        self._conversation_drafts = {}
        self._conversation_draft_modes = {}
        self._conversation_draft_key = ""
        self._mask_url = ""
        self._candidate = None
        self._selection_target_id = ""
        self._pending_request = None
        from .image_edit import ImageEditController
        from .controllers.photo_strategy import generated
        from .controllers.channel_mask import ChannelMaskController
        self._channel_mask = ChannelMaskController(self)
        self._image_edit = ImageEditController(self._ai, self)
        self._image_edit.changed.connect(self.changed.emit)
        self._image_edit.failure.connect(lambda message: self._notify(message, True))
        self._image_edit.completed.connect(lambda pixels, token, generation: generated(self, pixels, token, generation))
        self._draft_history, self._draft_cursor = [], 0
        self._selection_quality = ""
        self._mask_view = "overlay"
        self._region_candidate, self._region_index = None, 0
        self._capabilities = capabilities()
        self._pixel_points = []
        self._pixel_hint = None
        self._scene = SceneIndex()
        self._scene_rows_model = SceneRowsModel(self)
        self.changed.connect(self._publish_scene_rows)
        self._publish_scene_rows()
        self._scene_followup = ""
        self._mask_thumbnails = {}
        self._pending_project = None
        self._relink_pending = None
        self._project_path = ""
        self._dirty = False
        self._edit_revision = 0
        self._recovery_error = False
        self._recovery_dir = self._ai.store.directory / "recovery"
        self._recovery_path = self._recovery_dir / (uuid4().hex + ".iphoto")
        self._recovered_from = None
        self._recovery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iphoto-recovery")
        self._recovery_future = None
        self._recovery_active_path = None
        self._recovery_pending = None
        self._recovery_poll = QTimer(self)
        self._recovery_poll.setInterval(30)
        self._recovery_poll.timeout.connect(self._poll_recovery)
        self._project_save_future = None
        self._project_save_path = None
        self._project_save_revision = 0
        self._project_save_was_dirty = False
        self._project_save_poll = QTimer(self)
        self._project_save_poll.setInterval(30)
        self._project_save_poll.timeout.connect(self._poll_project_save)
        self._conversation_export_future = None
        self._conversation_export_path = None
        self._conversation_export_poll = QTimer(self)
        self._conversation_export_poll.setInterval(30)
        self._conversation_export_poll.timeout.connect(self._poll_conversation_export)
        self._history = [self._snapshot()]
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(600)
        self._autosave.timeout.connect(self._queue_recovery)
        self.imageOpened.connect(self._opened)
        self._selection = SelectionController(self)

    @Property(QObject, constant=True)
    def selection(self):
        return self._selection

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

    @Property(QObject, constant=True)
    def layerRowsModel(self):
        return self._layer_rows_model

    @Property(str, notify=changed)
    def activeLayerId(self):
        return self._selected

    @Property(str, notify=changed)
    def activeLayerName(self):
        return self._layer()["name"]

    @Property("QVariantMap", notify=changed)
    def activeDisplay(self):
        return layers.activeDisplay(self)

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

    @Slot(str, result="QVariantMap")
    def layerContext(self, lid):
        return layers.layerContext(self, lid)

    @Slot(str, str, str, result=bool)
    def runLayerAction(self, lid, action, parent_id=""):
        return layers.runLayerAction(self, lid, action, parent_id)

    @Property("QVariantList", notify=changed)
    def sceneObjects(self):
        return self._scene.rows()

    def _publish_scene_rows(self):
        self._scene_rows_model.replace(self._scene.rows(), self._scene.revision)

    @Property(QObject, constant=True)
    def sceneRowsModel(self):
        return self._scene_rows_model

    @Property(int, notify=changed)
    def sceneRevision(self):
        return self._scene.revision

    @Property(bool, notify=changed)
    def pixelAvailable(self):
        return any(c["id"] == "pixels" and c["available"] for c in self._capabilities)

    @Property(bool, notify=changed)
    def pixelBusy(self):
        return bool(
            self._pixel_active
            or (self._active and self._active["op"] == "segment")
        )

    @Property(bool, notify=changed)
    def matteAvailable(self):
        return any(c["id"] == "matte" and c["available"] for c in self._capabilities)

    @Property(bool, notify=changed)
    def matteBusy(self):
        return bool(self._matte_active or self._matte_pending)

    @Slot(int)
    def refineMatte(self, radius=8):
        return matting.start(self, radius)

    @Slot(int)
    def refineDetails(self, radius=8):
        return matting.start(self, radius, method="neural")

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

    @Slot()
    def adjustCheckedObjects(self):
        return objects.adjustCheckedObjects(self)

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

    @Property(float, notify=changed)
    def edgeShift(self):
        mask = self._candidate or self._layer()["mask"]
        return mask.get("edge_shift", 0)

    @Slot(float)
    def setEdgeShift(self, value):
        return selections.setEdgeShift(self, value)

    @Slot("QVariantList", float)
    def drawHeal(self, points, radius):
        return heal.drawHeal(self, points, radius)

    @Property("QVariantMap", notify=changed)
    def activeRepairInfo(self):
        return heal.activeRepairInfo(self)

    @Slot(str, result="QVariantList")
    def conversationAdjustmentLayers(self, message_id):
        return conversation.conversationAdjustmentLayers(self, message_id)

    @Property(bool, notify=changed)
    def canReviewAdjustment(self):
        from .controllers.adjustment_review import reviewable

        return self.hasImage and reviewable(self._layer())

    @Slot(str, result="QVariantList")
    def conversationRepairLayers(self, message_id):
        return conversation.conversationRepairLayers(self, message_id)

    @Slot(str, result="QVariantMap")
    def failedPromptInfo(self, message_id):
        return conversation.failedPromptInfo(self, message_id)

    @Slot(str, result=bool)
    def restoreFailedPrompt(self, message_id):
        return conversation.restoreFailedPrompt(self, message_id)

    @Slot(str, result=bool)
    def retryFailedPrompt(self, message_id):
        return conversation.retryFailedPrompt(self, message_id)

    @Slot(str, int, result=bool)
    def exportWithQuality(self, url, quality):
        return session.exportImage(self, url, quality)

    @Slot()
    def cancelExport(self):
        return export_process.cancel(self)

    @Property(str, notify=changed)
    def exportProgress(self):
        return export_process.progress_text(self) if self._export_request else ""

    @Slot(int, result=str)
    def suggestExportPath(self, format_index):
        return session.suggestExportPath(self, format_index)

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
                    (f"{LABELS[k]} · {len(v)}点" if k in CURVE_FIELDS else f"{LABELS[k]} {v:+g}")
                    for k, v in l["recipe"].items() if v
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

    @Slot(str, result=str)
    def layerMaskThumbnail(self, lid):
        return layers.layerMaskThumbnail(self, lid)

    @Property("QVariantList", notify=conversationChanged)
    def conversation(self):
        return self._conversation

    @Property(QObject, constant=True)
    def conversationModel(self):
        return self._conversation_model

    @Property(int, notify=conversationChanged)
    def conversationCount(self):
        return len(self._conversation)

    @Property(str, notify=conversationDraftChanged)
    def conversationDraft(self):
        return self._conversation_drafts.get(self._conversation_draft_key, "")

    @Property(str, notify=conversationDraftChanged)
    def conversationDraftMode(self):
        return self._conversation_draft_modes.get(self._conversation_draft_key, "edit")

    @Slot(str)
    def setConversationDraft(self, text):
        if not self.hasImage or not self._conversation_draft_key:
            return
        if text == self.conversationDraft:
            return
        if len(text) > 4000:
            return
        self._conversation_drafts[self._conversation_draft_key] = text
        was_dirty = self._dirty
        self._mark_dirty()
        self.conversationDraftChanged.emit()
        if not was_dirty:
            self.changed.emit()

    @Slot(str)
    def setConversationDraftMode(self, mode):
        if (
            not self.hasImage
            or not self._conversation_draft_key
            or mode not in ("edit", "advice", "regions")
            or mode == self.conversationDraftMode
        ):
            return
        self._conversation_draft_modes[self._conversation_draft_key] = mode
        if not self.conversationDraft:
            self.conversationDraftChanged.emit()
            return
        was_dirty = self._dirty
        self._mark_dirty()
        self.conversationDraftChanged.emit()
        if not was_dirty:
            self.changed.emit()

    @Slot(str, result=str)
    def conversationMessageState(self, message_id):
        return next(
            (message["state"] for message in self._conversation if message["id"] == message_id),
            "",
        )

    @Property(bool, notify=changed)
    def dirty(self):
        return self._dirty

    @Property(str, notify=changed)
    def projectPath(self):
        return self._project_path

    @Property(str, notify=changed)
    def photoBrowseDirectory(self):
        if self._path and not self._sample:
            return str(Path(self._path).parent)
        pictures = Path.home() / "Pictures"
        return str(pictures if pictures.is_dir() else Path.home())

    @Property(str, notify=changed)
    def projectBrowseDirectory(self):
        return (
            str(Path(self._project_path).parent)
            if self._project_path else self.photoBrowseDirectory
        )

    @Property(bool, notify=changed)
    def savingProject(self):
        return self._project_save_future is not None

    @Property(bool, notify=changed)
    def aiRepairPreparing(self):
        return bool((self._pending_request or {}).get("repair_grounding", {}).get("preparing"))

    @Slot()
    def cancelRepairPreparation(self):
        return conversation.cancelRepairPreparation(self)

    @Property(bool, notify=changed)
    def aiObjectPreparing(self):
        return bool((self._pending_request or {}).get("object_grounding", {}).get("preparing"))

    @Property(bool, notify=changed)
    def aiMaskPreparing(self):
        return bool((self._pending_request or {}).get("mask_refinement", {}).get("preparing"))

    @Property(QObject, constant=True)
    def channelMask(self):
        return self._channel_mask

    @Property(bool, notify=changed)
    def aiPhotoPreparing(self):
        return bool((self._pending_request or {}).get('photo_strategy'))

    @Property(bool, notify=changed)
    def aiChannelPreparing(self):
        return bool((self._pending_request or {}).get('channel_auto'))

    @Slot()
    def cancelObjectPreparation(self):
        from .controllers.object_grounding import cancel_preparation

        return cancel_preparation(self)

    @Property(bool, notify=changed)
    def exportingConversation(self):
        return self._conversation_export_future is not None

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

    def _change(self, *, parameter=False):
        return layers._change(self, parameter=parameter)

    def _schedule_render(self):
        return worker_bridge._schedule_render(self)

    def _notify(self, message, error=False, *, background=False, scope=""):
        if (scope == "draft" and not error
                and not self.hasSelectionDraft and not self.hasRegionDraft):
            # A synchronous auto-output may have consumed the draft before
            # its creator finishes emitting the initial guidance.
            return
        if not background:
            self._status_epoch += 1
        if (error and message == '已取消 AI 请求，参数未改变'
                and (getattr(self,'_pending_request',None) or {}).get('mask_refinement',{}).get('cancel_requested')):
            from .controllers.mask_refinement import cancelled
            return cancelled(self)
        if (error and message == '已取消 AI 请求，参数未改变'
                and (self._pending_request or {}).get('photo_strategy', {}).get('cancel_requested')):
            from .controllers.photo_strategy import cancelled
            return cancelled(self)
        if error and message == '已取消选区图像编辑，照片未改变' and (self._pending_request or {}).get('photo_strategy'):
            from .controllers.photo_strategy import cancelled
            return cancelled(self)
        if error and getattr(self, "_pending_request", None):
            self._scene_followup = ""
            self._message("error", message, state="failed")
            self._pending_request = None
        self._base_notify(message, error, scope=scope)

    @Slot(str)
    def openImage(self, url):
        return session.openImage(self, url)

    @Slot(str, result=bool)
    def importForeground(self, url):
        from .controllers.foreground_content import begin
        return begin(self, url)

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

    @Slot(result=bool)
    def restoreLayerDisplay(self):
        return layers.restoreLayerDisplay(self)

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

    @Slot(str, str, result=bool)
    def sendMessage(self, text, mode):
        return conversation.sendMessage(self, text, mode)

    def _local_result(self, summary):
        return conversation._local_result(self, summary)

    def _cloud_plan(self, result, generation):
        return conversation._cloud_plan(self, result, generation)

    def _mask_points_unavailable(self, generation):
        from .controllers.mask_refinement import points_unavailable

        return points_unavailable(self, generation)

    @Slot(str)
    def applyAdvice(self, message_id):
        return conversation.applyAdvice(self, message_id)

    @Slot(str)
    def copyMessage(self, message_id):
        return conversation.copyMessage(self, message_id)

    @Slot(str, result=bool)
    def exportConversation(self, url):
        return conversation.exportConversation(self, url)

    @Slot(str, result=bool)
    def exportConversationAsync(self, url):
        return conversation.exportConversationAsync(self, url)

    def _poll_conversation_export(self):
        conversation._finish_conversation_export(self)

    @Slot(result=str)
    def suggestConversationPath(self):
        return conversation.suggestConversationPath(self)

    def _payload(self):
        return session._payload(self)

    @Slot(str)
    def saveProject(self, url):
        return session.saveProject(self, url)

    @Slot(str, result=bool)
    def saveProjectAsync(self, url):
        return session.saveProjectAsync(self, url)

    @Slot(result=str)
    def suggestProjectPath(self):
        return session.suggestProjectPath(self)

    def _poll_project_save(self):
        return session._poll_project_save(self)

    @Slot(str)
    def openProject(self, url):
        return session.openProject(self, url)

    @Slot(str, result=bool)
    def relinkProjectSource(self, url):
        return session.relinkProjectSource(self, url)

    @Slot()
    def cancelProjectRelink(self):
        return session.cancelProjectRelink(self)

    def _save_recovery(self):
        return session._save_recovery(self)

    def _queue_recovery(self):
        return session._queue_recovery(self)

    def _poll_recovery(self):
        return session._poll_recovery(self)

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

    @Slot(str,str,result=bool)
    def exportRange(self,url,output):
        return session.exportRange(self,url,output)

    def close(self):
        return session.close(self)
