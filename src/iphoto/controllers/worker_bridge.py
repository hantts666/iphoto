"""Worker Bridge actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
from pathlib import Path
import json
import sys
from PySide6.QtCore import QProcess, QUrl
from ..engine import PRESETS, Recipe
from ..paths import ROOT


def _request(self, op, **data):
    if self.process.state() == QProcess.NotRunning:
        self._notify("本地引擎未运行，请保存项目后重新打开 iPhoto", True)
        return
    self._serial += 1
    request = {"id": self._serial, "op": op, "generation": self._generation, **data}
    if op == "render":
        self._pending_render = request
    else:
        self._queue.append(request)
    self._pump()


def _pump(self):
    if self._closing or self._active or self.process.state() != QProcess.Running:
        return
    if self._queue:
        self._active = self._queue.popleft()
    elif self._pending_render:
        self._active, self._pending_render = self._pending_render, None
    else:
        self.changed.emit()
        return
    self.process.write(
        (json.dumps(self._active, ensure_ascii=False) + "\n").encode("utf-8")
    )
    self.changed.emit()


def _stderr(self):
    data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
    if data:
        print(data[-2000:], file=sys.stderr)


def _read(self):
    self._buffer += bytes(self.process.readAllStandardOutput())
    while b"\n" in self._buffer:
        line, self._buffer = self._buffer.split(b"\n", 1)
        try:
            response = json.loads(line)
            active = self._active
            if not active or response["id"] != active["id"]:
                continue
            self._active = None
            op = response["op"]
            if not response["ok"]:
                if op in ("segment", "matte"):
                    self._status = "选区处理失败，原选区保留；可修正目标或减小边缘范围"
                    self._message(
                        "error",
                        response["error"],
                        state="failed",
                        origin=active.get("context", {}).get("origin"),
                    )
                self._pending_project = None if op == "open" else self._pending_project
                self._notify(response["error"], True)
                if op == "open" and self.hasImage:
                    self._schedule_render()
            elif op == "open":
                value = response["result"]
                self._path, self._name, self._sha = (
                    value["path"],
                    value["name"],
                    value["sha256"],
                )
                self._sample = Path(self._path) == (ROOT / "assets/lake.jpg")
                self._width, self._height = value["width"], value["height"]
                self._viewport.setSource(self._width, self._height)
                self._original = QUrl.fromLocalFile(value["original"]).toString()
                self._preview = self._original
                self._histogram = value["histogram"]
                self._recipe = Recipe().to_dict()
                self._locked = set()
                self._history = [(dict(self._recipe), set())]
                self._cursor = 0
                self._summary = "原图已就绪。输入你的要求，或先试试左侧的调色预设。"
                self._status = value["warning"] or "原图已保留 · 编辑不会覆盖原文件"
                if self._sample:
                    self._recipe = Recipe.from_dict(PRESETS["natural"]).to_dict()
                    self._summary = "示例已应用「自然通透」预设：轻提暗部、收敛高光，保留湖水与山林的层次。拖动分割线查看变化。"
                self._generation += 1
                self.imageOpened.emit()
                self._schedule_render()
            elif op == "render":
                if response["generation"] == self._generation:
                    value = response["result"]
                    self._preview = QUrl.fromLocalFile(value["preview"]).toString()
                    self._histogram = value["histogram"]
                    self._elapsed = value["elapsed_ms"]
                    if "mask" in value:
                        self._mask_url = QUrl.fromLocalFile(value["mask"]).toString()
            elif op == "interpret":
                if response["generation"] == self._generation:
                    self._recipe = Recipe.from_dict(
                        response["result"]["recipe"]
                    ).to_dict()
                    self._summary = response["result"]["summary"]
                    self._commit()
                    self._change()
                    self._status = "已应用本地规则 · 未调用云端 AI"
                    self._local_result(self._summary)
                else:
                    self._notify("参数已改变，这次过期的建议未应用", True)
            elif op == "segment":
                if response["generation"] == self._generation and not active.get(
                    "cancelled"
                ):
                    from .pixel_selections import complete

                    complete(self, response["result"], active["context"])
                else:
                    self._status = "本次像素选区已取消或过期，原选区保留"
                    self._notify("本次像素选区已取消或过期，未改变当前选区")
            elif op == "matte":
                if response["generation"] == self._generation and not active.get(
                    "cancelled"
                ):
                    from .matting import complete

                    complete(self, response["result"])
                else:
                    self._status = "本次边缘细化已取消或过期，原选区保留"
                    self._notify("本次边缘细化已取消或过期，未改变当前选区")
            elif op == "selection":
                if response["generation"] == self._generation:
                    value = response["result"]
                    if self._region_candidate is not None:
                        self._region_candidate["layers"][self._region_index]["mask"] = (
                            value["mask"]
                        )
                        self._mark_dirty()
                        self._mask_url = ""
                        self._generation += 1
                        self._schedule_render()
                    else:
                        self._set_candidate(value["mask"])
                    warnings = value["quality"].get("warnings", [])
                    self._notify(
                        "；".join(warnings)
                        if warnings
                        else "选区已生成，请检查边缘后输出到图层"
                    )
            elif op == "export":
                value = response["result"]
                self._notify(f"已按原图尺寸导出 {value['width']} × {value['height']}：" + value["path"])
            self.changed.emit()
            self._pump()
        except Exception as exc:
            self._active = None
            self._notify("处理响应失败：" + str(exc), True)
            self._pump()


def _process_error(self, _):
    if not self._closing:
        if self.process.state() == QProcess.NotRunning:
            self._active = None
            self._queue.clear()
            self._pending_render = None
            self._timer.stop()
        self._notify("本地引擎启动失败，请重新打开 iPhoto", True)


def _finished(self, *_):
    self._active = None
    self._queue.clear()
    self._pending_render = None
    if not self._closing:
        self._ai.cancel()
        self._notify("本地引擎已退出，请重新打开应用；原照片未被更改", True)


def _schedule_render(self):
    if self.hasImage:
        self._sync_layer()
        layers = deepcopy(self._layers)
        mask = deepcopy(self._candidate or self._layer()["mask"])
        if self._candidate is not None and self._mask_view == "adjustment":
            # Preview a draft with the active layer's current adjustments.
            # Never mutate its real mask or the history until confirmation.
            for layer in layers:
                if layer["id"] == self._selected:
                    layer["mask"] = deepcopy(self._candidate)
                    break
        if self._region_candidate:
            layers += deepcopy(self._region_candidate["layers"])
            mask = deepcopy(
                self._region_candidate["layers"][self._region_index]["mask"]
            )
        self._request("render", layers=layers, mask=mask, mask_view=self._mask_view)
