"""Conversation actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
from hashlib import sha256
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
    self._mark_dirty()
    self.conversationChanged.emit()
    self.changed.emit()
    return item


def applyDescription(self, text):
    self.sendMessage(text, "edit")


def sendMessage(self, text, mode):
    if (
        not self.hasImage
        or self.busy
        or self.hasRegionDraft
        or (self.hasSelectionDraft and mode not in ("selection", "scene", "targets"))
    ):
        return
    if self.activeIsGroup and mode in ("edit", "advice"):
        return self._notify("请在组内选择或新建调整层，再使用 AI 修图")
    if self.process.state() != QProcess.Running:
        return self._notify("本地引擎未运行，请保存项目后重新打开 iPhoto", True)
    text = text.strip()
    if not 1 <= len(text) <= 4000 or mode not in (
        "edit",
        "advice",
        "selection",
        "regions",
        "scene",
        "targets",
    ):
        return self._notify("请输入 1～4000 字的要求", True)
    if len(self._conversation) >= 1990:
        return self._notify("对话已接近上限，请保存项目并导出对话后开始新项目", True)
    if self._ai.enabled and not self._ai.ready:
        self.aiSettingsRequested.emit()
        return self._notify("请先填写 API Key")
    if (
        mode == "edit"
        and raster_mask(self._layer()["mask"], (256, 256)).getbbox() is None
    ):
        return self._notify("当前图层是空选区，请先绘制或用 AI 生成选区", True)
    self._commit()
    self._pending_request = {
        "mode": mode,
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
    self._message("user", text)
    if not self._ai.enabled:
        if mode != "edit":
            return self._notify("文字建议与 AI 选区需要在 AI 设置中启用云端模型", True)
        self._request(
            "interpret",
            text=text,
            recipe=dict(self._recipe),
            locked=sorted(self._locked),
        )
        return
    if mode == "regions" and len(self._layers) > MAX_LAYERS - 4:
        return self._notify("自动分区需要预留四个图层位置，请先整理图层", True)
    self._status = {
        "edit": "AI 正在规划当前选区的调整…",
        "advice": "AI 正在分析并撰写建议…",
        "selection": "AI 正在识别选区目标…",
        "regions": "AI 正在判断区域并规划独立调整层…",
        "scene": "AI 正在建立画面元素清单，首次分析可能需要半分钟…",
        "targets": "AI 正在从已识别元素中选择目标…",
    }[mode]
    if mode in ("scene", "targets", "selection", "regions"):
        context = {
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
        return
    selection = deepcopy(self._candidate or self._layer()["mask"])
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
        },
    )
    self.changed.emit()


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
        pixel_selections.select_regions(self, result["regions"], result["summary"])
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
    self._notify(
        "AI 已定位目标，正在本地生成像素选区…"
        if (self._active and self._active["op"] == "segment")
        or any(r["op"] == "segment" for r in self._queue)
        else "AI 选区等待确认"
        if mode == "selection" and self._candidate
        else "AI 建议已保留"
        if mode == "advice"
        else "AI 已返回"
    )


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
    self._message("event", "已将这条建议的参数应用于当前选区；手动锁定的参数已保留。")
    self._commit()
    self._change()


def copyMessage(self, message_id):
    msg = next((m for m in self._conversation if m["id"] == message_id), None)
    if msg:
        QGuiApplication.clipboard().setText(msg["text"])


def exportConversation(self, url):
    try:
        path = path_from_url(url)
        text = "# iPhoto 对话记录\n\n" + "\n\n".join(
            f"## {m['role']} · {m['time']}\n\n图层：{m['layer_name']} · {m['model']} · {m['state']}\n\n{m['text']}"
            for m in self._conversation
        )
        with path.open("x", encoding="utf-8") as f:
            f.write(text)
        self._notify("对话已导出：" + str(path))
    except OSError as exc:
        self._notify("对话导出失败：" + str(exc), True)
