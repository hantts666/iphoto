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
        return False
    self._serial += 1
    request = {"id": self._serial, "op": op, "generation": self._generation, **data}
    if op != "segment" or request.get("priority") != "low":
        self._status_epoch += 1
    if op == "render":
        from .detail_tiles import invalidate

        invalidate(self)
    elif op == "segment" and request.get("priority") != "low":
        from .detail_tiles import release

        release(self)
    elif op in {"export", "matte", "selection"}:
        self._stop_detail()
    if op == "matte":
        from .matte_process import queue

        queue(self, request)
        return
    if op == "segment":
        self._pixel_queue.append(request)
        if request.get("priority") != "low" and self._pixel_active and self._pixel_active.get("priority") == "low":
            self._pixel_queue.append(self._pixel_active)
            self._stop_pixel()
        self._pump_pixel()
        self.changed.emit()
        return
    if op == "render":
        self._pending_render = request
    else:
        self._queue.append(request)
    already_active = self._active is not None
    self._pump()
    if op == "render":
        # At high zoom, the visible source tile responds to the same snapshot
        # immediately; ordinary progress signals must not debounce it away.
        self.requestDetail()
    if already_active and op != "render":
        # Queuing foreground work behind an active background task changes
        # busy/task state even though the worker cannot dispatch it yet.
        self.changed.emit()


def _warm_running(self):
    process = getattr(self, "_warm_process", None)
    return process is not None and process.state() != QProcess.NotRunning


def _start_warm(self):
    if not self.hasImage or self._warm_ready_sha == self._sha:
        return
    if _warm_running(self):
        if self._warm_sha != self._sha:
            _stop_warm(self)
        return
    proxy_path = QUrl(self._original).toLocalFile()
    if not Path(proxy_path).is_file():
        return
    from .detail_tiles import release

    release(self)
    interpreter = Path(sys.executable)
    if interpreter.name.lower() == "pythonw.exe":
        interpreter = interpreter.with_name("python.exe")
    self._warm_sha = self._sha
    self._warm_abandoned = False
    self._warm_process.start(
        str(interpreter), [str(ROOT / "run.py"), "--warm", proxy_path]
    )
    self.changed.emit()


def _stop_warm(self):
    self._warm_abandoned = True
    self._warm_sha = ""
    if _warm_running(self):
        self._warm_process.kill()


def _warm_finished(self, code, status):
    if (
        not self._warm_abandoned
        and code == 0
        and status == QProcess.NormalExit
        and self._warm_sha == self._sha
    ):
        self._warm_ready_sha = self._sha
    self._warm_sha = ""
    self.changed.emit()
    self._pump()
    self._pump_pixel()


def _warm_error(self, _):
    if self._warm_process.state() == QProcess.NotRunning:
        self._warm_sha = ""
        self.changed.emit()
        self._pump()
        self._pump_pixel()


def _pump_pixel(self):
    if self._closing or self._pixel_aborting or self._pixel_active:
        return
    self._pixel_queue = type(self._pixel_queue)(
        request for request in self._pixel_queue
        if not (
            request.get("priority") == "low"
            and request.get("context", {}).get("purpose") == "precache"
            and (
                request["context"].get("source_sha") != self._sha
                or request["context"].get("scene_revision") != self._scene.revision
                or all(job["id"] in self._scene.precise for job in request.get("jobs", []))
            )
        )
    )
    if not self._pixel_queue:
        return
    if self._pixel_process.state() == QProcess.NotRunning:
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self._pixel_process.start(
            str(interpreter), [str(ROOT / "run.py"), "--pixel-worker"]
        )
        return
    if self._pixel_process.state() != QProcess.Running:
        return
    next_request = next(
        (request for request in self._pixel_queue if request.get("priority") != "low"),
        self._pixel_queue[0],
    )
    if _warm_running(self) and not (next_request.get("composition") and not next_request.get("jobs")):
        return
    self._pixel_active = next_request
    self._pixel_queue.remove(self._pixel_active)
    request = {
        **self._pixel_active,
        "proxy_path": QUrl(self._original).toLocalFile(),
        "source_path": self._path,
        "source_sha": self._sha,
    }
    self._pixel_process.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
    self.changed.emit()


def _pixel_stderr(self):
    data = bytes(self._pixel_process.readAllStandardError()).decode(
        "utf-8", errors="replace"
    )
    if data:
        print("[pixel worker] " + data[-2000:], file=sys.stderr)


