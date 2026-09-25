"""Classical color segmentation and single-subject compatibility backends."""

from pathlib import Path
import numpy as np
from PIL import Image, ImageChops
from ..document import empty_mask, raster_mask
from ..masks import encode_bitmap

MODEL_DIR = Path(__file__).resolve().parents[3] / "models"


def _cv():
    try:
        import cv2

        cv2.setNumThreads(2)
        return cv2
    except ImportError:
        raise ValueError(
            "本地选区工具缺少 OpenCV，请运行项目安装脚本更新依赖"
        ) from None


def bitmap_mask(image, label):
    mask = empty_mask()
    mask.update(bitmap=encode_bitmap(image), label=label)
    return mask


def assess(mask, size=(256, 256)):
    pixels = np.asarray(raster_mask(mask, size))
    coverage = float(np.mean(pixels > 0))
    warnings = []
    if coverage < 0.001:
        warnings.append("选区几乎为空，请补选或重新描述目标")
    if coverage > 0.97:
        warnings.append("选区接近全图，请检查背景是否误入")
    return {"coverage": round(coverage * 100, 1), "warnings": warnings}


def refine(image, mask):
    cv = _cv()
    proxy = image.convert("RGB")
    proxy.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    hard = np.asarray(raster_mask(mask, proxy.size)) > 127
    if hard.mean() < 0.001 or hard.mean() > 0.99:
        raise ValueError("请先框出或画出部分目标；空选区和全选不能优化边缘")
    # Coarse boxes need foreground discovery. Existing object contours need
    # their interior preserved while only a narrow boundary band is reconsidered.
    shapes = mask.get("ops", [])
    coarse = (
        "bitmap" not in mask
        and len(shapes) == 1
        and (
            shapes[0]["kind"] == "rect"
            or (
                shapes[0]["kind"] == "polygon"
                and len(shapes[0]["points"]) == 4
                and len({p[0] for p in shapes[0]["points"]}) == 2
                and len({p[1] for p in shapes[0]["points"]}) == 2
            )
        )
    )
    radius = max(3, round(min(proxy.size) * (0.035 if coarse else 0.015)))
    kernel = cv.getStructuringElement(
        cv.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    support = cv.dilate(hard.astype(np.uint8), kernel).astype(bool)
    labels = np.full(hard.shape, cv.GC_BGD, np.uint8)
    labels[support] = cv.GC_PR_BGD
    labels[hard] = cv.GC_PR_FGD
    if not coarse:
        core = cv.erode(hard.astype(np.uint8), kernel).astype(bool)
        labels[core] = cv.GC_FGD
    cv.setRNGSeed(17)
    cv.grabCut(
        np.asarray(proxy),
        labels,
        None,
        np.zeros((1, 65), np.float64),
        np.zeros((1, 65), np.float64),
        3,
        cv.GC_INIT_WITH_MASK,
    )
    selected = (labels == cv.GC_FGD) | (labels == cv.GC_PR_FGD)
    overlap = float(
        np.logical_and(selected, hard).sum()
        / max(1, np.logical_or(selected, hard).sum())
    )
    if not selected.any() or overlap < (0.12 if coarse else 0.55):
        raise ValueError("边缘优化结果与原选区差异过大，已保留原草稿；请补选目标后重试")
    result = bitmap_mask(
        Image.fromarray(selected.astype(np.uint8) * 255), "边缘优化选区"
    )
    result["feather"] = mask["feather"]
    return result, {
        **assess(result),
        "overlap": round(overlap, 3),
        "method": "coarse-box" if coarse else "preserve-interior",
    }


def wand(image, mask, point, tolerance=24, mode="replace"):
    cv = _cv()
    if (
        not isinstance(point, list)
        or len(point) != 2
        or any(type(v) not in (float, int) or not 0 <= v <= 1 for v in point)
    ):
        raise ValueError("魔棒位置无效")
    if (
        type(tolerance) not in (float, int)
        or not 0 <= tolerance <= 100
        or mode not in ("replace", "add", "subtract")
    ):
        raise ValueError("魔棒参数无效")
    proxy = image.convert("RGB")
    proxy.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    w, h = proxy.size
    buffer = np.zeros((h + 2, w + 2), np.uint8)
    cv.floodFill(
        np.asarray(proxy).copy(),
        buffer,
        (min(w - 1, round(point[0] * w)), min(h - 1, round(point[1] * h))),
        0,
        (tolerance,) * 3,
        (tolerance,) * 3,
        4 | cv.FLOODFILL_MASK_ONLY | cv.FLOODFILL_FIXED_RANGE | (255 << 8),
    )
    chosen = Image.fromarray(buffer[1:-1, 1:-1])
    if mode != "replace":
        previous = raster_mask(mask, proxy.size)
        chosen = (
            ImageChops.lighter(previous, chosen)
            if mode == "add"
            else ImageChops.subtract(previous, chosen)
        )
    result = bitmap_mask(chosen, "颜色连续选区")
    return result, assess(result)


_subject_net = None


def subject(image):
    global _subject_net
    from ..plugins import CAPABILITIES

    cap = next(c for c in CAPABILITIES if c.id == "u2net").inspect()
    if not cap["available"]:
        raise ValueError(cap["status"] + "；打开“图像能力”查看本地模型位置")
    cv = _cv()
    if _subject_net is None:
        _subject_net = cv.dnn.readNetFromONNX(str(MODEL_DIR / "u2netp.onnx"))
        _subject_net.setPreferableBackend(cv.dnn.DNN_BACKEND_OPENCV)
        _subject_net.setPreferableTarget(cv.dnn.DNN_TARGET_CPU)
    rgb = image.convert("RGB")
    array = np.asarray(rgb.resize((320, 320), Image.Resampling.LANCZOS)).astype(
        np.float32
    )
    array /= max(float(array.max()), 1)
    array = (
        (array - np.array([0.485, 0.456, 0.406], np.float32))
        / np.array([0.229, 0.224, 0.225], np.float32)
    ).transpose(2, 0, 1)[None]
    _subject_net.setInput(array)
    # u2netp's first exported output is the fused prediction, not a side head.
    output = _subject_net.forward(_subject_net.getUnconnectedOutLayersNames()[0])
    alpha = np.asarray(output)[0, 0]
    if alpha.shape != (320, 320) or not np.isfinite(alpha).all():
        raise ValueError("主体模型输出无效")
    lo, hi = float(alpha.min()), float(alpha.max())
    if hi < 0.05 or hi - lo < 0.001:
        raise ValueError("模型没有找到清晰的主体，请改用框选或文字选区")
    mask = Image.fromarray(np.rint((alpha - lo) / (hi - lo) * 255).astype(np.uint8))
    size = rgb.copy()
    size.thumbnail((1600, 1600))
    result = bitmap_mask(
        mask.resize(size.size, Image.Resampling.LANCZOS), "本地主体选区"
    )
    return result, assess(result)
