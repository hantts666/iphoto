"""Qt-facing orchestration for the independent pixel-selection subsystem."""

from copy import deepcopy
from ..document import new_layer, raster_mask_cached, validate_mask, MAX_LAYERS
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


def start(self, jobs, context, priority=None, composition=None):
    facial = any(job.get("mask_target") == "face_skin" for job in jobs)
    body = any(job.get("mask_target") == "body_skin" for job in jobs)
    if facial:
        from ..segmentation.face_models import available as face_available

        if not face_available():
            self._notify("面部皮肤模型尚未配置：运行 scripts/setup_face_parsing.py 后重新检测图像能力；本次分层未应用。", True)
            return False
    if (composition is None or jobs) and (not jobs or any(job.get("mask_target", "object") in ("object", "body_skin") for job in jobs)) and not ready(self):
        return False
    if facial or body:
        self._stop_warm()
    context.setdefault(
        "origin",
        deepcopy(
            self._pending_request
            or {"mode": "selection", "model": "EfficientSAM-S（本地）"}
        ),
    )
    points = len(context.get("points") or [])
    if context.get("purpose") not in ("warm", "precache"):
        if composition is not None and not jobs:
            self._status = f"正在组合 {len(composition['ids'])} 个对象的范围…"
        elif facial:
            self._status = "正在自动分离面部皮肤、保护眉眼和嘴唇…"
        elif body:
            self._status = "正在按原图细节分别生成身体部位范围…"
        elif getattr(self, "_warm_sha", "") == self._sha:
            self._status = "照片首次编码中；点选已排队，完成后会自动生成选区…"
        else:
            self._status = (
                f"正在按 {points} 个提示点生成本地选区…编码已缓存时约 1～3 秒"
                if points
                else "正在生成像素选区…首次需编码照片，后续提示复用缓存"
            )
    options = {"priority": priority} if priority else {}
    if composition is not None:
        options["composition"] = composition
    if self._request("segment", jobs=jobs, context=context, **options) is False:
        return False
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


def select_objects(self, ids, mode="replace", exclude=None, summary="", auto_apply=False,
                   *, grounded=None, seed_items=None, origin=None, local_only=False):
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
        "local_only": bool(local_only),
        "detail_grounded_ids": list(grounded or {}),
        "seed_items": deepcopy(seed_items or {}),
        "source_sha": self._sha,
        "scene_revision": self._scene.revision,
        "origin": deepcopy(
            origin or self._pending_request
            or {"mode": "selection", "model": "EfficientSAM-S（本地）"}
        ),
    }
    from .object_grounding import intercept

    if intercept(self, context, {}):
        return True
    grounded, seed_items = grounded or {}, seed_items or {}
    jobs = [
        {
            "id": lid,
            "hint": grounded.get(lid, objects[lid])["mask"],
            "points": [[*grounded.get(lid, objects[lid])["anchor"], 1]]
            if "anchor" in grounded.get(lid, objects[lid])
            else [],
        }
        for lid in ids + exclude
        if lid in grounded or lid not in self._scene.precise and lid not in seed_items
    ]
    if len(ids) > 1 or exclude or mode != "replace":
        context["composed"] = True
        composition = {"ids": ids, "exclude": exclude, "mode": mode,
                       "size": [self._width, self._height], "base": context["base"],
                       "cached": {lid: deepcopy(seed_items.get(lid, self._scene.precise.get(lid, {}))["mask"])
                                  for lid in ids+exclude if lid not in grounded
                                  and (lid in seed_items or lid in self._scene.precise)}}
        return start(self, jobs, context, composition=composition)
    elif not jobs:
        complete(self, {"items": []}, context)
        return True
    else:
        return start(self, jobs, context)


def select_hint(self, hint, summary="", anchor=None, *, detail_grounded=False, origin=None):
    return start(
        self,
        [{"id": "target", "hint": hint, "points": [[*anchor, 1]] if anchor else []}],
        {"purpose": "hint", "hint": deepcopy(hint), "summary": summary,
         "detail_grounded_ids": ["target"] if detail_grounded else [],
         **({"origin": deepcopy(origin)} if origin else {})},
    )


