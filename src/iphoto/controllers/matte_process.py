"""Cancellable, one-shot alpha matting process for foreground selection work."""

import json
from pathlib import Path
import sys

from PySide6.QtCore import QProcess

from ..paths import ROOT


def queue(self, request):
    self._matte_pending = {
        **request, "source_path": self._path, "source_sha": self._sha,
        **({"layer_id": self._selected, "target_id": self._selection_target_id} if "stroke" in request or "channel_token" in request else {}),
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
            if not isinstance(response, dict):
                continue
            if response.get("id") != active["id"]:
                continue
            current = (
                active["generation"] == self._generation
                and active["source_sha"] == self._sha
                and response.get("generation") == active["generation"]
                and response.get("op") == "matte"
                and not self._closing and not self._matte_aborting
                and ("stroke" not in active and "channel_token" not in active or (active["layer_id"] == self._selected
                     and active["target_id"] == self._selection_target_id and active["mask"] == self._candidate
                     and not self.hasRegionDraft))
            )
            if "progress" in response:
                if current and active.get("method") in ("neural", "channel", "correction"):
                    _progress(self, active, response["progress"])
                continue
            self._matte_active = None
            if not current and 'auto_token' in active:
                from .channel_auto import discard
                discard(self, active['auto_token'])
            if current and response.get("ok"):
                if 'auto_token' in active:
                    from .channel_auto import complete as complete_channel
                    complete_channel(self, response['result'], active['auto_token'])
                    self.changed.emit()
                    continue
                from .matting import complete

                complete(self, response["result"], points=active.get("points", []) if active.get("method") == "neural" else None)
            elif current:
                self._status = "选区处理失败，原选区保留；可减小边缘范围后重试"
                self._notify(response.get("error", "边缘细化失败"), True)
            self.changed.emit()
        except Exception as exc:
            self._matte_active = None
            self._notify("处理边缘细化结果失败：" + str(exc), True)


def _progress(self, active, progress):
    """Progress is informational; only the final reply may publish a mask."""
    if not isinstance(progress, dict):
        return
    if progress == {"phase": "prepare"} and not active.get("detail_tile"):
        self._status = ("正在读取原图并计算通道透明度…可随时取消"
                        if active.get('method')=='channel' and not active.get('channel_options',{}).get('ai')
                        else "正在读取原图并准备局部透明细化…可随时取消" if "stroke" in active else "正在读取原图并准备 AI 边缘模型…可随时取消")
    elif progress == {'phase':'polish'} and active.get('method') == 'channel':
        self._status = '正在按原像素颜色细化发丝与透明细纹…可随时取消'
    elif progress == {'phase':'semantic'} and active.get('method') == 'correction':
        active.pop('detail_tile',None)
        active.pop('detail_tiles',None)
        self._status = 'AI 正在按效果检查的保留 / 排除点修正局部误选…可随时取消'
    elif progress.get("phase") == "details":
        tile, tiles = progress.get("tile"), progress.get("tiles")
        if (type(tile) is not int or type(tiles) is not int or not 1 <= tile <= tiles <= 128
                or tile <= active.get("detail_tile", 0)
                or tiles != active.get("detail_tiles", tiles)):
            return
        active["detail_tile"], active["detail_tiles"] = tile, tiles
        self._status = (f"AI 正在细化涂抹区域 {tile}/{tiles} 块…其他范围保留，可随时取消" if "stroke" in active
                        else f"正在用 AI 细化原图边缘 {tile}/{tiles} 块…保留提示点，可随时取消")
    else:
        return
    self.changed.emit()


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
