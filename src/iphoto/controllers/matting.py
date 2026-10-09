"""Transactional edge refinement; the image worker owns all solver state."""

from copy import deepcopy

from ..document import empty_mask, validate_mask


def paint(self, points, radius, *, profile='general'):
    if self.busy or not self.hasImage or not self.hasSelectionDraft or self.hasRegionDraft:
        return False
    if not any(c["id"] == "details" and c["available"] for c in self.imageCapabilities):
        self._notify("AI 细节模型未配置，请在图像能力中查看", True)
        return False
    if profile not in ('general','hair') or (profile=='hair' and not any(c['id']=='hair_details' and c['available'] for c in self.imageCapabilities)):
        self._notify('人物发丝模型未配置，请在图像能力中查看',True)
        return False
    try:
        stroke = {"points": deepcopy(points), "radius": radius}
        validate_mask({**empty_mask(), "ops": [{"kind": "brush", "mode": "add", **stroke}]})
    except (ValueError, TypeError) as exc:
        self._notify(str(exc), True)
        return False
    self._status = "AI 正在结合人像外缘与头发分区处理笔触…可取消" if profile=='hair' else "AI 正在细化涂抹区域的透明度，其他范围保留…可随时取消"
    return self._request("matte", mask=deepcopy(self._candidate), method="hair" if profile=='hair' else "neural", stroke=stroke,
                         points=deepcopy(self._pixel_points)) is not False


def start(self, radius, *, method="classic"):
    if self.busy or not self.hasImage:
        return
    neural = method == "neural"
    available = (any(c["id"] == "details" and c["available"] for c in self.imageCapabilities)
                 if neural else self.matteAvailable)
    if not available:
        return self._notify(
            "AI 细节模型未配置，请在扩展 → 图像能力中查看" if neural
            else "透明边缘组件未安装，请运行 scripts/setup.ps1 更新依赖", True
        )
    if type(radius) is not int or not 1 <= radius <= 64:
        return self._notify("边缘范围应为 1～64 像素", True)
    if self._region_candidate:
        mask = self._region_candidate["layers"][self._region_index]["mask"]
    else:
        if self._candidate is None:
            self.beginSelection("current")
        mask = self._candidate
    self._status = ("正在准备 AI 边缘细化…保留当前范围和提示点，可随时取消" if neural
                    else "正在按原图分辨率细化边缘…可随时取消，大图需要更长时间")
    self._request("matte", mask=deepcopy(mask), radius=radius, method=method,
                  points=deepcopy(self._pixel_points) if neural and not self._region_candidate else [])


def cancel(self):
    if self.matteBusy:
        owned_channel = any(job and 'channel_token' in job for job in (self._matte_active, self._matte_pending))
        self._stop_matte()
        if owned_channel:
            self._channel_mask.cancelled()
        self._status = "已取消边缘细化；原选区保留，可继续编辑"
        self.changed.emit()


def complete(self, result, *, points=None):
    mask, quality = result["mask"], result["quality"]
    local = quality.get("local_refinement", False)
    if local and quality.get("changed_pixels") == 0:
        return self._notify("本次透明细化没有改变范围；可扩大问题区域或改用补选 / 擦除")
    if self._region_candidate:
        self._region_candidate["layers"][self._region_index]["mask"] = mask
        self._mark_dirty()
        self._mask_url = ""
        self._generation += 1
        self._schedule_render()
    else:
        self._set_candidate(mask)
        if points is not None:
            self._pixel_points = deepcopy(points)
            self._pixel_hint = deepcopy(self._candidate)
    if quality.get('channel_mask'):
        self.setMaskView('white')
        from ..matting.channels import CHANNEL_NAMES
        method = 'AI 透明边缘' if quality['tiles'] else '通道透明度'
        self._selection_quality = f"{CHANNEL_NAMES[quality['channel']]}通道 · {method} · {quality['elapsed_ms']/1000:.1f}s"
        if quality.get('native_detail'):
            self._selection_quality += ' · 原像素细纹理'
        if quality.get('color_recovery'):
            self._selection_quality += ' · 透明输出去背景串色'
        if quality.get('warnings'):
            self._selection_quality += ' · ' + '；'.join(quality['warnings'])
        detail=('\n已从整张照片建立范围。同色背景可能一起选中，可用画笔补选／擦除，再用 AI 修细节；可直接调整或导出透明 PNG。'
                if quality.get('whole') else '\n可切换白底或黑底检查；请放大检查发丝、孔洞和透明内部。')
        if quality.get('color_recovery'):
            detail+='透明 PNG 使用相同的前景颜色恢复。'
        self._message('assistant', self._selection_quality + detail,
                      state='draft', origin={'mode':'selection','model':quality['backend']})
        self._notify('通道抠图已计算，正在显示实际白底效果；请检查边缘，不合适可以撤销')
        return
    neural = quality.get("backend") == "ViTMatte-S · ONNX"
    self._selection_quality = (f"{'人物发丝' if quality.get('hair_matting') else '局部 AI 透明度'} · 已修改 {quality['changed_pixels']:,} 个像素 · {quality['elapsed_ms']/1000:.1f}s" if local else (
        ("AI 原图边缘 · " if neural else "")
        + f"连续透明度 · {quality['partial_pixels']:,} 个过渡像素"
        f" · {quality['elapsed_ms'] / 1000:.1f}s"
    ))
    if quality.get("warnings"):
        self._selection_quality += " · " + "；".join(quality["warnings"])
    self._status = "透明边缘已就绪；可查看黑白透明度或调色效果，确认后再输出"
    self._message(
        "assistant",
        self._selection_quality + ("\n涂抹区域外的透明度保持；照片颜色未改写，可逐笔撤销。" if local else "\n已取消额外羽化；透明度已细化，未改写照片颜色。"),
        state="region_draft" if self._region_candidate else "draft",
        origin={"mode": "selection", "model": quality['backend'] if quality.get('hair_matting') else "ViTMatte-S（本地 AI）" if neural else "PyMatting（本地）"},
    )
    self._notify("透明边缘已细化，请对照调色效果；不合适可以撤销")
    self.changed.emit()
