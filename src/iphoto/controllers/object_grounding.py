"""Foreground close-up localization for weak small-object masks."""
from copy import deepcopy
from uuid import uuid4

from ..ai_grounding import crop_pixels, map_grounding, needs_object_detail, object_crop
from ..engine import Recipe
from . import pixel_selections


def _targets(self, context):
    purpose = context["purpose"]
    if purpose == "objects":
        ids = set(context["ids"] + context["exclude"])
        return [obj for obj in self._scene.catalog["objects"] if obj["id"] in ids]
    if purpose == "regions":
        return [{**region, "id": str(index)} for index, region in enumerate(context["regions"])]
    return [{"id": "target", "name": context["origin"].get("text", "目标")[:80],
             "mask": context["hint"]}]


def intercept(self, context, items):
    if (not self._ai.ready or context.get("local_only") or context.get("detail_grounded_ids")
            or context["purpose"] not in ("objects", "regions", "hint")
            or context["purpose"] == "hint" and not context.get("origin", {}).get("text")):
        return False
    targets = [obj for obj in _targets(self, context)
               if needs_object_detail(obj["mask"], items.get(obj["id"],
                  self._scene.precise.get(obj["id"], {})).get("quality", {}))]
    if not targets:
        return False
    binding = self._document_signature()
    origin = deepcopy(context.get("origin") or {})
    pending = {**origin, "layer_id": self._selected, "layer_name": self.activeLayerName,
               "model": self._ai.settings.model, "binding": binding}
    pending["object_grounding"] = {
        "remaining": deepcopy(targets), "total": len(targets), "prepared": {},
        "context": deepcopy(context), "seed_items": deepcopy(items),
        "source_sha": self._sha, "scene_revision": self._scene.revision,
        "base": deepcopy(self._candidate), "target_id": self._selection_target_id,
        "layers": deepcopy(self._layers),
    }
    self._pending_request = pending
    if not origin.get("text"):
        self._message("user", "精确选择：" + "、".join(obj["name"] for obj in targets))
    # A queued coarse preview of this same object must not overwrite the final
    # localized result. Other background objects can continue independently.
    ids = {obj["id"] for obj in targets}
    for name in ("_queue", "_pixel_queue"):
        queue = getattr(self, name)
        setattr(self, name, type(queue)(job for job in queue if not (
            job.get("context", {}).get("purpose") == "precache"
            and job["context"].get("object_id") in ids)))
    try:
        _next(self, pending)
    except (ValueError, OSError) as exc:
        self._notify(str(exc), True)
    return True


def _ensure_current(self, pending):
    state = pending["object_grounding"]
    content = lambda layers: [{k: v for k, v in layer.items() if k != "collapsed"} for layer in layers]
    if (state["source_sha"] != self._sha or state["scene_revision"] != self._scene.revision
            or pending["binding"] != self._document_signature()
            or content(state["layers"]) != content(self._layers)
            or state["base"] != self._candidate or state["target_id"] != self._selection_target_id):
        raise ValueError("照片、图层或范围已变化，过期局部定位未应用")


def _next(self, pending):
    _ensure_current(self, pending)
    state = pending["object_grounding"]
    target = state["remaining"].pop(0)
    crop = object_crop(target["mask"], (self._width, self._height))
    state["active"] = {"target": target, "crop": crop, "token": uuid4().hex}
    state["preparing"] = True
    self._status = "正在准备原图局部细节：" + target["name"] + "…"
    self.changed.emit()
    if self._request("object_crop", crop=crop, source_sha=self._sha,
                     context={"object_token": state["active"]["token"]}) is False:
        raise ValueError("原图局部细节未能准备，原范围保留")


def crop_ready(self, result, context, generation):
    pending = self._pending_request or {}
    state = pending.get("object_grounding")
    if not state or context.get("object_token") != state["active"]["token"]:
        return
    try:
        if generation != self._generation:
            raise ValueError("照片或范围已变化，过期局部定位未应用")
        _ensure_current(self, pending)
        active = state["active"]
        rectangle = crop_pixels(active["crop"], (self._width, self._height))
        if result["crop_size"] != [rectangle[2]-rectangle[0], rectangle[3]-rectangle[1]]:
            raise ValueError("原图局部尺寸不一致，原范围保留")
        state["preparing"] = False
        target = active["target"]
        label = f"{len(state['prepared'])+1}/{state['total']} " + target["name"]
        self._status = "AI 正在放大定位：" + label + "…"
        if not self._ai.plan(
            "这张照片是目标附近的原图局部细节。请准确定位：" + target["name"]
            + "。外框覆盖完整目标，保留点必须落在清楚可见的目标实体像素内部，"
            "不要落在外框中心的背景、衣料或镂空处。环形目标的点应在实体边上。"
            "坐标只按当前这张局部图0～999，不按整图估算；看不清则unsupported。",
            Recipe().to_dict(), [], result["path"], self._generation, "selection",
            {"_grounding_label": label}):
            raise ValueError("AI 局部定位未能启动，原范围保留")
        self.changed.emit()
    except (ValueError, KeyError, OSError) as exc:
        self._notify(str(exc), True)


def planned(self, result):
    pending = self._pending_request
    state = pending["object_grounding"]
    try:
        _ensure_current(self, pending)
        if result.get("mode") != "selection" or result["status"] == "unsupported":
            raise ValueError("AI 无法可靠定位当前小目标，原范围保留；可用框选或补点修正。\n" + result["summary"])
        active = state["active"]
        target = active["target"]
        state["prepared"][target["id"]] = map_grounding(
            result, active["crop"], (self._width, self._height), target)
        if state["remaining"]:
            return _next(self, pending)
        context, prepared, seed = state["context"], state["prepared"], state["seed_items"]
        self._pending_request = None
        purpose = context["purpose"]
        if purpose == "objects":
            accepted = pixel_selections.select_objects(
                self, context["ids"], context["mode"], context["exclude"], context["summary"],
                context["auto_apply"], grounded=prepared, seed_items=seed, origin=context.get("origin"))
        elif purpose == "regions":
            regions = deepcopy(context["regions"])
            for lid, region in prepared.items():
                regions[int(lid)].update(mask=region["mask"], anchor=region["anchor"])
            accepted = pixel_selections.select_regions(self, regions, context["summary"], context["auto_apply"],
                                            detail_ids=list(prepared), seed_items=seed, origin=context.get("origin"))
        else:
            region = prepared["target"]
            accepted = pixel_selections.select_hint(self, region["mask"], context["summary"], region["anchor"],
                                         detail_grounded=True, origin=context.get("origin"))
        if accepted is False:
            raise ValueError("局部定位后的像素任务未能启动，原范围与图层保留")
    except (ValueError, KeyError, OSError) as exc:
        # Keep an origin until notification records the failed operation.
        self._pending_request = pending
        self._notify(str(exc), True)


def cancel_preparation(self):
    if not self.aiObjectPreparing:
        return
    self._queue = type(self._queue)(job for job in self._queue if job["op"] != "object_crop")
    if self._active and self._active["op"] == "object_crop":
        self._active["cancelled"] = True
    self._notify("已取消目标局部定位，原有范围与图层保留", True)
