"""Cancellable, one-shot alpha matting process for foreground selection work."""

import json
from pathlib import Path
import sys

from PySide6.QtCore import QProcess

from ..paths import ROOT


def queue(self, request):
    self._matte_pending = {
        **request, "source_path": self._path, "source_sha": self._sha,
    }
    pump(self)
    self.changed.emit()


def pump(self):
    if self._closing or self._matte_aborting or self._matte_active or not self._matte_pending:
        return
    state = self._matte_process.state()
    if state == QProcess.NotRunning:
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self._matte_fresh = True
        self._matte_process.start(
            str(interpreter), [str(ROOT / "run.py"), "--matte-worker"]
        )
        return
    if state != QProcess.Running or not self._matte_fresh:
        return
    self._matte_active, self._matte_pending = self._matte_pending, None
    self._matte_fresh = False
    self._matte_process.write(
        (json.dumps(self._matte_active, ensure_ascii=False) + "\n").encode("utf-8")
    )
    self.changed.emit()


def read(self):
    self._matte_buffer += bytes(self._matte_process.readAllStandardOutput())
    if len(self._matte_buffer) > 32 * 1024 * 1024:
        stop(self)
        self._notify("边缘细化结果过大，原选区保留", True)
        return
    while b"\n" in self._matte_buffer:
        line, self._matte_buffer = self._matte_buffer.split(b"\n", 1)
        if not line.lstrip().startswith(b"{"):
            print("[matte stdout] " + line.decode("utf-8", "replace")[:300], file=sys.stderr)
            continue
        active = self._matte_active
        if active is None:
            continue
        try:
            response = json.loads(line)
            if response.get("id") != active["id"]:
                continue
            self._matte_active = None
            current = (
                active["generation"] == self._generation
                and active["source_sha"] == self._sha
            )
            if current and response.get("ok"):
                from .matting import complete

                complete(self, response["result"])
            elif current:
                self._status = "选区处理失败，原选区保留；可减小边缘范围后重试"
                self._notify(response.get("error", "边缘细化失败"), True)
            self.changed.emit()
        except Exception as exc:
            self._matte_active = None
            self._notify("处理边缘细化结果失败：" + str(exc), True)


def stderr(self):
    data = bytes(self._matte_process.readAllStandardError()).decode("utf-8", "replace")
    if data:
        print("[matte worker] " + data[-2000:], file=sys.stderr)


def stop(self):
    self._matte_active = self._matte_pending = None
    self._matte_buffer = b""
    self._matte_fresh = False
    if self._matte_process.state() != QProcess.NotRunning:
        self._matte_aborting = True
        self._matte_process.kill()
    else:
        self._matte_aborting = False
    self.changed.emit()


def finished(self, *_):
    if not self._matte_aborting:
        read(self)
    aborted = self._matte_aborting
    had_active = self._matte_active is not None
    self._matte_aborting = False
    self._matte_active = None
    self._matte_buffer = b""
    self._matte_fresh = False
    if had_active and not aborted and not self._closing:
        self._notify("边缘细化进程已退出，原选区保留；可重试", True)
    self.changed.emit()
    pump(self)


def error(self, reason):
    if reason != QProcess.FailedToStart:
        return
    aborted = self._matte_aborting
    self._matte_aborting = False
    self._matte_active = None
    if not aborted:
        self._matte_pending = None
    self._matte_buffer = b""
    self._matte_fresh = False
    if not aborted and not self._closing:
        self._notify("边缘细化进程未能启动，原选区保留", True)
    elif aborted:
        pump(self)