def _pixel_read(self):
    self._pixel_buffer += bytes(self._pixel_process.readAllStandardOutput())
    while b"\n" in self._pixel_buffer:
        line, self._pixel_buffer = self._pixel_buffer.split(b"\n", 1)
        if not line.lstrip().startswith(b"{"):
            print("[pixel stdout] " + line.decode("utf-8", "replace")[:300], file=sys.stderr)
            continue
        active = None
        try:
            response = json.loads(line)
            active = self._pixel_active
            if not active or response.get("id") != active["id"]:
                continue
            if "progress" in response:
                progress = response["progress"]
                if (isinstance(progress, dict) and progress.get("kind") == "body"
                        and type(progress.get("part")) is int and type(progress.get("total")) is int
                        and 1 <= progress["part"] <= progress["total"] <= 4
                        and response.get("generation") == self._generation
                        and not active.get("cancelled") and active.get("priority") != "low"
                        and any(job.get("mask_target") == "body_skin" for job in active.get("jobs", []))):
                    self._status = f"正在按原图细节分割身体部位 {progress['part']}/{progress['total']}；全部完成后建立图层…"
                    self.changed.emit()
                elif (isinstance(progress, dict) and progress.get("kind") == "object"
                        and progress.get("phase") in ("segment", "edges", "local_edges")
                        and type(progress.get("part")) is int and type(progress.get("total")) is int
                        and 1 <= progress["part"] <= progress["total"] <= 16
                        and progress["total"] == len(active.get("jobs", []))
                        and active["jobs"][progress["part"] - 1].get("mask_target", "object") == "object"
                        and response.get("generation") == self._generation
                        and active.get("generation") == self._generation
                        and not active.get("cancelled") and active.get("priority") != "low"):
                    phase = {"segment": "识别对象范围", "edges": "按原图恢复边缘透明度",
                             "local_edges": "按原图颜色恢复边缘"}[progress["phase"]]
                    self._status = f"正在{phase} {progress['part']}/{progress['total']}…可随时取消"
                    self.changed.emit()
                # Progress is not a result: it cannot release the active job,
                # publish a partial layer, or mark whole-photo SAM as ready.
                continue
            self._pixel_active = None
            context = active.get("context", {})
            current_scene_cache = (
                context.get("purpose") == "precache"
                and context.get("source_sha") == self._sha
                and context.get("scene_revision") == self._scene.revision
            )
            if not response["ok"] and context.get("purpose") == "precache":
                failed_ids = [job["id"] for job in active.get("jobs", [])]
                failed_ids += [
                    job["id"] for request in self._pixel_queue
                    if request.get("priority") == "low"
                    and request.get("context", {}).get("purpose") == "precache"
                    and request["context"].get("source_sha") == context.get("source_sha")
                    and request["context"].get("scene_revision") == context.get("scene_revision")
                    for job in request.get("jobs", [])
                ]
                self._pixel_queue = type(self._pixel_queue)(
                    request for request in self._pixel_queue
                    if not (
                        request.get("priority") == "low"
                        and request.get("context", {}).get("purpose") == "precache"
                        and request["context"].get("source_sha") == context.get("source_sha")
                        and request["context"].get("scene_revision") == context.get("scene_revision")
                    )
                )
                if current_scene_cache:
                    self._scene.mark_pixel_status(failed_ids, "unavailable")
                    if context.get("status_epoch") == self._status_epoch and self._candidate is None and not self._ai.busy:
                        self._status = "元素精确预览未完成，可继续手动选择或稍后重试分析"
                        self._notify(self._status + "：" + response["error"], background=True)
            elif not response["ok"]:
                error = response["error"]
                if (
                    context.get("purpose") == "points"
                    and context.get("points")
                    and ("可靠目标" in error or "满足提示点" in error)
                ):
                    from .pixel_selections import keep_points

                    keep_points(
                        self,
                        context["points"],
                        "这次点击没有命中清晰目标：提示点已保留（共 "
                        + str(len(context["points"]))
                        + " 个）。继续点目标内部、Alt 点排除，或改用框选 / 画笔补充。",
                    )
                else:
                    self._status = "选区处理失败，原选区保留；可修正目标或减小边缘范围"
                    self._message(
                        "error", error, state="failed", origin=context.get("origin")
                    )
                    self._notify(error, True)
            elif (response["generation"] == self._generation or current_scene_cache) and not active.get("cancelled"):
                from .pixel_selections import complete

                if any(job.get("mask_target", "object") == "object" for job in active.get("jobs", [])) or context.get("purpose") == "warm":
                    self._warm_ready_sha = self._sha
                complete(self, response["result"], active["context"])
            elif context.get("purpose") != "precache":
                self._status = "本次像素选区已取消或过期，原选区保留"
                self._notify("本次像素选区已取消或过期，未改变当前选区")
            self.changed.emit()
            self._pump_pixel()
        except Exception as exc:
            self._pixel_active = None
            if active and active.get("context", {}).get("auto_apply"):
                from .pixel_selections import failed_result

                failed_result(self, active["context"], exc)
            else:
                self._notify("处理像素结果失败：" + str(exc), True)
            self._pump_pixel()


