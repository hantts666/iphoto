"""Qt-facing orchestration for the independent pixel-selection subsystem."""

from copy import deepcopy
from ..document import new_layer, raster_mask, MAX_LAYERS
from ..segmentation.models import available
from ..segmentation.prompts import validate_points


def ready(self):
    if available():
        return True
    self._notify(
        "像素选区模型尚未配置：运行 scripts/setup_segmentation.py 后，在图像能力中重新检测。未用粗多边形替代结果。",
        True,
    )
    return False


def reset_prompts(self, refine=False):
    if self.busy:
        return
    self._pixel_points = []
    self._pixel_hint = deepcopy(self._candidate) if refine else None
    self.changed.emit()


def start(self, jobs, context, priority=None):
    if not ready(self):
        return False
    context.setdefault(
        "origin",
        deepcopy(
            self._pending_request
            or {"mode": "selection", "model": "EfficientSAM-S（本地）"}
        ),
    )
    points = len(context.get("points") or [])
    if context.get("purpose") not in ("warm", "precache"):
        if getattr(self, "_warm_sha", "") == self._sha:
            self._status = "照片首次编码中；点选已排队，完成后会自动生成选区…"
        else:
            self._status = (
                f"正在按 {points} 个提示点生成本地选区…编码已缓存时约 1～3 秒"
                if points
                else "正在生成像素选区…首次需编码照片，后续提示复用缓存"
            )
    if priority:
        self._request("segment", jobs=jobs, context=context, priority=priority)
    else:
        self._request("segment", jobs=jobs, context=context)
    return True


def warm(self):
    """Pre-encode the embedding so the first point click responds in seconds."""
    if self.busy or self._pixel_active or self._pixel_queue or not self.hasImage or not available():
        return
    self._start_warm()


def precache(self, ids):
    """Background precise masks for catalog objects: real hover previews."""
    if not self.hasImage or not available():
        return
    objects = {o["id"]: o for o in (self._scene.catalog or {}).get("objects", [])}
    revision = self._scene.revision
    pending = {
        job["id"]
        for request in (
            ([self._active] if self._active else [])
            + ([self._pixel_active] if self._pixel_active else [])
            + list(self._queue) + list(self._pixel_queue)
        )
        if request["op"] == "segment"
        and request.get("context", {}).get("purpose") == "precache"
        and request["context"].get("source_sha") == self._sha
        and request["context"].get("scene_revision") == revision
        for job in request.get("jobs", [])
    }
    jobs = [
        {
            "id": lid,
            "hint": objects[lid]["mask"],
            "points": [[*objects[lid]["anchor"], 1]] if "anchor" in objects[lid] else [],
        }
        for lid in ids
        if lid in objects and lid not in self._scene.precise and lid not in pending
    ][:16]
    if not jobs:
        return
    self._status = "后台预计算元素蒙版…悬停元素行可查看真实轮廓"
    self._scene.mark_pixel_status([job["id"] for job in jobs], "pending")
    self.changed.emit()
    # One object per request lets the pixel worker prioritize a foreground
    # click and keeps failures limited to a small unit of work.
    for job in jobs:
        start(
            self,
            [job],
            {
                "purpose": "precache",
                "source_sha": self._sha,
                "scene_revision": revision,
                "object_id": job["id"],
                "status_epoch": self._status_epoch,
            },
            priority="low",
        )


def select_objects(self, ids, mode="replace", exclude=None, summary="", auto_apply=False):
    if mode not in ("replace", "add", "subtract", "intersect"):
        return self._notify("选区组合方式无效", True)
    ids = list(dict.fromkeys(ids))
    exclude = list(dict.fromkeys(exclude or []))
    objects = {o["id"]: o for o in (self._scene.catalog or {}).get("objects", [])}
    if not ids or any(lid not in objects for lid in ids + exclude):
        return self._notify("目标不在当前元素清单中，请重新分析画面", True)
    context = {
        "purpose": "objects",
        "ids": ids,
        "exclude": exclude,
        "mode": mode,
        "base": deepcopy(self._candidate),
        "summary": summary,
        "auto_apply": bool(auto_apply),
        "origin": deepcopy(
            self._pending_request
            or {"mode": "selection", "model": "EfficientSAM-S（本地）"}
        ),
    }
    jobs = [
        {
            "id": lid,
            "hint": objects[lid]["mask"],
            "points": [[*objects[lid]["anchor"], 1]]
            if "anchor" in objects[lid]
            else [],
        }
        for lid in ids + exclude
        if lid not in self._scene.precise
    ]
    if not jobs:
        complete(self, {"items": []}, context)
    else:
        start(self, jobs, context)


