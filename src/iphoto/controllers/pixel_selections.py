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


def start(self, jobs, context):
    if not ready(self):
        return False
    context.setdefault(
        "origin",
        deepcopy(
            self._pending_request
            or {"mode": "selection", "model": "EfficientSAM-S（本地）"}
        ),
    )
    self._status = "正在生成像素选区…首次需编码照片，后续提示复用缓存"
    self._request("segment", jobs=jobs, context=context)
    return True


def select_objects(self, ids, mode="replace", exclude=None, summary=""):
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


def select_regions(self, regions, summary):
    if len(self._layers) + len(regions) > MAX_LAYERS:
        return self._notify("分区方案超过图层上限，未应用", True)
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
        {"purpose": "regions", "regions": regions, "summary": summary},
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
    if self._active and self._active["op"] == "segment":
        self._active["cancelled"] = True
        self._status = "已取消接收本次选区，等待本地计算释放；原选区保留"
        self.changed.emit()


def complete(self, result, context):
    purpose = context["purpose"]
    items = {item["id"]: item for item in result["items"]}
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
    self._status = "像素选区已就绪；可用正负点修正，确认后再输出到图层"
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
    self._notify("已生成像素蒙版，请检查边缘与漏选")
