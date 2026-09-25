"""Transactional edge refinement; the image worker owns all solver state."""

from copy import deepcopy


def start(self, radius):
    if self.busy or not self.hasImage:
        return
    if not self.matteAvailable:
        return self._notify(
            "透明边缘组件未安装，请运行 scripts/setup.ps1 更新依赖", True
        )
    if not 1 <= radius <= 64:
        return self._notify("边缘范围应为 1～64 像素", True)
    if self._region_candidate:
        mask = self._region_candidate["layers"][self._region_index]["mask"]
    else:
        if self._candidate is None:
            self.beginSelection("current")
        mask = self._candidate
    self._status = "正在按原图分辨率细化边缘…大图需要更长时间"
    self._request("matte", mask=deepcopy(mask), radius=radius)


def cancel(self):
    if self.matteBusy:
        self._active["cancelled"] = True
        self._status = "已取消接收边缘结果，等待本地计算释放；原选区保留"
        self.changed.emit()


def complete(self, result):
    mask, quality = result["mask"], result["quality"]
    if self._region_candidate:
        self._region_candidate["layers"][self._region_index]["mask"] = mask
        self._mark_dirty()
        self._mask_url = ""
        self._generation += 1
        self._schedule_render()
    else:
        self._set_candidate(mask)
    self._selection_quality = (
        f"连续透明度 · {quality['partial_pixels']:,} 个过渡像素"
        f" · {quality['elapsed_ms'] / 1000:.1f}s"
    )
    self._status = "透明边缘已就绪；可查看黑白透明度或调色效果，确认后再输出"
    self._message(
        "assistant",
        self._selection_quality + "\n已取消额外羽化；透明度已细化，未改写照片颜色。",
        state="region_draft" if self._region_candidate else "draft",
        origin={"mode": "selection", "model": "PyMatting（本地）"},
    )
    self._notify("透明边缘已细化，请对照调色效果；不合适可以撤销")
    self.changed.emit()
