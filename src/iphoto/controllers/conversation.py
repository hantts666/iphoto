"""Conversation actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import re
import base64
from io import BytesIO
from uuid import uuid4
from PySide6.QtCore import QProcess, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from ..engine import Recipe
from ..document import raster_mask, now, MAX_LAYERS
from ..paths import path_from_url
from ..storage import atomic_output
from . import pixel_selections


def _safe_text(self, text):
    return re.sub(r"\bsk-[A-Za-z0-9_.\\-]{8,}", "[Key 已隐藏]", str(text))


def _document_signature(self):
    self._sync_layer()
    layers = [
        {
            k: deepcopy(layer[k])
            for k in ("id", "visible", "opacity", "recipe", "mask", "kind", "parent_id")
        }
        for layer in self._layers
    ]
    for layer in layers:
        layer["mask"].pop("label", None)
    encoded = json.dumps(
        [self._sha, self._selected, layers], sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _message(self, role, text, mode="", state="", recipe=None, origin=None):
    pending = origin if origin is not None else self._pending_request or {}
    item = {
        "id": uuid4().hex,
        "role": role,
        "text": self._safe_text(text)[:8000],
        "time": now(),
        "model": pending.get("model", ""),
        "mode": mode or pending.get("mode", ""),
        "layer_id": pending.get("layer_id", self._selected),
        "layer_name": pending.get("layer_name", self.activeLayerName),
        "state": state,
    }
    if recipe is not None:
        item["recipe"] = dict(recipe)
        if state == "proposed":
            item["binding"] = pending.get("binding", "")
    self._conversation.append(item)
    self._conversation_model.append(item)
    self._mark_dirty()
    self.conversationChanged.emit()
    self.changed.emit()
    return item


def applyDescription(self, text):
    self.sendMessage(text, "edit")


def _spatial_context(self):
    return {
        "objects": [
            {k: o[k] for k in ("id", "name", "category")}
            for o in (self._scene.catalog or {}).get("objects", [])
        ],
        "existing_layers": [
            {
                "name": l["name"],
                "recipe": l["recipe"],
                "kind": l["kind"],
                "parent_id": l["parent_id"],
            }
            for l in self._layers
        ],
    }


def sendMessage(self, text, mode):
    if (
        not self.hasImage
        or self.busy
        or self.hasRegionDraft
        or (self.hasSelectionDraft and mode not in ("selection", "scene", "targets"))
    ):
        return False
    if self.activeIsGroup and mode in ("edit", "advice"):
        self._notify("请在组内选择或新建调整层，再使用 AI 修图")
        return False
    if self.process.state() != QProcess.Running:
        self._notify("本地引擎未运行，请保存项目后重新打开 iPhoto", True)
        return False
    text = text.strip()
    if not 1 <= len(text) <= 4000 or mode not in (
        "auto",
        "edit",
        "advice",
        "selection",
        "regions",
        "scene",
        "targets",
    ):
        self._notify("请输入 1～4000 字的要求", True)
        return False
    if len(self._conversation) >= 1990:
        self._notify("对话已接近上限，请保存项目并导出对话后开始新项目", True)
        return False
    if self._ai.enabled and not self._ai.ready:
        self.aiSettingsRequested.emit()
        self._notify("请先填写 API Key")
        return False
    if (
        mode == "edit"
        and raster_mask(self._layer()["mask"], (256, 256)).getbbox() is None
    ):
        self._notify("当前图层是空选区，请先绘制或用 AI 生成选区", True)
        return False
    if not self._ai.enabled and mode != "edit":
        self._notify("文字建议与 AI 选区需要在 AI 设置中启用云端模型", True)
        return False
    if mode == "regions" and len(self._layers) > MAX_LAYERS - 4:
        self._notify("自动分区需要预留四个图层位置，请先整理图层", True)
        return False
    self._commit()
    self._pending_request = {
        "mode": mode,
        "text": text,
        "layer_id": self._selected,
        "layer_name": self.activeLayerName,
        "model": self._ai.settings.model if self._ai.enabled else "本地规则",
        "binding": self._document_signature(),
    }
    recent = [
        {
            "role": m["role"],
            "text": m["text"][:1500],
            "layer": m["layer_name"],
            "state": m["state"],
        }
        for m in self._conversation[-8:]
        if m["role"] in ("user", "assistant")
    ]
    if not self._ai.enabled:
        if self._request(
            "interpret",
            text=text,
            recipe=dict(self._recipe),
            locked=sorted(self._locked),
        ) is False:
            return False
        self._message("user", text)
        return True
    self._message("user", text)
    self._status = {
        "auto": "AI 正在判断调整范围并规划图层…",
        "edit": "AI 正在规划当前选区的调整…",
        "advice": "AI 正在分析并撰写建议…",
        "selection": "AI 正在识别选区目标…",
        "regions": "AI 正在判断区域并规划独立调整层…",
        "scene": "AI 正在建立画面元素清单，首次分析可能需要半分钟…",
        "targets": "AI 正在从已识别元素中选择目标…",
    }[mode]
    if mode in ("scene", "targets", "selection", "regions"):
        context = _spatial_context(self)
        self._ai.plan(
            text,
            dict(self._recipe),
            sorted(self._locked),
            QUrl(self._original).toLocalFile(),
            self._generation,
            mode,
            context,
        )
        self.changed.emit()
        return True
    selection = deepcopy(self._candidate or self._layer()["mask"])
    mask_data = None
    if mode != "auto" or self.selectionLabel != "全图":
        mask_size = (
            max(1, round(min(512, 512 * self.imageRatio))),
            max(1, round(min(512, 512 / self.imageRatio))),
        )
        mask_image = raster_mask(selection, mask_size)
        mask_buffer = BytesIO()
        mask_image.save(mask_buffer, format="PNG")
        mask_data = "data:image/png;base64," + base64.b64encode(
            mask_buffer.getvalue()
        ).decode("ascii")
    selection.pop("bitmap", None)
    selection["description"] = self._quality_text(
        self._candidate or self._layer()["mask"]
    )
    self._ai.plan(
        text,
        dict(self._recipe),
        sorted(self._locked),
        QUrl(self._original).toLocalFile(),
        self._generation,
        mode,
        {
            "layer_name": self.activeLayerName,
            "selection": selection,
            "recent_conversation": recent,
            "selection_image": mask_data,
            "mask_image_note": "第二张图是当前选区/蒙版：白色允许修改，黑色保护。",
            "existing_layers": [
                {
                    "name": l["name"],
                    "recipe": l["recipe"],
                    "mask_label": l["mask"]["label"],
                    "visible": l["visible"],
                    "opacity": l["opacity"],
                }
                for l in self._layers
            ],
            "max_new_layers": min(4, MAX_LAYERS - len(self._layers)),
            "active_is_group": self.activeIsGroup,
        },
    )
    self.changed.emit()
    return True


def _local_result(self, summary):
    self._message(
        "assistant", summary.replace("全局调整", "当前图层选区调整"), state="applied"
    )
    self._pending_request = None


def _cloud_plan(self, result, generation):
    if self._closing:
        return
    pending = self._pending_request or {}
    if generation != self._generation or pending.get("layer_id") != self._selected:
        return self._notify("照片或图层已变化，过期结果未应用", True)
    mode = result.get("mode", "edit")
    if (
        mode == "auto"
        and result["status"] == "unsupported"
        and not pending.get("auto_fallback")
        and any(term in pending.get("text", "") for term in ("磨皮", "皮肤平滑"))
        and any(term in result["summary"] for term in ("选区", "范围", "区域", "图层"))
    ):
        pending["auto_fallback"] = True
        self._status = "AI 正在改用局部分层规划…"
        self.changed.emit()
        if self._ai.plan(
            pending["text"], dict(self._recipe), sorted(self._locked),
            QUrl(self._original).toLocalFile(), self._generation, "regions",
            _spatial_context(self),
        ):
            return
        self._message("error", "AI 未能继续规划局部图层，照片未改变。", state="failed")
        self._pending_request = None
        return
    if mode == "auto" and result.get("action") == "adjust" and self.activeIsGroup:
        result = {
            "mode": "auto",
            "status": "unsupported",
            "action": "unsupported",
            "summary": "当前选中的是图层组，不能直接调组参数。请描述要调整的具体区域，让 AI 建立局部图层。",
        }
    layer_count_before = len(self._layers)
    auto_layering = (mode == "auto" and result.get("action") == "layers") or (
        mode == "regions" and pending.get("auto_fallback", False)
    )
    auto_failed = False
    self._summary = result["summary"]
    if result["status"] == "unsupported":
        self._message("assistant", result["summary"], state="unsupported")
        self._scene_followup = ""
    elif mode == "scene":
        self._scene.set({"summary": result["summary"], "objects": result["objects"]})
        self._scene.remember(self._scene_key())
        self._message(
            "assistant",
            result["summary"] + "\n已建立元素清单，可按类别勾选或在画布点选。",
            state="catalog",
        )
        pixel_selections.precache(self, [o["id"] for o in result["objects"]])
    elif mode == "targets":
        pixel_selections.select_objects(
            self,
            result["object_ids"],
            exclude=result["exclude_ids"],
            summary=result["summary"],
        )
    elif mode == "selection":
        mask = result["mask"]
        mask["label"] = ("AI · " + self._conversation[-1]["text"])[:200]
        pixel_selections.select_hint(
            self, mask, result["summary"], result.get("anchor")
        )
    elif mode == "regions":
        if not pixel_selections.select_regions(
            self, result["regions"], result["summary"], auto_apply=auto_layering
        ) and auto_layering:
            self._message("error", "本地像素选区未能启动，自动分层未执行；请检查图像能力。", state="failed")
            auto_layering = False
            auto_failed = True
    elif auto_layering:
        if len(self._layers) + len(result["regions"]) > MAX_LAYERS:
            self._message("assistant", "图层位置不足，自动分层未执行；请先整理图层。", state="unsupported")
            auto_layering = False
            auto_failed = True
        elif not pixel_selections.select_regions(
            self, result["regions"], result["summary"], auto_apply=True
        ):
            self._message("error", "本地像素选区未能启动，自动分层未执行；请检查图像能力。", state="failed")
            auto_layering = False
            auto_failed = True
    elif mode == "auto" and result.get("action") == "answer":
        self._message("assistant", result["summary"], state="answered")
    elif mode == "advice":
        self._message(
            "assistant", result["summary"], state="proposed", recipe=result["recipe"]
        )
    else:
        self._recipe = Recipe.from_dict(result["recipe"]).to_dict()
        self._message(
            "assistant", result["summary"], state="applied", recipe=self._recipe
        )
        self._commit()
        self._change()
    self._pending_request = None
    if mode == "scene" and self._scene_followup and result["status"] != "unsupported":
        text, self._scene_followup = self._scene_followup, ""
        QTimer.singleShot(0, lambda: self.selectByDescription(text))
    segment_requests = (
        ([self._active] if self._active else [])
        + ([self._pixel_active] if self._pixel_active else [])
        + list(self._queue)
        + list(self._pixel_queue)
    )
    foreground_pixel_pending = any(
        request["op"] == "segment" and request.get("priority") != "low"
        for request in segment_requests
    )
    if auto_failed:
        status = "自动分层未执行，请查看对话中的原因"
    elif result["status"] == "unsupported":
        status = "AI 未执行修改，请查看对话中的说明"
    elif mode == "scene":
        status = (
            "元素清单已建立，正在后台准备轮廓；可先继续操作"
            if any(request["op"] == "segment" for request in segment_requests)
            else "元素清单已建立，可在画布点选或勾选元素"
        )
    elif mode in ("selection", "targets", "regions", "auto") and foreground_pixel_pending:
        status = "AI 已规划局部图层，正在本地生成蒙版；完成后自动建立图层…" if auto_layering else "AI 已定位目标，正在本地生成像素选区…"
    elif auto_layering and len(self._layers) > layer_count_before:
        status = "AI 已自动建立局部调整层；可检查蒙版边缘并继续微调"
    elif mode == "auto" and result.get("action") == "answer":
        status = "AI 已回复；照片未改变"
    elif mode == "auto" and result.get("action") == "adjust":
        status = "AI 已调整当前图层"
    elif mode == "selection" and self._candidate:
        status = "AI 选区等待确认"
    elif mode == "advice":
        status = "AI 建议已保留"
    else:
        status = "AI 已返回"
    self._notify(status, background=mode == "scene")


def applyAdvice(self, message_id):
    if not self._can_edit():
        return
    msg = next((m for m in self._conversation if m["id"] == message_id), None)
    if not msg or msg.get("state") != "proposed" or "recipe" not in msg:
        return
    if msg["layer_id"] != self._selected:
        return self._notify("请先选择建议对应的图层：" + msg["layer_name"])
    if not msg.get("binding") or msg["binding"] != self._document_signature():
        msg["state"] = "stale"
        self._mark_dirty()
        self._conversation_model.refresh(msg["id"])
        self.conversationChanged.emit()
        return self._notify(
            "建议对应的图层或选区已改变，请重新获取建议；当前照片未改变"
        )
    if not raster_mask(self._layer()["mask"], (256, 256)).getbbox():
        return self._notify("当前图层为空选区，请先创建选区", True)
    recipe = dict(msg["recipe"])
    for key in self._locked:
        recipe[key] = self._recipe[key]
    self._recipe = recipe
    msg["state"] = "applied"
    self._conversation_model.refresh(msg["id"])
    self._message("event", "已将这条建议的参数应用于当前选区；手动锁定的参数已保留。")
    self._commit()
    self._change()


def copyMessage(self, message_id):
    msg = next((m for m in self._conversation if m["id"] == message_id), None)
    if msg:
        QGuiApplication.clipboard().setText(msg["text"])


def _export_destination(url):
    path = path_from_url(url)
    if not path.is_absolute():
        raise ValueError("请输入完整保存路径")
    return path


def _conversation_snapshot(self):
    # Copy only immutable scalar values on the UI thread; formatting happens off-thread.
    fields = ("role", "time", "layer_name", "model", "state", "text")
    return tuple(tuple(message[field] for field in fields) for message in self._conversation)


def _write_conversation(path, snapshot):
    text = "# iPhoto 对话记录\n\n" + "\n\n".join(
        f"## {role} · {time}\n\n图层：{layer_name} · {model} · {state}\n\n{body}"
        for role, time, layer_name, model, state, body in snapshot
    )
    with atomic_output(path) as file:
        file.write(text.encode("utf-8"))


def _export_error(exc):
    if isinstance(exc, FileExistsError):
        return "对话导出失败：目标文件已存在，请换一个文件名"
    return "对话导出失败：" + str(exc)


def exportConversation(self, url):
    try:
        path = _export_destination(url)
        _write_conversation(path, _conversation_snapshot(self))
        self._notify("对话已导出：" + str(path))
        return True
    except (ValueError, OSError) as exc:
        self._notify(_export_error(exc), True)
        return False


def exportConversationAsync(self, url):
    if self._conversation_export_future is not None:
        return False
    try:
        if not self._conversation:
            raise ValueError("没有可导出的对话")
        path = _export_destination(url)
        snapshot = _conversation_snapshot(self)
        self._conversation_export_future = self._recovery_executor.submit(
            _write_conversation, path, snapshot
        )
        self._conversation_export_path = path
        self._conversation_export_poll.start()
        self._notify("正在导出对话…")
        return True
    except (ValueError, OSError, RuntimeError, MemoryError) as exc:
        self._notify(_export_error(exc), True)
        return False


def _finish_conversation_export(self, *, wait=False):
    future = self._conversation_export_future
    if future is None or (not wait and not future.done()):
        return
    path = self._conversation_export_path
    self._conversation_export_future = self._conversation_export_path = None
    self._conversation_export_poll.stop()
    try:
        future.result()
        self._notify("对话已导出：" + str(path))
        self.conversationExportCompleted.emit(str(path), "")
    except Exception as exc:
        message = _export_error(exc)
        self._notify(message, True)
        self.conversationExportCompleted.emit(str(path), message)


def suggestConversationPath(self):
    if not self._path:
        return ""
    directory = Path(self.projectBrowseDirectory)
    source = Path(self._project_path or self._path)
    stem = "山湖之间" if self._sample and not self._project_path else source.stem
    candidate = directory / f"{stem}-对话.md"
    number = 2
    while candidate.exists():
        candidate = directory / f"{stem}-对话-{number}.md"
        number += 1
    return str(candidate)
