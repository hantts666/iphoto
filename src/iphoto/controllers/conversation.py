"""Conversation actions. ``self`` is the owning Editor, passed explicitly."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import re
import base64
from io import BytesIO
from uuid import uuid4
from PIL import Image
from PySide6.QtCore import QProcess, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from ..engine import Recipe, LABELS
from ..ai_grounding import region_crop, map_grounding
from ..ai_repair import repair_context, map_repair_spots
from ..inpainting import available as repair_available
from ..segmentation.face_models import available as face_skin_available
from ..document import raster_mask, now, MAX_LAYERS
from ..layer_tree import display_states
from ..paths import path_from_url
from ..storage import atomic_output
from . import layers, pixel_selections, heal, face_inventory


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


def _layer_result_notes(layer_list, edits):
    by_id = {layer["id"]: layer for layer in layer_list}
    display_states_by_id = display_states(layer_list)
    notes = []
    for edit in edits:
        controls = edit.get("requested_controls", [])
        layer = by_id[edit["layer_id"]]
        states = []
        if "visible" in controls:
            states.append("显示" if layer["visible"] else "隐藏")
        if "opacity" in controls:
            label = "整体效果强度" if layer["kind"] == "group" else "不透明度"
            states.append(f"{label} {layer['opacity'] * 100:g}%")
        if states:
            notes.append("“" + layer["name"] + "”：" + "，".join(states) + "。")
        state = display_states_by_id[edit["layer_id"]]
        if not state["enabled"]:
            if not layer["visible"] or layer["opacity"] == 0:
                reason = "当前隐藏" if not layer["visible"] else "不透明度为 0%"
                notes.append("“" + layer["name"] + "”" + reason + "，设置已更新，效果暂不可见。")
            else:
                group = by_id[state["blockers"][-1]]
                reason = "隐藏" if not group["visible"] else "不透明度为 0%"
                notes.append("父组“" + group["name"] + "”仍" + reason + "，“" + layer["name"] + "”效果仍不可见。")
    return notes


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
    if pending.get('repair_layer_ids'):
        item['repair_layer_ids'] = list(pending['repair_layer_ids'])
    if pending.get('adjustment_layer_ids'):
        item['adjustment_layer_ids'] = list(pending['adjustment_layer_ids'])
    if role in ('user', 'error') and pending.get('request_binding'):
        item['request_binding'] = pending['request_binding']
        if role == 'error' and pending.get('request_user_id'):
            item['request_user_id'] = pending['request_user_id']
    self._conversation.append(item)
    self._conversation_model.append(item)
    self._mark_dirty()
    self.conversationChanged.emit()
    self.changed.emit()
    return item


def _retry_binding(self, document_binding=None):
    self._sync_layer()
    # The normal binding already hashes source, layer selection, masks,
    # recipes and display controls. Do not serialize large layer bitmaps twice.
    covered = {'id', 'visible', 'opacity', 'recipe', 'mask', 'kind', 'parent_id', 'collapsed'}
    extra = [{**{k: v for k, v in layer.items() if k not in covered},
              'mask_label': layer['mask'].get('label', '')} for layer in self._layers]
    value = [document_binding or self._document_signature(), extra, self._candidate, self._selection_target_id]
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def failedPromptInfo(self, message_id):
    index = next((i for i, m in enumerate(self._conversation) if m['id'] == message_id), -1)
    if index < 0:
        return {}
    message = self._conversation[index]
    if message['role'] != 'error' or message['state'] != 'failed':
        return {}
    reference = message.get('request_user_id')
    user = next((m for m in self._conversation[:index] if m['id'] == reference), None) if reference else None
    if reference and user and (user['mode'] != message['mode']
                              or user.get('request_binding') != message.get('request_binding')):
        user = None
    # Old projects lack the request reference. Only an adjacent matching user
    # message is unambiguous; never guess across another reply or request.
    if not reference and index:
        previous = self._conversation[index-1]
        if previous['role'] == 'user' and previous['mode'] == message['mode'] and previous['layer_id'] == message['layer_id']:
            user = previous
    settings = bool(re.match(r'^HTTP (400|401|403|404|429)[：:]', message['text']))
    if (not user or user['role'] != 'user' or user['mode'] not in ('auto', 'edit', 'advice', 'regions')
            or not 1 <= len(user['text'].strip()) <= 4000):
        return {'settings': settings}
    return {'available': True, 'settings': settings, 'text': user['text'], 'mode': user['mode'],
            'user_id': user['id'], 'binding': user.get('request_binding', ''),
            'retry': bool(reference and user.get('request_binding'))}


def restoreFailedPrompt(self, message_id):
    info = failedPromptInfo(self, message_id)
    if not self.hasImage or self.busy or not info.get('available'):
        return False
    if self.conversationDraft.strip() and self.conversationDraft != info['text']:
        QGuiApplication.clipboard().setText(info['text'])
        self._notify('已复制原要求，当前输入保留')
        return False
    self.setConversationDraftMode('edit' if info['mode'] == 'auto' else info['mode'])
    self.setConversationDraft(info['text'])
    self._notify('已取回原要求；请检查当前作用范围后发送')
    return True


def retryFailedPrompt(self, message_id):
    info = failedPromptInfo(self, message_id)
    if (not info.get('retry') or not self.hasImage or self.busy or self.hasRegionDraft
            or self.conversationDraft.strip() or not self.ai.enabled or not self.ai.ready):
        return False
    if info['binding'] != _retry_binding(self):
        restoreFailedPrompt(self, message_id)
        self._notify('照片、图层或范围已变化，未重试；原要求已取回，请检查后发送')
        return False
    return self.sendMessage(info['text'], info['mode'])


def conversationRepairLayers(self, message_id):
    message = next((m for m in self._conversation if m['id'] == message_id), None)
    if not message or message['role'] != 'assistant' or message['state'] != 'applied':
        return []
    # Older system-generated repair summaries already have the final layer ID.
    # An unrelated past adjustment on a now-healed layer is not a repair result.
    ids = message.get('repair_layer_ids')
    if ids is None:
        if not message['text'].startswith('已在') or '处执行局部修复，建立独立图层：' not in message['text']:
            return []
        ids = [message.get('layer_id', '')]
    return list(dict.fromkeys(layer['id'] for layer, _ in heal.review_targets(self, ids)))


def conversationAdjustmentLayers(self, message_id):
    from .adjustment_review import live_layers

    message = next((m for m in self._conversation if m['id'] == message_id), None)
    if not message or message['role'] != 'assistant' or message['state'] != 'applied':
        return []
    ids = message.get('adjustment_layer_ids', [message.get('layer_id', '')])
    return [layer['id'] for layer in live_layers(self, ids)]


def applyDescription(self, text):
    self.sendMessage(text, "edit")


def _spatial_context(self):
    return {
        "face_skin_available": face_skin_available(),
        "detected_faces": face_inventory.spatial_context(self),
        "body_skin_available": pixel_selections.available(),
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


def _current_scope(self):
    if self.hasSelectionDraft:
        return "selection"
    if self.activeIsGroup:
        return "group"
    # A layer inside a group may be clipped by ancestor masks. A full mask
    # or a label saying "全图" alone does not make it a whole-image target.
    if self.activeParentId:
        return "local"
    mask = self._layer()["mask"]
    # Only the document's explicit full range proves whole-image coverage.
    # Downsampling a bitmap can hide tiny protected holes or narrow edges.
    full = (
        not mask["ops"]
        and "bitmap" not in mask
        and (mask["base"] == "full") != mask["inverted"]
    )
    return "whole_image" if full else "local"


def sendMessage(self, text, mode):
    if (
        not self.hasImage
        or self.busy
        or self.hasRegionDraft
        or (self.hasSelectionDraft and mode not in ("auto", "selection", "scene", "targets"))
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
    selection_request = mode == "auto" and self.hasSelectionDraft
    new_selection_layer = selection_request and not self._selection_target_id
    if selection_request:
        if self._selection_target_id and self._selection_target_id != self._selected:
            self._notify("原图层已变化，请重新选择要调整的范围", True)
            return False
        if new_selection_layer and len(self._layers) >= MAX_LAYERS:
            self._notify("图层已满，请整理后再让 AI 调整此范围；当前范围保留", True)
            return False
        if not raster_mask(self._candidate, (512, 512)).getbbox():
            self._notify("当前范围为空，请先选择要调整的位置", True)
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
    if mode in ('auto', 'edit', 'advice', 'regions'):
        self._pending_request['request_binding'] = _retry_binding(self, self._pending_request['binding'])
    if mode == "auto":
        self._pending_request["layer_snapshot"] = deepcopy(self._layers)
    if selection_request:
        self._pending_request["selection_snapshot"] = {
            "mask": deepcopy(self._candidate), "target_id": self._selection_target_id,
            "layers": self._pending_request["layer_snapshot"],
        }
        self._pending_request["layer_name"] = self.activeLayerName if not new_selection_layer else "当前选择范围"
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
        user = self._message("user", text)
        self._pending_request['request_user_id'] = user['id']
        return True
    user = self._message("user", text)
    self._pending_request['request_user_id'] = user['id']
    self._status = {
        "auto": "AI 正在判断调整范围并规划图层…",
        "edit": "AI 正在规划当前选区的调整…",
        "advice": "AI 正在分析并撰写建议…",
        "selection": "AI 正在识别选区目标…",
        "regions": "AI 正在判断区域并规划独立调整层…",
        "scene": "AI 正在建立画面元素清单，首次分析可能需要半分钟…",
        "targets": "AI 正在从已识别元素中选择目标…",
    }[mode]
    if selection_request:
        self._status = "AI 正在规划当前范围的调整；原范围保留，完成后自动应用…"
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
    current_scope = _current_scope(self)
    layer_display = display_states(self._layers)
    mask_data = None
    if mode != "auto" or current_scope != "whole_image":
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
        Recipe().to_dict() if new_selection_layer else dict(self._recipe),
        [] if new_selection_layer else sorted(self._locked),
        QUrl(self._original).toLocalFile(),
        self._generation,
        mode,
        {
            "layer_name": self.activeLayerName,
            "current_scope": current_scope,
            "current_display_enabled": new_selection_layer or layer_display[self._selected]["enabled"],
            "selection_output": "new_layer" if new_selection_layer else "replace_mask" if selection_request else "current_layer",
            "selection_layer_id": self._selection_target_id if selection_request else "",
            "selection": selection,
            "recent_conversation": recent,
            "selection_image": mask_data,
            "mask_image_note": "第二张图是当前选区/蒙版：白色允许修改，黑色保护。",
            "existing_layers": [
                {
                    "id": l["id"],
                    "name": l["name"],
                    "kind": l["kind"],
                    "parent_id": l["parent_id"],
                    "recipe": l["recipe"],
                    "locked": l["locked"],
                    "mask_label": l["mask"]["label"],
                    "visible": l["visible"],
                    "opacity": l["opacity"],
                    "display": layer_display[l["id"]],
                }
                for l in self._layers
            ],
            "max_new_layers": min(4, MAX_LAYERS - len(self._layers)),
            "active_is_group": self.activeIsGroup,
            "repair_available": repair_available(),
            "face_skin_available": face_skin_available(),
            "detected_faces": face_inventory.spatial_context(self),
            "body_skin_available": pixel_selections.available(),
        },
    )
    self.changed.emit()
    return True


def _local_result(self, summary):
    notes = _layer_result_notes(self._layers, [{"layer_id": self._selected}])
    if notes:
        summary = "已更新“" + self.activeLayerName + "”的调整参数。\n" + "\n".join(notes)
    self._message(
        "assistant", summary.replace("全局调整", "当前图层选区调整"), state="applied"
    )
    self._pending_request = None


def _skin_grounding_next(self, pending):
    grounding = pending["skin_grounding"]
    target = grounding["remaining"].pop(0)
    index = target["index"] if isinstance(target, dict) else target
    part_index = target["part"] if isinstance(target, dict) else None
    region = grounding["regions"][index]
    part = region["parts"][part_index] if part_index is not None else region
    name = region["name"] + (f" · 第{part_index+1}个部位" if part_index is not None else "")
    path = QUrl(self._original).toLocalFile()
    with Image.open(path) as picture:
        size = picture.size
    crop = region_crop(part["mask"], size)
    if region.get("mask_target") in ("face_skin", "body_skin"):
        part["skin_crop"] = crop
    grounding["active"] = {"index": index, "part": part_index, "crop": crop, "size": size}
    self._status = "AI 正在放大定位：" + name + "…"
    self.changed.emit()
    return self._ai.plan(
        "图片是需要调整部位的局部放大图。请只定位裸露的皮肤，避开帽子、帽檐、"
        "头发、衣服与背景，排除眉毛、眼睛、嘴唇和口腔。仅定位当前这个部位，不能把其他部位一起选入。目标：" + name + "；" + region["reason"]
        + "。用户要求：" + pending["text"][:1000]
        + "。box 紧贴该部位的皮肤，point 必须落在可见皮肤内部，不能落在边缘。"
        "坐标按当前这张局部图片归一化 0～999。目标不可见或不能可靠定位则返回 unsupported。",
        Recipe().to_dict(), [], path, self._generation, "selection",
        {"_image_crop": crop, "_grounding_label": name},
    )


def _repair_next(self, pending):
    repair = pending["repair_grounding"]
    region = repair["remaining"].pop(0)
    size = (self._width, self._height)
    context = repair_context(region, size)
    context["_grounding_label"] = "瑕疵检查 · " + region["name"]
    token = uuid4().hex
    repair["active"] = {"region": region, "size": size, "context": context, "token": token}
    repair["preparing"] = True
    self._status = "正在从原图准备局部细节：" + region["name"] + "…"
    self.changed.emit()
    return self._request("repair_crop", crop=context["_image_crop"], source_sha=self._sha,
                         context={"repair_token": token})


def _repair_crop_ready(self, result, request_context, generation):
    pending = self._pending_request or {}
    repair = pending.get("repair_grounding")
    if not repair or request_context.get("repair_token") != repair["active"]["token"]:
        return
    if generation != self._generation or pending.get("binding") != self._document_signature():
        return self._notify("照片或图层已变化，过期修复未应用", True)
    active = repair["active"]
    if result.get("crop_size") != active["context"]["crop_size"]:
        return self._notify("原图局部尺寸不一致，照片未改变", True)
    repair["preparing"] = False
    region = active["region"]
    self._status = "AI 正在放大检查：" + region["name"] + "…"
    # The worker already cropped the decoded, oriented full-resolution source.
    # Keep the mapping crop internally, but do not crop this small PNG again.
    context = {k: v for k, v in active["context"].items() if k != "_image_crop"}
    return self._ai.plan(
        "请检查这张局部放大图，只定位清楚可见的小瑕疵。部位：" + region["name"]
        + "；目标：" + region["reason"] + "。用户原要求：" + pending["text"][:1800],
        Recipe().to_dict(), [], result["path"], self._generation, "repair", context,
    )


def cancelRepairPreparation(self):
    if not self.aiRepairPreparing:
        return
    self._queue = type(self._queue)(job for job in self._queue if job["op"] != "repair_crop")
    if self._active and self._active["op"] == "repair_crop":
        self._active["cancelled"] = True
    self._notify("已取消局部修复准备，照片未改变", True)


def _cloud_plan(self, result, generation):
    if self._closing:
        return
    pending = self._pending_request or {}
    if generation != self._generation or pending.get("layer_id") != self._selected:
        return self._notify("照片或图层已变化，过期结果未应用", True)
    if pending.get("object_grounding"):
        from .object_grounding import planned

        return planned(self, result)
    repair = pending.get("repair_grounding")
    if repair and result.get("mode") == "repair":
        try:
            if pending.get("binding") != self._document_signature():
                raise ValueError("照片中的图层已变化，过期修复未应用")
            active = repair["active"]
            if result["status"] == "unsupported":
                repair["unlocated"].append(active["region"]["name"] + "：" + result["summary"])
            else:
                repair["prepared"].append(map_repair_spots(result, active["context"], active["size"], active["region"]))
            if repair["remaining"]:
                _repair_next(self, pending)
                return
            if not repair["prepared"]:
                self._message("assistant", "没有可靠定位到可修复的小瑕疵，照片未改变。\n" + "\n".join(repair["unlocated"]), state="unsupported")
                self._pending_request = None
                return self._notify("AI 未执行修复，请查看对话中的说明")
            ids = heal.applyAIRepairs(self, repair["prepared"], pending.get("layer_snapshot", []))
        except (ValueError, KeyError, OSError) as exc:
            return self._notify(str(exc), True)
        count = sum(len(part["heal"]["ops"]) for part in repair["prepared"])
        names = "、".join(part["name"] for part in repair["prepared"])
        summary = f"已在{count}处执行局部修复，建立独立图层：{names}。\n原有图层参数和范围保留；可调整强度、隐藏对比或一步撤销，请放大检查修复细节。"
        if repair["unlocated"]:
            summary += "\n以下部位保持不变：" + "\n".join(repair["unlocated"])
        origin = {**pending, "layer_id": ids[-1], "layer_name": self.activeLayerName, 'repair_layer_ids': ids}
        self._message("assistant", summary, state="applied", origin=origin)
        self._pending_request = None
        return self._notify("AI 已建立局部修复层；可一步撤销或调整强度")
    if result.get("mode") == "auto" and result.get("action") == "repair":
        try:
            if pending.get("binding") != self._document_signature():
                raise ValueError("照片中的图层已变化，过期修复未应用")
            if not repair_available():
                raise ValueError("本地修复能力不可用，请检查图像能力")
            if len(self._layers) + len(result["repairs"]) > MAX_LAYERS:
                raise ValueError("修复图层位置不足，照片未改变")
            pending["repair_grounding"] = {"remaining": list(result["repairs"]), "prepared": [], "unlocated": []}
            _repair_next(self, pending)
        except (ValueError, OSError) as exc:
            self._notify(str(exc), True)
        return
    grounding = pending.get("skin_grounding")
    if grounding and result.get("mode") == "selection":
        if result["status"] == "unsupported":
            self._message("assistant", "局部皮肤定位未完成：" + result["summary"], state="unsupported")
            self._pending_request = None
            return self._notify("AI 无法可靠定位皮肤，照片未改变；请查看对话说明")
        try:
            active = grounding["active"]
            index = active["index"]
            part_index = active.get("part")
            region = grounding["regions"][index]
            if part_index is None:
                grounding["regions"][index] = map_grounding(result, active["crop"], active["size"], region)
            else:
                region["parts"][part_index] = map_grounding(result, active["crop"], active["size"], region["parts"][part_index])
            if grounding["remaining"]:
                _skin_grounding_next(self, pending)
                return
        except (ValueError, OSError) as exc:
            return self._notify(str(exc), True)
        result = {
            "mode": "auto", "action": "layers", "status": "planned",
            "summary": grounding["summary"], "regions": grounding["regions"],
        }
        pending["skin_grounded"] = True
        del pending["skin_grounding"]
    mode = result.get("mode", "edit")
    if (
        mode == "auto"
        and result["status"] == "unsupported"
        and not pending.get("auto_fallback")
        and not pending.get("selection_snapshot")
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
    if mode == "auto" and result.get("action") == "adjust" and self.activeIsGroup and not pending.get("selection_snapshot"):
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
    if auto_layering and result["status"] != "unsupported" and not pending.get("skin_grounded"):
        from ..segmentation.face_detection import match_hint
        result['regions'] = deepcopy(result['regions'])
        for region in result['regions']:
            face = match_hint(region['mask'],face_inventory.current(self)) if region.get('mask_target')=='face_skin' else None
            if face:
                region.update(mask=deepcopy(face['mask']),anchor=list(face['anchor']),
                              skin_crop=list(face['skin_crop']),recover_face_anchor=True)
                if 'face_features' in face:
                    region['face_features'] = deepcopy(face['face_features'])
        skin_indices = [i for i, r in enumerate(result["regions"])
                        if r["recipe"]["skin_smoothing"] > 0 and not r.get('recover_face_anchor')]
        if skin_indices and len(self._layers) + len(result["regions"]) <= MAX_LAYERS:
            pending["skin_grounding"] = {
                "regions": deepcopy(result["regions"]), "summary": result["summary"],
                "remaining": [target for i in skin_indices for target in (
                    [{"index": i, "part": j} for j in range(len(result["regions"][i]["parts"]))]
                    if result["regions"][i].get("parts") else [i])],
            }
            try:
                _skin_grounding_next(self, pending)
            except (ValueError, OSError) as exc:
                self._notify(str(exc), True)
            return
    auto_failed = False
    self._summary = result["summary"]
    if result["status"] == "unsupported":
        self._message("assistant", result["summary"], state="unsupported")
        self._scene_followup = ""
    elif mode == "scene":
        from ..scene import with_local_faces
        self._scene.set(with_local_faces({"summary": result["summary"], "objects": result["objects"]},self._face_hints))
        self._scene.remember(self._scene_key())
        self._message(
            "assistant",
            result["summary"] + "\n已建立元素清单，可按类别勾选或在画布点选。",
            state="catalog",
        )
        pixel_selections.precache(self, [o["id"] for o in self._scene.catalog["objects"]])
    elif mode == "targets":
        pixel_selections.select_objects(
            self,
            result["object_ids"],
            exclude=result["exclude_ids"],
            summary=result["summary"],
        )
        if (self._pending_request or {}).get("object_grounding"):
            return
    elif mode == "selection":
        mask = result["mask"]
        mask["label"] = ("AI · " + self._conversation[-1]["text"])[:200]
        from ..segmentation.face_detection import match_hint
        target = result.get('mask_target','object')
        face = match_hint(mask,face_inventory.current(self)) if target in ('face','face_skin') else None
        # Remember a full face only after the local parser succeeds. A skin
        # patch, failed prediction or cancelled request cannot become a face.
        face_hint = None
        if target == 'face' and (face or result.get('anchor')):
            face_hint = deepcopy(face) if face else {
                'name': 'AI 人脸', 'mask': deepcopy(mask), 'anchor': list(result['anchor']),
            }
            if not face:
                face_hint['mask']['label'] = 'AI 人脸'
        pixel_selections.select_hint(
            self, deepcopy(face['mask']) if face else mask, result["summary"],
            face['anchor'] if face else result.get("anchor"),mask_target=target,
            crop=face['skin_crop'] if face else None,recover_face_anchor=bool(face),
            features=face.get('face_features') if face else None, face_hint=face_hint
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
    elif mode == "auto" and result.get("action") == "group":
        try:
            if pending.get("binding") != self._document_signature():
                raise ValueError("照片中的图层已变化，过期编组未应用")
            lid = layers.applyGroupPlan(self, result["group"], pending.get("layer_snapshot", []))
        except (ValueError, KeyError) as exc:
            self._message("error", str(exc), state="failed")
            auto_failed = True
        else:
            by_id = {layer["id"]: layer for layer in self._layers}
            names = "、".join("“" + by_id[i]["name"] + "”" for i in result["group"]["layer_ids"])
            summary = "已将" + names + "编入“" + self.activeLayerName + "”。\n各子层参数、蒙版和显示设置保留。"
            notes = _layer_result_notes(self._layers, [{"layer_id": lid, "requested_controls": ["visible", "opacity"]}])
            if notes:
                summary += "\n" + "\n".join(notes)
            origin = {**pending, "layer_id": lid, "layer_name": self.activeLayerName}
            self._message("assistant", summary, state="applied", origin=origin)
    elif mode == "auto" and result.get("action") == "update_layers":
        try:
            if pending.get("binding") != self._document_signature():
                raise ValueError("照片中的图层已变化，过期修改未应用")
            ids = layers.applyLayerEdits(self, result["layer_edits"], pending.get("layer_snapshot", []))
        except (ValueError, KeyError) as exc:
            self._message("error", str(exc), state="failed")
            auto_failed = True
        else:
            picked = self._selected if ids else result["layer_edits"][0]["layer_id"]
            self._selection.pickLayer(picked)
            if ids:
                previous = next(layer['recipe'] for layer in pending['layer_snapshot'] if layer['id'] == picked)
                self._selection.focusChangedParameters(previous)
            names = "、".join(l["name"] for l in self._layers if l["id"] in ids)
            blocked = [
                next(l["name"] for l in self._layers if l["id"] == e["layer_id"])
                + "（" + "、".join(LABELS[k] for k in e["preserved_locked"]) + "）"
                for e in result["layer_edits"] if e["preserved_locked"]
            ]
            controls = _layer_result_notes(self._layers, result["layer_edits"])
            if ids:
                summary = "已调整已有图层：" + names + "。"
                if not controls and not blocked:
                    summary += "\n" + result["summary"]
            else:
                summary = "本次没有可应用的图层变化，照片未改变。"
            if controls:
                summary += "\n" + "\n".join(controls)
            if blocked:
                summary += "\n保留手动锁定：" + "、".join(blocked) + "；这些参数未修改。"
            origin = {**pending, "layer_id": self._selected, "layer_name": self.activeLayerName,
                      **({"adjustment_layer_ids": ids} if ids else {})}
            self._message("assistant", summary, state="applied" if ids else "answered", recipe=self._recipe, origin=origin)
            result["changed_layers"] = ids
    elif mode == "auto" and result.get("action") == "answer":
        self._message("assistant", result["summary"], state="answered")
    elif mode == "auto" and result.get("scope") == "current_selection":
        try:
            if pending.get("binding") != self._document_signature():
                raise ValueError("照片中的图层已变化，过期范围调整未应用")
            lid = layers.applySelectionRecipe(self, result["recipe"], pending["selection_snapshot"])
        except (ValueError, KeyError) as exc:
            self._message("error", str(exc), state="failed")
            auto_failed = True
        else:
            output = "已保存此层的范围并调整" if pending["selection_snapshot"]["target_id"] else "已使用当前范围建立独立调整层"
            summary = output + "：" + self.activeLayerName + "。\n" + result["summary"]
            origin = {**pending, "layer_id": lid, "layer_name": self.activeLayerName}
            self._message("assistant", summary, state="applied", recipe=self._recipe, origin=origin)
    elif mode == "auto" and result.get("action") == "global":
        lid = layers.addGlobalLayer(self, result["recipe"], at_root=True)
        if lid:
            origin = {**pending, "layer_id": lid, "layer_name": self.activeLayerName}
            self._message("assistant", result["summary"], state="applied", recipe=self._recipe, origin=origin)
            self._selection.pickLayer(lid)
            self._selection.focusChangedParameters()
        else:
            self._message("error", "全图调整层未能建立，原来的局部层保持不变；请先整理图层。", state="failed")
            auto_failed = True
    elif mode == "advice":
        self._message(
            "assistant", result["summary"], state="proposed", recipe=result["recipe"]
        )
    else:
        previous_recipe = dict(self._recipe)
        self._recipe = Recipe.from_dict(result["recipe"]).to_dict()
        notes = _layer_result_notes(self._layers, [{"layer_id": self._selected}])
        summary = result["summary"] if not notes else "已更新“" + self.activeLayerName + "”的调整参数。\n" + "\n".join(notes)
        self._message(
            "assistant", summary, state="applied", recipe=self._recipe
        )
        self._commit()
        self._change()
        self._selection.focusChangedParameters(previous_recipe)
    if (self._pending_request or {}).get("object_grounding"):
        return
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
        status = "AI 调整未执行，请查看对话中的原因"
    elif result["status"] == "unsupported":
        status = "AI 未执行修改，请查看对话中的说明"
    elif mode == "scene":
        status = (
            "元素清单已建立，正在后台准备轮廓；可先继续操作"
            if any(request["op"] == "segment" for request in segment_requests)
            else "元素清单已建立，可在画布点选或勾选元素"
        )
    elif mode in ("selection", "targets", "regions", "auto") and foreground_pixel_pending:
        facial = any(r.get("mask_target") == "face_skin" for r in result.get("regions", []))
        body = any(r.get("mask_target") == "body_skin" for r in result.get("regions", []))
        status = ("正在自动分离面部皮肤、保护眉眼和嘴唇；完成后自动建立图层…" if facial
                  else "正在按原图细节分别生成身体部位范围；完成后自动建立图层…" if body
                  else "AI 已规划局部图层，正在本地生成蒙版；完成后自动建立图层…" if auto_layering
                  else "AI 已定位目标，正在本地生成像素选区…")
    elif auto_layering and len(self._layers) > layer_count_before:
        status = "AI 已自动建立局部调整层；可检查蒙版边缘并继续微调"
    elif mode == "auto" and result.get("action") == "answer":
        status = "AI 已回复；照片未改变"
    elif mode == "auto" and result.get("action") == "global":
        status = "AI 已建立全图调整层，原来的局部调整保留；可一步撤销"
    elif mode == "auto" and result.get("action") == "group":
        status = "AI 已编组，子层参数与范围保留；可一步撤销"
    elif mode == "auto" and result.get("action") == "update_layers":
        status = "AI 已修改已有图层；可一步撤销" if result.get("changed_layers") else "AI 未改变参数，请查看对话中的原因"
    elif mode == "auto" and result.get("action") == "adjust":
        status = "AI 已使用当前范围完成调整；可一步撤销" if result.get("scope") == "current_selection" else "AI 已调整当前图层"
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
    previous_recipe = dict(self._recipe)
    self._recipe = recipe
    msg["state"] = "applied"
    self._conversation_model.refresh(msg["id"])
    notes = _layer_result_notes(self._layers, [{"layer_id": self._selected}])
    summary = "已将这条建议的参数应用于当前选区；手动锁定的参数已保留。"
    if notes:
        summary += "\n" + "\n".join(notes)
    self._message("event", summary)
    self._commit()
    self._change()
    self._selection.focusChangedParameters(previous_recipe)


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
