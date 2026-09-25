"""Session actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from ..engine import ENGINE_VERSION
from ..document import new_layer, read_project, write_project
from ..paths import ROOT, path_from_url


def _base_openImage(self, url):
    if self.busy:
        self._notify("请等待当前任务完成")
        return
    self._generation += 1
    self._timer.stop()
    self._pending_render = None
    self._status = "正在读取照片与色彩信息…"
    expected = (
        self._pending_project[0]["source_sha256"] if self._pending_project else ""
    )
    self._request("open", path=str(path_from_url(url)), expected_sha256=expected)


def loadDemo(self):
    self.openImage(str(ROOT / "assets/lake.jpg"))


def _base_close(self):
    self._closing = True
    self._ai.close()
    self._timer.stop()
    self.process.closeWriteChannel()
    if not self.process.waitForFinished(1500):
        self.process.kill()
        self.process.waitForFinished(1500)
    self._cache.cleanup()


def _opened(self):
    self._pixel_points, self._pixel_hint = [], None
    self._mask_thumbnails.clear()
    self._scene.set(None)
    self._scene_followup = ""
    layer = new_layer("全图调整", True)
    layer["recipe"], layer["locked"] = dict(self._recipe), sorted(self._locked)
    self._layers, self._selected = [layer], layer["id"]
    self._conversation, self._candidate, self._mask_url = [], None, ""
    self._draft_history, self._draft_cursor = [], 0
    self._region_candidate, self._region_index = None, 0
    self._selection_quality = ""
    self._project_path = ""
    self._recovery_path = self._recovery_dir / (uuid4().hex + ".iphoto")
    if self._pending_project:
        payload, path = self._pending_project
        self._pending_project = None
        if payload["source_sha256"] != self._sha:
            self._notify(
                "源图片已变化，旧图层与对话未套用；请恢复原照片后再打开项目", True
            )
        else:
            self._layers, self._selected = payload["layers"], payload["active_layer"]
            self._conversation = payload["conversation"]
            self._project_path = "" if Path(path).parent == self._recovery_dir else path
            self._candidate = payload.get("selection_draft")
            self._region_candidate = payload.get("region_draft")
            self._scene.set(payload.get("scene_catalog"))
            if self._scene.catalog:
                self._scene.remember(self._scene_key())
            if self._candidate is not None:
                self._draft_history = [deepcopy(self._candidate)]
                self._selection_quality = self._quality_text(self._candidate)
            self._load_layer()
            self._summary = "已恢复全部图层、选区与 AI 对话。"
    self._history, self._cursor = [self._snapshot()], 0
    self._dirty = False
    self.conversationChanged.emit()
    self.changed.emit()


def openImage(self, url):
    if self.busy:
        return self._base_openImage(url)
    if not self._save_recovery():
        self._pending_project = None
        self._notify("未切换照片：恢复副本保存失败，请先保存项目", True)
        return
    self._base_openImage(url)


def _payload(self):
    self._sync_layer()
    return {
        "schema_version": "1.6",
        "engine_version": ENGINE_VERSION,
        "source": self._path,
        "source_sha256": self._sha,
        "layers": self._layers,
        "active_layer": self._selected,
        "conversation": self._conversation,
        "selection_draft": self._candidate,
        "region_draft": self._region_candidate,
        "scene_catalog": self._scene.catalog,
    }


def saveProject(self, url):
    if not self.hasImage or self.busy:
        return
    try:
        path = path_from_url(url).resolve()
        if path == Path(self._path).resolve():
            raise ValueError("不能覆盖源照片")
        if path.suffix.lower() not in (".json", ".iphoto"):
            raise ValueError("项目后缀应为 .iphoto 或 .json")
        write_project(path, self._payload(), overwrite=str(path) == self._project_path)
        self._save_recovery()
        self._project_path, self._dirty = str(path), False
        self._notify("已保存图层、选区与对话：" + str(path))
    except (ValueError, OSError) as exc:
        self._notify("保存失败：" + str(exc), True)


def openProject(self, url):
    if self.busy:
        return
    try:
        path = path_from_url(url).resolve()
        payload = read_project(path)
        if not Path(payload["source"]).is_file():
            raise ValueError("找不到源照片，请将原照片放回：" + payload["source"])
        self._pending_project = (payload, str(path))
        self.openImage(payload["source"])
    except (ValueError, KeyError, OSError) as exc:
        self._pending_project = None
        self._notify("打开项目失败：" + str(exc), True)


def _save_recovery(self):
    if not self.hasImage or not self._dirty:
        return True
    try:
        self._recovery_dir.mkdir(parents=True, exist_ok=True)
        write_project(self._recovery_path, self._payload(), overwrite=True)
        self._recovery_error = False
        return True
    except (ValueError, OSError):
        if not self._recovery_error:
            self._recovery_error = True
            self._base_notify("自动恢复副本保存失败，请手动保存项目", True)
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
    for path in files:
        try:
            payload = read_project(path)
            if not Path(payload["source"]).is_file():
                continue
        except (ValueError, OSError):
            continue
        self.openProject(str(path))
        return
    self._notify("没有可用的恢复副本；损坏的文件或缺失源照片的会话已跳过")


def prepareClose(self):
    if self._closing:
        return True
    operations = list(self._queue) + ([self._active] if self._active else [])
    if any(request["op"] == "export" for request in operations):
        self._notify("正在导出照片，请完成后再关闭窗口")
        return False
    if self._ai.busy:
        self._ai.cancel()
    if not self._save_recovery():
        self.recoverySaveFailed.emit()
        return False
    return True


def exportImage(self, url, jpeg_quality=100):
    if self._can_edit():
        self._sync_layer()
        self._status = "正在按原分辨率合成所有图层并导出…"
        self._request(
            "export", path=str(path_from_url(url)), layers=deepcopy(self._layers), jpeg_quality=jpeg_quality
        )


def close(self):
    self._closing = True
    self._ai.close()
    self._save_recovery()
    self._autosave.stop()
    self._base_close()
