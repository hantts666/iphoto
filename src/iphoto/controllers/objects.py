"""Objects actions. ``self`` is the owning Editor, passed explicitly."""

from ..scene import combine_masks
from . import pixel_selections


def _scene_key(self):
    return (
        self._sha,
        self._ai.settings.provider,
        self._ai.settings.base_url,
        self._ai.settings.model,
    )


def analyzeScene(self, refresh=False):
    if not self.hasImage or self.busy or self.hasRegionDraft:
        return
    if not refresh and self._scene.restore(self._scene_key()):
        self.changed.emit()
        return self._notify("已载入这张照片的元素清单，无需再次调用 AI")
    self.sendMessage(
        "分析画面中的主要可见元素，包含可见的天空、前景和背景，按类别列出可以独立选择的对象。",
        "scene",
    )


def selectByDescription(self, text):
    if not self.hasImage or self.busy or self.hasRegionDraft:
        return
    if not 1 <= len(text.strip()) <= 4000:
        return self._notify("请输入 1～4000 字的选区目标", True)
    if not self._ai.ready:
        return self.sendMessage(text, "selection")
    if not self._scene.catalog and not self._scene.restore(self._scene_key()):
        self._scene_followup = text.strip()
        self.analyzeScene(False)
    else:
        self.sendMessage(text, "targets")


def checkSceneObject(self, lid, checked):
    if self.busy or self.hasRegionDraft:
        return
    if lid not in {o["id"] for o in (self._scene.catalog or {}).get("objects", [])}:
        return
    if checked:
        self._scene.selected.add(lid)
    else:
        self._scene.selected.discard(lid)
    self.changed.emit()


def checkSceneCategory(self, category, checked):
    if self.busy or self.hasRegionDraft:
        return
    for obj in (self._scene.catalog or {}).get("objects", []):
        if obj["category"] == category:
            if checked:
                self._scene.selected.add(obj["id"])
            else:
                self._scene.selected.discard(obj["id"])
    self.changed.emit()


def clearObjectChecks(self):
    if not self.busy:
        self._scene.selected.clear()
        self.changed.emit()


def _objects_mask(self, ids, mode="replace", base=None):
    masks = [
        self._scene.precise.get(o["id"], {}).get("mask", o["mask"])
        for o in (self._scene.catalog or {}).get("objects", [])
        if o["id"] in ids
    ]
    size = (self._width, self._height)
    result = combine_masks(masks, size, base, mode)
    names = [o["name"] for o in self._scene.catalog["objects"] if o["id"] in ids]
    label = "对象 · " + "、".join(names)
    if base and mode != "replace":
        label = (
            base["label"]
            + {"add": " + ", "subtract": " - ", "intersect": " ∩ "}[mode]
            + "、".join(names)
        )
    result["label"] = label[:200]
    return result


def combineObjects(self, mode):
    if self.busy or self.hasRegionDraft or not self.hasImage:
        return
    if not self._scene.selected:
        return self._notify("请先勾选一个或多个画面元素")
    if mode != "replace" and self._candidate is None:
        return self._notify("请先新建选区，再添加、减去或相交")
    try:
        pixel_selections.select_objects(self, self._scene.selected, mode)
    except ValueError as exc:
        self._notify(str(exc), True)


def objectAt(self, x, y):
    return self._scene.hit(x, y)


def clickObject(self, point, mode):
    if self.busy or self.hasRegionDraft or not self.hasImage:
        return
    if not self._scene.catalog:
        return self._notify("请先分析画面，随后可点选识别到的对象")
    if len(point) != 2:
        return
    lid = self._scene.hit(*point)
    if not lid:
        return self._notify("这里没有已识别的对象；可改用套索、框选或重新分析画面")
    if mode == "subtract" and self._candidate is None:
        return self._notify("先选择目标，再按 Alt 减去对象")
    self._scene.selected = {lid}
    pixel_selections.select_objects(self, [lid], mode)