def select_regions(self, regions, summary, auto_apply=False, *, detail_ids=None, seed_items=None, origin=None):
    if len(self._layers) + len(regions) > MAX_LAYERS:
        return self._notify("分区方案超过图层上限，未应用", True)
    origin = deepcopy(origin or self._pending_request or {})
    if auto_apply:
        origin["layer_name"] = "局部分层"
    return start(
        self,
        [
            {
                "id": str(i),
                "hint": r["mask"],
                "points": [[*r["anchor"], 1]] if "anchor" in r else [],
                "mask_target": r.get("mask_target", "object"),
                **({"skin_crop": r["skin_crop"]} if "skin_crop" in r else {}),
                **({"parts": [{"hint": p["mask"], "points": [[*p["anchor"], 1]],
                              **({"skin_crop": p["skin_crop"]} if "skin_crop" in p else {})}
                             for p in r["parts"]]} if r.get("parts") else {}),
            }
            for i, r in enumerate(regions)
            if str(i) in (detail_ids or []) or str(i) not in (seed_items or {})
        ],
        {"purpose": "regions", "regions": regions, "summary": summary, "auto_apply": bool(auto_apply), "origin": origin,
         "detail_grounded_ids": list(detail_ids or []), "seed_items": deepcopy(seed_items or {})},
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


def cancel_preparation(self):
    """Stop preparation and its background queue, without changing the photo."""
    from .worker_bridge import _drop_pixel_requests

    self._stop_warm()
    # A queued background object would otherwise restart the same encoding as
    # soon as the warm process exits, defeating an explicit preparation cancel.
    _drop_pixel_requests(self)
    self._stop_pixel()
    self._queue = type(self._queue)(
        request for request in self._queue
        if not (request["op"] == "segment" and request.get("priority") == "low")
    )
    self._notify("已取消照片准备；图层与选区保留，下次点选会重新准备")


def keep_points(self, points, message):
    """A rejected click still counts: retain prompts so the next click stacks."""
    self._pixel_points = deepcopy(points)
    self._status = message
    self.changed.emit()
    self._notify(message)


def failed_result(self, context, error):
    """Keep an automatic-result failure in the saved conversation as well."""
    self._status = "局部调整未应用，已有图层与范围保留"
    self._message("error", str(error), state="failed", origin=context.get("origin"))
    self._notify(self._status + "：" + str(error), True)


def complete(self, result, context):
    purpose = context["purpose"]
    auto_apply = bool(context.get("auto_apply")) and purpose in ("objects", "regions")
    items = {item["id"]: item for item in result["items"]}
    if purpose == "objects" and (
        context.get("source_sha", self._sha) != self._sha
        or context.get("scene_revision", self._scene.revision) != self._scene.revision
    ):
        raise ValueError("对象清单或照片已更新，本次范围未应用")
    from .object_grounding import intercept

    if intercept(self, context, items):
        return
    if any(lid not in items for lid in context.get("detail_grounded_ids", [])):
        raise ValueError("局部定位后的像素结果不完整，原有范围与图层保留")
    items = {**context.get("seed_items", {}), **items}
    for lid in context.get("detail_grounded_ids", []):
        items[lid]["quality"] = {**items[lid]["quality"], "detail_grounded": True}
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
        if review := sum(bool(row["pixelWarnings"]) for row in self._scene.rows()):
            status += f"；{review} 个范围需检查，可补点 / 排除点"
        if (context.get("status_epoch") == self._status_epoch
                and self._candidate is None and not self._ai.busy):
            self._status = status
        self.changed.emit()
        return
    if purpose == "objects":
        for lid, item in items.items():
            self._scene.set_precise(lid, item["mask"], item["quality"])
        if any(lid not in self._scene.precise for lid in context["ids"] + context["exclude"]):
            raise ValueError("未取得全部目标的像素蒙版，已有图层与范围保留；请重试")
        if context.get("composed"):
            mask = validate_mask(result.get("mask"), cache_bitmap=True)
            bitmap = mask.get("bitmap", {})
            if (bitmap.get("width"), bitmap.get("height")) != (self._width, self._height):
                raise ValueError("对象组合结果未保持原图尺寸，已有图层与范围保留")
        else:
            mask = self._objects_mask(context["ids"])
            if context["exclude"] or context["mode"] != "replace":
                raise ValueError("对象组合缺少后台结果，已有图层与范围保留")
        mask["label"] = (
            "像素对象 · "
            + "、".join(
                o["name"]
                for o in self._scene.catalog["objects"]
                if o["id"] in context["ids"]
            )
        )[:200]
        if not raster_mask_cached(mask, (512, 512)).getbbox():
            raise ValueError("组合后的选区为空，原选区保留；请检查排除对象")
        self._scene.selected = set(context["ids"])
        if auto_apply:
            name = "、".join(o["name"] for o in self._scene.catalog["objects"] if o["id"] in context["ids"])
            layer = new_layer(name[:80])
            layer["mask"] = mask
            generated = [layer]
        else:
            self._set_candidate(mask)
    elif purpose == "regions":
        layers = []
        for i, region in enumerate(context["regions"]):
            layer = new_layer(region["name"])
            layer.update(mask=items[str(i)]["mask"], recipe=region["recipe"])
            if "bitmap" not in layer["mask"]:
                raise ValueError("分区像素蒙版不完整，已有图层与范围保留")
            layers.append(layer)
        if auto_apply:
            generated = layers
        else:
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
    # Cached masks retain their quality warnings. Their historical model time
    # is not part of this selection's elapsed time.
    warning_qualities = ([self._scene.precise[lid]["quality"]
                          for lid in dict.fromkeys(context["ids"] + context["exclude"])]
                         if purpose == "objects" else qualities)
    warnings = list(dict.fromkeys(w for q in warning_qualities for w in q.get("warnings", [])))
    quality_mask = mask if purpose == "objects" else self._candidate
    quality = (
        (self._quality_text(quality_mask) if quality_mask else "像素分区")
        + " · " + " + ".join(dict.fromkeys(q.get("model", "EfficientSAM-S") for q in warning_qualities)) + f" · {seconds:.1f}s"
        + (" · 缓存" if not items else "")
    )
    if warnings:
        quality += " · " + "；".join(warnings)
    if context.get("detail_grounded_ids"):
        quality += " · 已按原图局部细节重新定位"
    detail = (
        (context.get("summary", "") + "\n" if context.get("summary") else "")
        + quality
        + ("\n" + "\n".join(r["name"] + "：" + r["reason"] for r in context["regions"])
           if purpose == "regions" else "")
    )
    if auto_apply:
        from .layers import addLocalLayers

        addLocalLayers(self, generated, consume_selection=purpose == "objects")
        self._selection_quality = quality
        if purpose == "objects":
            self._scene.selected.clear()
        origin = {**context.get("origin", {}), "layer_id": self._selected,
                  "adjustment_layer_ids": [layer['id'] for layer in generated],
                  "layer_name": "局部分层" if purpose == "regions" else self.activeLayerName}
        self._message("assistant", detail + f"\n已建立 {len(generated)} 个独立调整层，可直接调参数、检查范围或一步撤销。",
                      state="applied", origin=origin)
        self._status = ("AI 已自动建立局部调整层；检查边缘时可点图层蒙版缩略图"
                        if purpose == "regions" else "已开始局部调整；右侧可调参数，点图层蒙版缩略图可修边")
        if warnings:
            self._status += "；范围需检查：" + "；".join(warnings)
        self._notify(self._status)
        return
    self._selection_quality = quality
    self._status = "像素选区已就绪；可修正边缘或开始调整"
    self._message(
        "assistant",
        detail,
        state="region_draft" if purpose == "regions" else "draft",
        origin=context.get("origin"),
    )
    self.changed.emit()
    self._notify("范围需检查：" + "；".join(warnings) + "；可用补点 / 排除点修正"
                 if warnings else "已生成像素蒙版，请检查边缘与漏选", scope="draft")