def _stop_pixel(self):
    self._pixel_aborting = True
    self._pixel_active = None
    self._pixel_buffer = b""
    if self._pixel_process.state() != QProcess.NotRunning:
        self._pixel_process.kill()
    else:
        self._pixel_aborting = False
    self.changed.emit()


def _drop_pixel_requests(self):
    requests = ([self._pixel_active] if self._pixel_active else []) + list(self._pixel_queue)
    failed_ids = [
        job["id"] for request in requests
        if request.get("priority") == "low"
        and request.get("context", {}).get("purpose") == "precache"
        and request["context"].get("source_sha") == self._sha
        and request["context"].get("scene_revision") == self._scene.revision
        for job in request.get("jobs", [])
    ]
    if failed_ids:
        self._scene.mark_pixel_status(failed_ids, "unavailable")
    self._pixel_active = None
    self._pixel_queue.clear()


def _pixel_finished(self, _code, _status):
    aborted = self._pixel_aborting
    had_pending = self._pixel_active is not None or bool(self._pixel_queue)
    self._pixel_aborting = False
    self._pixel_buffer = b""
    if not self._closing and not aborted and had_pending:
        _drop_pixel_requests(self)
        self._notify("像素计算进程已退出，当前选区保持不变；可重试点选", True)
    else:
        self._pixel_active = None
    self.changed.emit()
    self._pump_pixel()


def _pixel_error(self, _):
    if self._pixel_process.state() == QProcess.NotRunning and not self._pixel_aborting:
        _drop_pixel_requests(self)
        self._notify("像素计算进程未能启动；请检查本地模型配置", True)