def select_hint(self, hint, summary="", anchor=None):
    return start(
        self,
        [{"id": "target", "hint": hint, "points": [[*anchor, 1]] if anchor else []}],
        {"purpose": "hint", "hint": deepcopy(hint), "summary": summary},
    )


def select_regions(self, regions, summary, auto_apply=False):
    if len(self._layers) + len(regions) > MAX_LAYERS:
        return self._notify("分区方案超过图层上限，未应用", True)
    origin = deepcopy(self._pending_request or {})
    if auto_apply:
        origin["layer_name"] = "局部分层"
    return start(
        self,
        [
            {
                "id": str(i),
                "hint": r["mask"],
                "points": [[*r["anchor"], 1]] if "anchor" in r else [],
            }
            for i, r in enumerate(regions)
        ],
        {"purpose": "regions", "regions": regions, "summary": summary, "auto_apply": bool(auto_apply), "origin": origin},
    )


def point(self, position, positive):
    if self.busy or self.hasRegionDraft or not self.hasImage:
        return
    try:
        points = validate_points(
            self._pixel_points + [[*position, 1 if positive else 0]]
        )
        if not any(p[2] for p in points):
            return self._notify("先在目标内部点一下，再按 Alt 点击排除背景")
        return start(
            self,
            [{"id": "target", "hint": self._pixel_hint, "points": points}],
            {"purpose": "points", "hint": deepcopy(self._pixel_hint), "points": points},
        )
    except (ValueError, TypeError) as exc:
        self._notify(str(exc), True)


def undo_point(self):
    if self.busy or not self._pixel_points:
        return
    points = self._pixel_points[:-1]
    if not points:
        reset_prompts(self)
        return self._notify("提示点已清除；当前选区保留，可开始新目标")
    start(
        self,
        [{"id": "target", "hint": self._pixel_hint, "points": points}],
        {"purpose": "points", "hint": deepcopy(self._pixel_hint), "points": points},
    )


def cancel(self):
    if getattr(self, "_pixel_active", None):
        self._stop_pixel()
        self._status = "已停止本次像素计算；原选区保留"
        self.changed.emit()
        return
    if (
        self._active
        and self._active["op"] == "segment"
        and self._active.get("priority") != "low"
    ):
        self._active["cancelled"] = True
        self._status = "已取消接收本次选区，等待本地计算释放；原选区保留"
        self.changed.emit()
        return
    queued = [
        request
        for request in list(self._queue) + list(getattr(self, "_pixel_queue", ()))
        if request["op"] == "segment" and request.get("priority") != "low"
    ]
    if queued:
        self._queue = type(self._queue)(
            request for request in self._queue if request not in queued
        )
        if hasattr(self, "_pixel_queue"):
            self._pixel_queue = type(self._pixel_queue)(
                request for request in self._pixel_queue if request not in queued
            )
        self._stop_warm()
        self._status = "已取消等待中的像素选区；原选区保留"
        self.changed.emit()


def keep_points(self, points, message):
    """A rejected click still counts: retain prompts so the next click stacks."""
    self._pixel_points = deepcopy(points)
    self._status = message
    self.changed.emit()
    self._notify(message)


