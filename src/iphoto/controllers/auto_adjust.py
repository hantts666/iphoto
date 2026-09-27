"""Local auto balance from worker tone statistics. Deterministic, not AI."""

import math

from ..engine import LABELS, RANGES, Recipe


def _enc2lin(value):
    value = min(1.0, max(0.0, value))
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def compute(stats, current, locked=()):
    """Return recipe changes (absolute values) for one auto-balance pass."""
    if not stats:
        raise ValueError("预览统计尚未就绪，请稍候再试")
    changes = {}
    mid, low, high = stats["mid"], stats["low"], stats["high"]
    lin_mid = _enc2lin(mid)
    # Target band for encoded midtone; skip near-black images to avoid
    # amplifying sensor noise, skip well-exposed images entirely.
    if 0.004 < lin_mid and not 0.15 <= lin_mid <= 0.32:
        delta = max(-1.0, min(1.0, math.log2(0.22 / lin_mid) * 0.7))
        if abs(delta) > 0.05:
            changes["exposure"] = round(current.get("exposure", 0) + delta, 2)
    span = high - low
    if span < 0.8:
        add = (0.85 - span) * 60
        if add > 3:
            changes["contrast"] = round(current.get("contrast", 0) + add)
    if mid < 0.42 and low < 0.12:
        changes["shadows"] = max(current.get("shadows", 0), 24)
    if high > 0.96 and mid > 0.55:
        changes["highlights"] = min(current.get("highlights", 0), -24)
    red, green, blue = stats["means"]
    if red > 0.02 and blue > 0.02:
        warmth = 35 * math.log2(blue / red) * 0.5 * 0.6
        if abs(warmth) > 2:
            changes["warmth"] = round(current.get("warmth", 0) + warmth)
    average_rb = (red + blue) / 2
    if green > 0.02 and average_rb > 0.02:
        tint = 25 * math.log2(green / average_rb) * 0.5 * 0.6
        if abs(tint) > 2:
            changes["tint"] = round(current.get("tint", 0) + tint)
    result = {}
    for key, value in changes.items():
        if key in locked:
            continue
        lo, hi = RANGES[key]
        value = min(hi, max(lo, value))
        if abs(value - current.get(key, 0)) > 1e-9:
            result[key] = value
    return result


def autoAdjust(self):
    if not self._can_edit() or self.activeIsGroup:
        return
    try:
        changes = compute(self._stats, self._recipe, self._locked)
    except ValueError as exc:
        return self._notify(str(exc), True)
    if not changes:
        return self._notify("曝光与色彩已较均衡，或相关参数已锁定；无需自动调整")
    data = dict(self._recipe)
    data.update(changes)
    self._recipe = Recipe.from_dict(data).to_dict()
    summary = "、".join(
        f"{LABELS[k]} {v:+.2f} EV" if k == "exposure" else f"{LABELS[k]} {v:+.0f}"
        for k, v in changes.items()
    )
    self._summary = "按直方图统计自动调整：" + summary + "。可继续微调或 Ctrl+Z 撤销。"
    self._message("event", "已按直方图统计自动调整：" + summary + "。")
    self._commit()
    self._change()
    self._notify("自动调整：" + summary)