def _pump(self):
    if self._closing or self._active or self.process.state() != QProcess.Running:
        return
    self._queue = type(self._queue)(
        request
        for request in self._queue
        if not (
            request.get("priority") == "low"
            and request.get("context", {}).get("purpose") == "precache"
            and (
                request["context"].get("source_sha") != self._sha
                or request["context"].get("scene_revision") != self._scene.revision
                or all(
                    job["id"] in self._scene.precise for job in request.get("jobs", [])
                )
            )
        )
    )
    # A new preview is interactive too. Drain it before the next background
    # object, but keep explicit user actions ahead of previews.
    warming = _warm_running(self)
    def runnable(request):
        return not (warming and request["op"] == "segment")

    high = next(
        (r for r in self._queue if r.get("priority") != "low" and runnable(r)),
        None,
    )
    next_job = next((r for r in self._queue if runnable(r)), None)
    if high is not None:
        self._active = high
        self._queue.remove(high)
    elif self._pending_render:
        self._active, self._pending_render = self._pending_render, None
    elif next_job is not None:
        self._active = next_job
        self._queue.remove(self._active)
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
        if not line.lstrip().startswith(b"{"):
            # Library noise on worker stdout (e.g. "[W:onnxruntime:...]") must
            # not be parsed as a protocol response.
            print(
                "[worker stdout] " + line.decode("utf-8", "replace")[:300],
                file=sys.stderr,
            )
            continue
        try:
            response = json.loads(line)
            active = self._active
            if not active or response["id"] != active["id"]:
                continue
            self._active = None
            op = response["op"]
            if op in ("repair_crop", "object_crop") and active.get("cancelled"):
                self.changed.emit()
                self._pump()
                continue
            if not response["ok"]:
                context = active.get("context", {})
                if op == "segment" and context.get("purpose") in ("precache", "warm"):
                    if context["purpose"] == "precache":
                        failed_ids = [job["id"] for job in active.get("jobs", [])]
                        failed_ids += [
                            job["id"]
                            for request in self._queue
                            if request.get("priority") == "low"
                            and request.get("context", {}).get("purpose") == "precache"
                            and request["context"].get("source_sha") == context.get("source_sha")
                            and request["context"].get("scene_revision") == context.get("scene_revision")
                            for job in request.get("jobs", [])
                        ]
                        self._queue = type(self._queue)(
                            request
                            for request in self._queue
                            if not (
                                request.get("priority") == "low"
                                and request.get("context", {}).get("purpose") == "precache"
                                and request["context"].get("source_sha") == context.get("source_sha")
                                and request["context"].get("scene_revision") == context.get("scene_revision")
                            )
                        )
                        if (
                            context.get("source_sha") == self._sha
                            and context.get("scene_revision") == self._scene.revision
                        ):
                            self._scene.mark_pixel_status(failed_ids, "unavailable")
                        self._status = "元素精确预览未完成，可继续手动选择或稍后重试分析"
                        self._notify(self._status + "：" + response["error"])
                    elif (
                        response["generation"] == self._generation
                        and context.get("source_sha") == self._sha
                        and not self.busy
                        and not self._queue
                        and not self._pending_render
                    ):
                        self._status = "像素预热未完成，首次点选可能稍慢"
                        self._notify(self._status + "：" + response["error"])
                elif (
                    op == "segment"
                    and context.get("purpose") == "points"
                    and context.get("points")
                    and (
                        "可靠目标" in response["error"]
                        or "满足提示点" in response["error"]
                    )
                ):
                    from .pixel_selections import keep_points

                    keep_points(
                        self,
                        context["points"],
                        "这次点击没有命中清晰目标：提示点已保留（共 "
                        + str(len(context["points"]))
                        + " 个）。继续点目标内部、Alt 点排除，或改用框选 / 画笔补充。",
                    )
                else:
                    if op in ("segment", "matte"):
                        self._status = "选区处理失败，原选区保留；可修正目标或减小边缘范围"
                        self._message(
                            "error",
                            response["error"],
                            state="failed",
                            origin=active.get("context", {}).get("origin"),
                        )
                    if op == "open":
                        from .session import project_open_failed

                        project_open_failed(self, response["error"])
                    self._notify(response["error"], True)
                    if op == "export":
                        self.exportCompleted.emit(active["path"], response["error"])
                if op == "open" and self.hasImage:
                    self._schedule_render()
            elif op == "open":
                self._last_render_metrics = {}
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
                self._preview_generation = -1
                self._histogram = value["histogram"]
                self._stats = value.get("stats")
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
                from .preview_updates import accepts

                if (accepts(self, response["generation"])
                        and response["generation"] >= self._preview_generation):
                    value = response["result"]
                    self._preview = QUrl.fromLocalFile(value["preview"]).toString()
                    self._preview_generation = response["generation"]
                    self._histogram = value["histogram"]
                    self._stats = value.get("stats", self._stats)
                    self._elapsed = value["elapsed_ms"]
                    self._last_render_metrics = {
                        "generation": response["generation"],
                        "worker_elapsed_ms": value["elapsed_ms"],
                        "stage_ms": value.get("stage_ms", {}),
                        "cache_hit": value.get("cache_hit", False),
                    }
                    if "mask" in value and response["generation"] == self._generation:
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
                context = active.get("context", {})
                current_scene_cache = (
                    context.get("purpose") == "precache"
                    and context.get("source_sha") == self._sha
                    and context.get("scene_revision") == self._scene.revision
                )
                if (
                    (response["generation"] == self._generation or current_scene_cache)
                    and not active.get("cancelled")
                ):
                    from .pixel_selections import complete

                    complete(self, response["result"], active["context"])
                elif context.get("purpose") != "warm":
                    self._status = "本次像素选区已取消或过期，原选区保留"
                    self._notify("本次像素选区已取消或过期，未改变当前选区")
            elif op == "repair_crop":
                from .conversation import _repair_crop_ready

                _repair_crop_ready(self, response["result"], active["context"], response["generation"])
            elif op == "object_crop":
                from .object_grounding import crop_ready

                crop_ready(self, response["result"], active["context"], response["generation"])
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
                        else "选区已生成，请检查边缘后输出到图层",
                        scope="draft",
                    )
            elif op == "export":
                value = response["result"]
                self._notify(f"已按原图尺寸导出 {value['width']} × {value['height']}：" + value["path"])
                self.exportCompleted.emit(value["path"], "")
            self.changed.emit()
            self._pump()
        except Exception as exc:
            self._active = None
            self._notify("处理响应失败：" + str(exc), True)
            if active and active["op"] == "export":
                self.exportCompleted.emit(active["path"], "处理响应失败：" + str(exc))
            self._pump()


def _process_error(self, _):
    if not self._closing:
        if self.process.state() == QProcess.NotRunning:
            exports = [r for r in ([self._active] if self._active else []) + list(self._queue) if r["op"] == "export"]
            self._active = None
            self._queue.clear()
            self._pending_render = None
            self._timer.stop()
            for request in exports:
                self.exportCompleted.emit(request["path"], "本地引擎已退出，导出没有完成")
        self._notify("本地引擎启动失败，请重新打开 iPhoto", True)


def _finished(self, *_):
    exports = [r for r in ([self._active] if self._active else []) + list(self._queue) if r["op"] == "export"]
    self._active = None
    self._queue.clear()
    self._pending_render = None
    if not self._closing:
        self._ai.cancel()
        self._notify("本地引擎已退出，请重新打开应用；原照片未被更改", True)
        for request in exports:
            self.exportCompleted.emit(request["path"], "本地引擎已退出，导出没有完成")


def preview_payload(self):
    """One snapshot for the proxy, source detail and selection overlay."""
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
    return {"layers": layers, "mask": mask, "mask_view": self._mask_view}


def _schedule_render(self):
    if self.hasImage:
        self._request("render", **preview_payload(self))