def complete(self, result, context):
    purpose = context["purpose"]
    items = {item["id"]: item for item in result["items"]}
    if purpose == "warm":
        # Preheating is background work. An AI request or another foreground
        # action owns the status line and must not be hidden by this result.
        return
    if purpose == "precache":
        for lid, item in items.items():
            self._scene.set_precise(lid, item["mask"], item["quality"])
        missing = context.get("object_id")
        if missing and missing not in items:
            self._scene.mark_pixel_status([missing], "unavailable")
        states = [row["pixelStatus"] for row in self._scene.rows()]
        status = f"元素轮廓 {states.count('ready')}/{len(states)} 已就绪"
        if pending := states.count("pending"):
            status += f"；{pending} 个预计算中"
        if unavailable := states.count("unavailable"):
            status += f"；{unavailable} 个未可靠贴边，可点击重试或手动选择"
        if (context.get("status_epoch") == self._status_epoch
                and self._candidate is None and not self._ai.busy):
            self._status = status
        self.changed.emit()
        return
    if purpose == "objects":
        for lid, item in items.items():
            self._scene.set_precise(lid, item["mask"], item["quality"])
        self._scene.selected = set(context["ids"])
        mask = self._objects_mask(context["ids"])
        if context["exclude"]:
            mask = self._objects_mask(context["exclude"], "subtract", mask)
        if context["mode"] != "replace":
            from ..scene import combine_masks

            size = (self._width, self._height)
            mask = combine_masks([mask], size, context["base"], context["mode"])
        mask["label"] = (
            "像素对象 · "
            + "、".join(
                o["name"]
                for o in self._scene.catalog["objects"]
                if o["id"] in context["ids"]
            )
        )[:200]
        if not raster_mask(mask, (512, 512)).getbbox():
            raise ValueError("组合后的选区为空，原选区保留；请检查排除对象")
        self._set_candidate(mask)
    elif purpose == "regions":
        layers = []
        for i, region in enumerate(context["regions"]):
            layer = new_layer(region["name"])
            layer.update(mask=items[str(i)]["mask"], recipe=region["recipe"])
            layers.append(layer)
        self._region_candidate = {"summary": context["summary"], "layers": layers}
        self._region_index = 0
        self._mask_url = ""
        self._generation += 1
        self._mark_dirty()
        self._schedule_render()
    else:
        self._set_candidate(items["target"]["mask"])
        # Retain the first result as a spatial prior: on this lightweight
        # model an unconstrained second click can otherwise switch instances.
        self._pixel_hint = deepcopy(context.get("hint") or items["target"]["mask"])
        self._pixel_points = context.get("points", [])
    qualities = [item["quality"] for item in items.values()]
    seconds = sum(q.get("elapsed_ms", 0) for q in qualities) / 1000
    warnings = list(dict.fromkeys(w for q in qualities for w in q.get("warnings", [])))
    self._selection_quality = (
        (self._quality_text(self._candidate) if self._candidate else "像素分区")
        + f" · EfficientSAM-S · {seconds:.1f}s"
        + (" · 缓存" if not items else "")
    )
    if warnings:
        self._selection_quality += " · " + "；".join(warnings)
    self._status = "像素选区已就绪；可修正边缘或开始调整"
    self._message(
        "assistant",
        (context.get("summary", "") + "\n" if context.get("summary") else "")
        + self._selection_quality
        + (
            "\n" + "\n".join(r["name"] + "：" + r["reason"] for r in context["regions"])
            if purpose == "regions"
            else ""
        ),
        state="region_draft" if purpose == "regions" else "draft",
        origin=context.get("origin"),
    )
    self.changed.emit()
    if context.get("auto_apply") and purpose == "regions":
        self.selection.applyRegions()
        if not self.hasRegionDraft:
            self._status = "AI 已自动建立局部调整层；检查边缘时可点图层蒙版缩略图"
            self._notify(self._status)
            return
    if context.get("auto_apply") and purpose == "objects":
        self.selection.apply("new_layer")
        if not self.hasSelectionDraft:
            self._scene.selected.clear()
            self._status = "已开始局部调整；右侧可调参数，点图层蒙版缩略图可修边"
            self.changed.emit()
            self._notify(self._status)
            return
    self._notify("已生成像素蒙版，请检查边缘与漏选")
