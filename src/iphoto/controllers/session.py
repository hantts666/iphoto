"""Session actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from ..engine import ENGINE_VERSION, validate_export_destination
from ..document import new_layer, read_project, write_project
from ..masks import clear_decode_cache
from ..paths import ROOT, path_from_url
from . import export_process


def _base_openImage(self, url):
    if self.busy:
        self._notify("请等待当前任务完成")
        return False
    self._generation += 1
    self._timer.stop()
    self._pending_render = None
    self._stop_detail()
    self._stop_matte()
    # Old background masks belong to the previous photo. A running job can
    # finish, but the new open request must not wait behind its queued siblings.
    self._queue = type(self._queue)(
        request for request in self._queue if request.get("priority") != "low"
    )
    self._pixel_queue = type(self._pixel_queue)(
        request for request in self._pixel_queue if request.get("priority") != "low"
    )
    if self._pixel_active and self._pixel_active.get("priority") == "low":
        self._stop_pixel()
    self._stop_warm()
    self._warm_ready_sha = ""
    self._status = "正在读取照片与色彩信息…"
    expected = (
        self._pending_project[0]["source_sha256"] if self._pending_project else ""
    )
    return self._request("open", path=str(path_from_url(url)), expected_sha256=expected) is not False


def loadDemo(self):
    self.openImage(str(ROOT / "assets/lake.jpg"))


def _base_close(self):
    self._closing = True
    self._ai.close()
    self._timer.stop()
    self._stop_warm()
    self._warm_process.waitForFinished(1000)
    self._stop_pixel()
    self._pixel_process.waitForFinished(1000)
    self._stop_detail()
    self._detail_process.waitForFinished(1000)
    self._stop_matte()
    self._matte_process.waitForFinished(1000)
    export_process.shutdown(self)
    self.process.closeWriteChannel()
    if not self.process.waitForFinished(1500):
        self.process.kill()
        self.process.waitForFinished(1500)
    self._cache.cleanup()


def _opened(self):
    # Clear the previous document's focus before publishing restored layers.
    self._selection._reset_document_focus()
    clear_decode_cache()
    self._pixel_points, self._pixel_hint = [], None
    self._mask_thumbnails.clear()
    self._scene.set(None)
    self._scene_followup = ""
    layer = new_layer("全图调整", True)
    layer["recipe"], layer["locked"] = dict(self._recipe), sorted(self._locked)
    self._layers, self._selected = [layer], layer["id"]
    self._conversation, self._candidate, self._mask_url = [], None, ""
    self._selection_target_id = ""
    self._draft_history, self._draft_cursor = [], 0
    self._region_candidate, self._region_index = None, 0
    self._selection_quality = ""
    self._project_path = ""
    self._recovery_path = self._recovery_dir / (uuid4().hex + ".iphoto")
    relinked = False
    self._recovered_from = None
    loaded_conversation_draft = ""
    loaded_conversation_mode = "edit"
    if self._pending_project:
        payload, path = self._pending_project
        self._pending_project = None
        if payload["source_sha256"] != self._sha:
            self._notify(
                "源图片已变化，旧图层与对话未套用；请恢复原照片后再打开项目", True
            )
        else:
            relinked = Path(self._path).resolve() != Path(payload["source"]).resolve()
            self._layers, self._selected = payload["layers"], payload["active_layer"]
            self._conversation = payload["conversation"]
            loaded_conversation_draft = payload.get("conversation_draft", "")
            loaded_conversation_mode = payload.get("conversation_draft_mode", "edit")
            recovered = Path(path).parent == self._recovery_dir
            self._project_path = "" if recovered else path
            self._recovered_from = Path(path) if recovered else None
            self._candidate = payload.get("selection_draft")
            self._selection_target_id = payload.get("selection_target_id", "")
            self._region_candidate = payload.get("region_draft")
            self._scene.set(payload.get("scene_catalog"))
            if self._scene.catalog:
                self._scene.remember(self._scene_key())
                from . import pixel_selections

                pixel_selections.precache(
                    self, [o["id"] for o in self._scene.catalog["objects"]]
                )
            if self._candidate is not None:
                self._draft_history = [deepcopy(self._candidate)]
                self._selection_quality = self._quality_text(self._candidate)
            self._load_layer()
            self._summary = "已恢复全部图层、选区与 AI 对话。"
    self._history, self._cursor = [self._snapshot()], 0
    self._dirty = relinked or self._recovered_from is not None
    if self._dirty:
        self._autosave.start()
    draft_key = str(self._recovered_from or self._project_path or self._path)
    self._conversation_draft_key = draft_key
    if draft_key not in self._conversation_drafts and loaded_conversation_draft:
        self._conversation_drafts[draft_key] = loaded_conversation_draft
    self._conversation_draft_modes.setdefault(draft_key, loaded_conversation_mode)
    self.conversationDraftChanged.emit()
    self._conversation_model.replace(self._conversation)
    self.conversationChanged.emit()
    self.changed.emit()
    if self._relink_pending:
        self._relink_pending = None
        self.sourceRelinkCompleted.emit()
    if relinked:
        self._notify("已找回原照片；请保存项目以更新源照片位置")


def openImage(self, url):
    if self.savingProject:
        self._notify("正在保存项目，请稍候再切换照片")
        return False
    if self.busy:
        return self._base_openImage(url)
    if not self._save_recovery():
        self._pending_project = None
        self._notify("未切换照片：恢复副本保存失败，请先保存项目", True)
        return
    return self._base_openImage(url)


def _payload(self):
    self._sync_layer()
    return {
        "schema_version": "1.11" if any(l.get("pixel_patch") for l in self._layers) else "1.10",
        "engine_version": ENGINE_VERSION,
        "source": self._path,
        "source_sha256": self._sha,
        "layers": self._layers,
        "active_layer": self._selected,
        "conversation": self._conversation,
        "conversation_draft": self._safe_text(self.conversationDraft),
        "conversation_draft_mode": self.conversationDraftMode,
        "selection_draft": self._candidate,
        "selection_target_id": self._selection_target_id,
        "region_draft": self._region_candidate,
        "scene_catalog": self._scene.catalog,
    }


def _move_conversation_draft(self, key):
    old_key = self._conversation_draft_key
    if old_key == key:
        return
    draft = self._conversation_drafts.pop(old_key, "")
    mode = self._conversation_draft_modes.pop(old_key, "edit")
    self._conversation_draft_key = key
    self._conversation_drafts[key] = draft
    self._conversation_draft_modes[key] = mode
    self.conversationDraftChanged.emit()


def saveProject(self, url):
    if not self.hasImage or self.busy:
        return
    try:
        _finish_project_save(self, wait=True)
        path = path_from_url(url).resolve()
        if path == Path(self._path).resolve():
            raise ValueError("不能覆盖源照片")
        if path.suffix.lower() not in (".json", ".iphoto"):
            raise ValueError("项目后缀应为 .iphoto 或 .json")
        _flush_recovery(self)
        write_project(path, self._payload(), overwrite=str(path) == self._project_path)
        self._project_path, self._dirty = str(path), False
        _move_conversation_draft(self, str(path))
        self._autosave.stop()
        cleaned = _discard_recovery(self, self._recovery_path)
        if self._recovered_from:
            cleaned = _discard_recovery(self, self._recovered_from) and cleaned
            if cleaned:
                self._recovered_from = None
        message = "已保存图层、选区与对话：" + str(path)
        if not cleaned:
            message += "；旧恢复副本未能清理"
        self._notify(message)
    except (ValueError, OSError) as exc:
        self._notify("保存失败：" + str(exc), True)


def saveProjectAsync(self, url):
    if not self.hasImage or self.busy or self.savingProject:
        return False
    try:
        path = path_from_url(url).resolve()
        if path == Path(self._path).resolve():
            raise ValueError("不能覆盖源照片")
        if path.suffix.lower() not in (".json", ".iphoto"):
            raise ValueError("项目后缀应为 .iphoto 或 .json")
        snapshot = deepcopy(self._payload())
        revision = self._edit_revision
        # The single writer serializes this save behind an in-flight recovery.
        # Discard only queued old snapshots so a later autosave cannot resurrect them.
        self._recovery_pending = None
        self._autosave.stop()
        self._project_save_path = path
        self._project_save_revision = revision
        self._project_save_was_dirty = self._dirty
        self._project_save_future = self._recovery_executor.submit(
            write_project, path, snapshot, str(path) == self._project_path
        )
        self._project_save_poll.start()
        self._notify("正在保存项目…")
        return True
    except (ValueError, OSError, RuntimeError, MemoryError) as exc:
        self._project_save_path = None
        if self._dirty:
            self._autosave.start()
        message = "保存失败：" + str(exc)
        self._notify(message, True)
        self.projectSaveCompleted.emit(str(url), message)
        return False


def _poll_project_save(self):
    _finish_project_save(self)


def _finish_project_save(self, *, wait=False):
    future = self._project_save_future
    if future is None or (not wait and not future.done()):
        return
    path = self._project_save_path
    revision = self._project_save_revision
    self._project_save_future = self._project_save_path = None
    self._project_save_poll.stop()
    try:
        future.result()
        self._project_path = str(path)
        _move_conversation_draft(self, str(path))
        if revision == self._edit_revision:
            _flush_recovery(self)
            self._dirty = False
            self._autosave.stop()
            cleaned = _discard_recovery(self, self._recovery_path)
            if self._recovered_from:
                cleaned = _discard_recovery(self, self._recovered_from) and cleaned
                if cleaned:
                    self._recovered_from = None
            message = "已保存图层、选区与对话：" + str(path)
            if not cleaned:
                message += "；旧恢复副本未能清理"
        else:
            self._dirty = True
            self._autosave.start()
            message = "项目已保存；保存期间的新修改仍未保存"
        self._notify(message)
        self.projectSaveCompleted.emit(str(path), "")
    except Exception as exc:
        self._dirty = self._dirty or self._project_save_was_dirty or revision != self._edit_revision
        if self._dirty:
            self._autosave.start()
        message = "保存失败：" + str(exc)
        self._notify(message, True)
        self.projectSaveCompleted.emit(str(path), message)


def openProject(self, url):
    if self.savingProject:
        self._notify("正在保存项目，请稍候再打开其他项目")
        return
    if self.busy:
        return
    try:
        path = path_from_url(url).resolve()
        payload = read_project(path)
        self._relink_pending = None
        if not Path(payload["source"]).is_file():
            self._relink_pending = (payload, str(path))
            reason = "找不到原照片；请选择移动后的原文件"
            self._notify(reason)
            self.sourceRelinkRequested.emit(payload["source"], str(path), reason)
            return
        self._pending_project = (payload, str(path))
        if not self.openImage(payload["source"]):
            self._pending_project = None
    except (ValueError, KeyError, OSError) as exc:
        self._pending_project = None
        self._notify("打开项目失败：" + str(exc), True)


def relinkProjectSource(self, url):
    if not self._relink_pending:
        return False
    if self.busy:
        self.sourceRelinkFailed.emit("请等待当前任务完成")
        return False
    try:
        candidate = path_from_url(url).resolve()
        if not candidate.is_file():
            raise ValueError("找不到所选照片，请检查文件位置")
        if not self._save_recovery():
            raise ValueError("当前编辑的恢复副本保存失败，请先保存项目")
        self._pending_project = self._relink_pending
        if not self._base_openImage(str(candidate)):
            self._pending_project = None
            raise ValueError("本地引擎未运行，请重新打开 iPhoto")
        return True
    except (ValueError, OSError) as exc:
        self.sourceRelinkFailed.emit(str(exc))
        return False


def cancelProjectRelink(self):
    if self._pending_project or self._relink_pending is None:
        return
    self._relink_pending = None
    self._notify("已取消找回原照片；当前编辑未改变")


def project_open_failed(self, error):
    if not self._pending_project:
        return
    payload, path = self._pending_project
    self._pending_project = None
    if self._relink_pending:
        self.sourceRelinkFailed.emit(error)
    else:
        self._relink_pending = (payload, path)
        self.sourceRelinkRequested.emit(payload["source"], path, error)


def _save_recovery(self):
    _flush_recovery(self)
    if not self.hasImage or not self._dirty:
        return True
    try:
        self._recovery_dir.mkdir(parents=True, exist_ok=True)
        write_project(self._recovery_path, self._payload(), overwrite=True)
        if self._recovered_from and _discard_recovery(self, self._recovered_from):
            self._recovered_from = None
            self.changed.emit()
        self._recovery_error = False
        return True
    except (ValueError, OSError):
        if not self._recovery_error:
            self._recovery_error = True
            self._base_notify("自动恢复副本保存失败，请手动保存项目", True)
        return False


def _queue_recovery(self):
    if not self.hasImage or not self._dirty:
        return
    try:
        self._recovery_dir.mkdir(parents=True, exist_ok=True)
        # The worker must never read mutable layer dictionaries while the user edits.
        self._recovery_pending = (self._recovery_path, deepcopy(self._payload()))
        _start_recovery(self)
    except (ValueError, OSError, MemoryError, RuntimeError):
        _recovery_failed(self)


def _start_recovery(self):
    if self._recovery_future is not None or self._recovery_pending is None:
        return
    path, payload = self._recovery_pending
    self._recovery_pending = None
    self._recovery_active_path = path
    self._recovery_future = self._recovery_executor.submit(
        write_project, path, payload, True
    )
    self._recovery_poll.start()


def _recovery_failed(self):
    if not self._recovery_error:
        self._recovery_error = True
        self._base_notify("自动恢复副本保存失败，请手动保存项目", True)


def _poll_recovery(self):
    future = self._recovery_future
    if future is None or not future.done():
        return
    path = self._recovery_active_path
    self._recovery_future = self._recovery_active_path = None
    try:
        future.result()
        if path == self._recovery_path:
            if self._recovered_from and _discard_recovery(self, self._recovered_from):
                self._recovered_from = None
                self.changed.emit()
        self._recovery_error = False
    except Exception:
        _recovery_failed(self)
    if self._recovery_pending is None:
        self._recovery_poll.stop()
    else:
        _start_recovery(self)


def _flush_recovery(self):
    self._recovery_pending = None
    while self._recovery_future is not None:
        # Poll on the UI thread so cleanup and notifications preserve ordering.
        try:
            self._recovery_future.result()
        except Exception:
            pass
        _poll_recovery(self)
    self._recovery_poll.stop()


def _discard_recovery(self, path):
    """Only remove a session-owned recovery file after another durable copy exists."""
    try:
        target = Path(path).resolve()
        if target.parent != self._recovery_dir.resolve() or target.suffix != ".iphoto":
            return False
        target.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _recovery_files(self):
    try:
        return sorted(
            [
                p
                for p in self._recovery_dir.glob("*.iphoto")
                if p != self._recovery_path
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []


def recoverLatest(self):
    files = self._recovery_files()
    moved_source = None
    for path in files:
        try:
            payload = read_project(path)
            if not Path(payload["source"]).is_file():
                if moved_source is None:
                    moved_source = path
                continue
        except (ValueError, OSError):
            continue
        self.openProject(str(path))
        return
    if moved_source is not None:
        self.openProject(str(moved_source))
    else:
        self._notify("没有可用的恢复副本；损坏的文件已跳过")


def prepareClose(self):
    if self._closing:
        return True
    if self.exportingConversation:
        self._notify("正在导出对话，请完成后再关闭窗口")
        return False
    operations = list(self._queue) + ([self._active] if self._active else [])
    if self._export_request or any(request["op"] == "export" for request in operations):
        self._notify("正在导出照片，请完成后再关闭窗口")
        return False
    if self._ai.busy:
        self._ai.cancel()
    _finish_project_save(self, wait=True)
    if not self._save_recovery():
        self.recoverySaveFailed.emit()
        return False
    return True


def exportImage(self, url, jpeg_quality=100):
    if not self._can_edit():
        if self.hasSelectionDraft:
            self._notify('当前范围尚未保存，请选择透明选区/黑白蒙版导出，或先保存范围',True)
        return False
    try:
        target = validate_export_destination(self._path, path_from_url(url))
    except (ValueError, OSError) as exc:
        self._notify(str(exc), True)
        return False
    self._sync_layer()
    self._status = "正在按原分辨率合成所有图层并导出…"
    return export_process.queue(self, target, jpeg_quality)


def exportRange(self, url, output):
    if not self.hasImage or self.busy or self.hasRegionDraft or self.activeIsGroup:
        return False
    try:
        if output not in ('cutout','mask'):
            raise ValueError('未知选区导出格式')
        target = validate_export_destination(self._path,path_from_url(url))
        if target.suffix.lower() != '.png':
            raise ValueError('透明选区和黑白蒙版请导出为 PNG')
        mask = deepcopy(self._candidate or self._layer()['mask'])
        from ..document import raster_mask_cached
        alpha = raster_mask_cached(mask,(256,256))
        if not alpha.getbbox() or alpha.getextrema() == (255,255):
            raise ValueError('请先选择需要保留的目标，再导出选区')
        self._sync_layer()
        snapshot = deepcopy(self._layers)
        if self._candidate is not None and self._selection_target_id:
            if self._selection_target_id != self._selected:
                raise ValueError('原图层已变化，范围未导出')
            next(layer for layer in snapshot if layer['id']==self._selection_target_id)['mask']=mask
        return export_process.queue(self,target,100,mask=mask,output=output,layers_snapshot=snapshot)
    except (ValueError,OSError) as exc:
        self._notify(str(exc),True)
        return False


def suggestExportPath(self, format_index=0):
    """Offer an absolute, unused path beside the source (or in Pictures for the demo)."""
    if not self._path or format_index not in (0, 1):
        return ""
    source = Path(self._path)
    directory, stem = source.parent, source.stem
    if self._sample:
        pictures = Path.home() / "Pictures"
        directory = pictures if pictures.is_dir() else Path.home()
        stem = "山湖之间"
    suffix = ".jpg" if format_index == 0 else ".png"
    candidate = directory / f"{stem}-编辑{suffix}"
    number = 2
    while candidate.exists():
        candidate = directory / f"{stem}-编辑-{number}{suffix}"
        number += 1
    return str(candidate)


def suggestProjectPath(self):
    """Suggest an unused project name beside the photo or current project."""
    if not self._path:
        return ""
    if self._project_path:
        existing = Path(self._project_path)
        directory, stem = existing.parent, existing.stem + "-副本"
    else:
        source = Path(self._path)
        directory, stem = source.parent, source.stem + "-项目"
        if self._sample:
            pictures = Path.home() / "Pictures"
            directory = pictures if pictures.is_dir() else Path.home()
            stem = "山湖之间-项目"
    candidate = directory / f"{stem}.iphoto"
    number = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{number}.iphoto"
        number += 1
    return str(candidate)


def close(self):
    self._closing = True
    self._image_edit.close()
    self._ai.close()
    from . import conversation

    conversation._finish_conversation_export(self, wait=True)
    _finish_project_save(self, wait=True)
    self._save_recovery()
    if not self._dirty:
        _discard_recovery(self, self._recovery_path)
        if self._recovered_from and _discard_recovery(self, self._recovered_from):
            self._recovered_from = None
    self._autosave.stop()
    self._recovery_poll.stop()
    self._project_save_poll.stop()
    self._conversation_export_poll.stop()
    self._recovery_executor.shutdown(wait=True, cancel_futures=True)
    self._base_close()
