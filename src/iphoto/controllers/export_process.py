"""Cancellable, one-shot full-resolution export with an owned staging folder."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QProcess

from ..engine import validate_export_destination
from ..paths import ROOT
from ..storage import publish_staged_file

PHASES = (
    "正在启动导出…", "正在读取原照片…", "正在按原图尺寸合成图层…",
    "正在写入照片文件…", "正在完成导出…",
)


def _phase(self, phase):
    if phase <= self._export_phase:
        return
    self._export_phase = phase
    self._status = PHASES[phase]
    self.changed.emit()


def _cleanup(request):
    if not request:
        return True
    folder = Path(request["stage_dir"])
    target = Path(request["path"])
    # Only remove the exact, random staging folder created beside this target.
    if not (folder.name.startswith(".iphoto-export-") and folder.parent.resolve() == target.parent.resolve()):
        raise ValueError("导出临时目录不在目标目录内")
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise ValueError("导出临时目录被替换，已停止清理")
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def _cleanup_or_retry(self, request):
    if not _cleanup(request):
        self._export_cleanup_pending.append((request, 0))
        self._export_cleanup_timer.start()


def retry_cleanup(self):
    remaining = []
    for request, attempts in self._export_cleanup_pending:
        if _cleanup(request):
            continue
        if attempts >= 50:
            self._notify("导出临时文件仍被系统占用，请稍后清理：" + request["stage_dir"], True)
        else:
            remaining.append((request, attempts + 1))
    self._export_cleanup_pending = remaining
    if remaining:
        self._export_cleanup_timer.start()


def queue(self, target, jpeg_quality, *, mask=None, output='photo', layers_snapshot=None):
    if self._export_request or self._export_aborting or self._export_process.state() != QProcess.NotRunning:
        self._notify("上一轮导出正在结束，请稍后重试", True)
        return False
    try:
        snapshot = deepcopy(self._layers if layers_snapshot is None else layers_snapshot)
    except MemoryError:
        self._notify("图层数据过大，无法准备导出", True)
        return False
    try:
        folder = Path(tempfile.mkdtemp(prefix=".iphoto-export-", dir=target.parent))
    except OSError as exc:
        self._notify("无法准备导出目录：" + str(exc), True)
        return False
    self._export_serial += 1
    self._export_request = {
        "id": self._export_serial,
        "op": "export",
        "path": str(target),
        "stage_dir": str(folder),
        "stage_path": str(folder / target.name),
        "source_path": self._path,
        "source_sha": self._sha,
        "layers": snapshot,
        "jpeg_quality": jpeg_quality,
        **({'mask':deepcopy(mask),'output':output} if mask is not None else {}),
    }
    self._export_buffer = b""
    self._export_line_offset = 0
    self._export_phase = 0
    self._export_final_seen = False
    self._status = PHASES[0]
    interpreter = Path(sys.executable)
    if interpreter.name.lower() == "pythonw.exe":
        interpreter = interpreter.with_name("python.exe")
    self._export_process.start(str(interpreter), [str(ROOT / "run.py"), "--export-worker"])
    self.changed.emit()
    return self._export_request is not None


def started(self):
    if not self._export_request:
        self._export_process.kill()
        return
    self._export_process.write(
        (json.dumps(self._export_request, ensure_ascii=False) + "\n").encode("utf-8")
    )


def read(self):
    data = bytes(self._export_process.readAllStandardOutput())
    request = self._export_request
    if not request or self._export_aborting:
        return
    self._export_buffer += data
    if len(self._export_buffer) > 1024 * 1024:
        cancel(self, "导出进程返回的数据过大，导出已停止")
        return
    while True:
        end = self._export_buffer.find(b"\n", self._export_line_offset)
        if end < 0:
            break
        line = self._export_buffer[self._export_line_offset:end]
        self._export_line_offset = end + 1
        try:
            response = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue  # Final validation still checks the complete bounded output.
        if not isinstance(response, dict):
            continue
        if response.get("type") != "progress":
            self._export_final_seen = True
            continue
        phase = response.get("phase")
        if (not self._export_final_seen and type(response.get("id")) is int
                and response["id"] == request["id"] and type(phase) is int and 1 <= phase <= 3):
            _phase(self, phase)


def stderr(self):
    data = bytes(self._export_process.readAllStandardError()).decode("utf-8", "replace")
    if data:
        print("[export worker] " + data[-2000:], file=sys.stderr)


def cancel(self, message="导出已取消，原照片和目标文件均未更改"):
    request = self._export_request
    if request is None:
        return
    self._export_request = None
    self._export_cancelled = request
    self._export_buffer = b""
    self._export_aborting = self._export_process.state() != QProcess.NotRunning
    if self._export_aborting:
        self._export_process.kill()
    else:
        _cleanup_or_retry(self, request)
        self._export_cancelled = None
    if not self._closing:
        self._notify(message)
        self.exportCompleted.emit(request["path"], message)
    self.changed.emit()


def finished(self, code, status):
    read(self)
    if self._export_aborting:
        self._export_aborting = False
        request = self._export_cancelled
        self._export_cancelled = None
        _cleanup_or_retry(self, request)
        self.changed.emit()
        return
    request = self._export_request
    if request is None:
        return
    try:
        if status != QProcess.NormalExit or code != 0:
            raise ValueError("导出进程异常退出，目标文件未写入")
        responses = [json.loads(line) for line in self._export_buffer.splitlines()
                     if line.lstrip().startswith(b"{")]
        results = [value for value in responses if value.get("type") != "progress"]
        if len(results) != 1:
            raise ValueError("导出进程没有返回有效结果")
        response = results[0]
        if type(response.get("id")) is not int or response["id"] != request["id"]:
            raise ValueError("导出结果与当前任务不匹配")
        if response.get("ok") is not True:
            raise ValueError(response.get("error", "导出失败"))
        if request["source_sha"] != self._sha:
            raise ValueError("源照片已切换，旧导出结果已丢弃")
        if Path(response["result"]["stage_path"]).resolve() != Path(request["stage_path"]).resolve():
            raise ValueError("导出结果路径与当前任务不匹配")
        target = validate_export_destination(self._path, request["path"])
        _phase(self, 4)
        publish_staged_file(response["result"]["stage_path"], target)
        result = response["result"]
        message = f"已按原图尺寸导出 {result['width']} × {result['height']}：{target}"
        self._export_request = None
        self._notify(message)
        self.exportCompleted.emit(str(target), "")
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        message = str(exc)
        self._export_request = None
        self._notify(message, True)
        self.exportCompleted.emit(request["path"], message)
    finally:
        self._export_request = None
        self._export_buffer = b""
        _cleanup_or_retry(self, request)
        self.changed.emit()


def error(self, reason):
    if reason != QProcess.FailedToStart:
        return
    cancelled = self._export_cancelled
    self._export_cancelled = None
    request = self._export_request
    self._export_request = None
    self._export_aborting = False
    self._export_buffer = b""
    if cancelled:
        _cleanup_or_retry(self, cancelled)
    if request:
        _cleanup_or_retry(self, request)
        if not self._closing:
            message = "导出进程未能启动，目标文件未写入"
            self._notify(message, True)
            self.exportCompleted.emit(request["path"], message)
    self.changed.emit()


def shutdown(self):
    if self._export_request:
        cancel(self)
    if self._export_process.state() != QProcess.NotRunning:
        self._export_process.kill()
        self._export_process.waitForFinished(1500)
    if self._export_cancelled:
        _cleanup_or_retry(self, self._export_cancelled)
        self._export_cancelled = None
    self._export_cleanup_timer.stop()
    for _ in range(40):
        if not self._export_cleanup_pending:
            break
        retry_cleanup(self)
        if self._export_cleanup_pending:
            time.sleep(0.05)
