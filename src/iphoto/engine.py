"""Deterministic, strip-based editing of color-managed JPEG/PNG photographs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageCms, ImageFilter, ImageOps
from .storage import atomic_output

ENGINE_VERSION = "1.7.0-original-alpha"
SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
RANGES = {
    "exposure": (-2.0, 2.0),
    "contrast": (-60, 60),
    "highlights": (-80, 80),
    "shadows": (-80, 80),
    "warmth": (-60, 60),
    "saturation": (-60, 60),
    "tint": (-60, 60),
    "vibrance": (-60, 60),
    "whites": (-60, 60),
    "blacks": (-60, 60),
    "sharpness": (0, 100),
    "softness": (0, 100),
}
LABELS = {
    "exposure": "曝光",
    "contrast": "对比度",
    "highlights": "高光",
    "shadows": "阴影",
    "warmth": "冷暖",
    "saturation": "饱和度",
    "tint": "色偏",
    "vibrance": "自然饱和度",
    "whites": "白色色阶",
    "blacks": "黑色色阶",
    "sharpness": "锐化",
    "softness": "柔化",
}
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
_LEVELS = np.arange(256, dtype=np.float32) / 255
LINEAR_LUT = np.where(
    _LEVELS <= 0.04045, _LEVELS / 12.92, ((_LEVELS + 0.055) / 1.055) ** 2.4
).astype(np.float32)


@dataclass(frozen=True)
class Recipe:
    exposure: float = 0.0
    contrast: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    warmth: float = 0.0
    saturation: float = 0.0
    tint: float = 0.0
    vibrance: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    sharpness: float = 0.0
    softness: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "Recipe":
        if not isinstance(data, dict) or set(data) - set(RANGES):
            raise ValueError("参数包含不支持的字段")
        clean = {}
        for key, value in data.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{key} 必须是有限数值")
            lo, hi = RANGES[key]
            if not lo <= value <= hi:
                raise ValueError(f"{key} 超出允许范围 {lo} ~ {hi}")
            clean[key] = float(value)
        return cls(**clean)

    def to_dict(self):
        return asdict(self)


@dataclass
class Source:
    path: Path
    image: Image.Image
    digest: str
    exif: bytes
    warning: str = ""


def file_hash(path: Path) -> str:
    h = sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_source(path: str | Path) -> Source:
    path = Path(path).resolve(strict=True)
    warning = ""
    with Image.open(path) as raw:
        if raw.format not in {"JPEG", "PNG"}:
            raise ValueError("当前原型支持 JPEG 和 PNG，RAW / HEIC 尚未接入")
        if raw.width * raw.height > 60_000_000:
            raise ValueError("当前原型支持不超过 6000 万像素的照片")
        if raw.mode in {"I", "I;16", "F"}:
            raise ValueError("当前原型暂不支持高位深 PNG，请先转换为 8-bit RGB")
        exif = raw.getexif()
        kept = Image.Exif()
        # Camera/date metadata only. Do not copy GPS, MakerNotes, stale thumbnails or orientation.
        for tag in (271, 272, 306, 315, 33432):
            if tag in exif:
                kept[tag] = exif[tag]
        oriented = ImageOps.exif_transpose(raw)
        has_alpha = "A" in oriented.getbands() or "transparency" in raw.info
        alpha = oriented.convert("RGBA").getchannel("A") if has_alpha else None
        icc = raw.info.get("icc_profile")
        if icc:
            try:
                profile = ImageCms.ImageCmsProfile(BytesIO(icc))
                rgb_input = (
                    oriented
                    if oriented.mode in {"RGB", "CMYK", "LAB"}
                    else oriented.convert("RGB")
                )
                rgb = ImageCms.profileToProfile(
                    rgb_input, profile, ImageCms.createProfile("sRGB"), outputMode="RGB"
                )
            except (ImageCms.PyCMSError, OSError, ValueError) as exc:
                raise ValueError(
                    "图片的 ICC 色彩配置无法转换，请先转换为 sRGB"
                ) from exc
        else:
            rgb = oriented.convert("RGB")
            if raw.mode == "CMYK":
                warning = "此 CMYK 图片没有 ICC，颜色仅作近似显示"
        if alpha is not None:
            rgb.putalpha(alpha)
        image = rgb.copy()
    return Source(path, image, file_hash(path), kept.tobytes(), warning)


def preview(image: Image.Image, edge=1600) -> Image.Image:
    result = image.copy()
    result.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return result


def _transform_linear(rgb, recipe):
    """Reference float32 math; receives linear RGB and returns encoded sRGB."""
    if recipe.warmth:
        # A relative creative warm/cool control, not calibrated RAW Kelvin.
        gain = np.exp2(np.array([1, 0, -1], np.float32) * (recipe.warmth / 100))
        gain /= np.dot(gain, LUMA)
        rgb *= gain
    if recipe.exposure:
        rgb *= 2.0**recipe.exposure
    if recipe.tint:
        gain = np.exp2(np.array([0.5, -1, 0.5], np.float32) * (recipe.tint / 100))
        rgb *= gain / np.dot(gain, LUMA)
    y = np.sum(rgb * LUMA, axis=-1, keepdims=True)
    if recipe.whites or recipe.blacks:
        rgb *= np.exp2(
            recipe.whites / 65 * np.clip(y, 0, 1) ** 4
            + recipe.blacks / 65 * np.clip(1 - y / 0.3, 0, 1) ** 4
        )
    if recipe.shadows or recipe.highlights:
        shadow_weight = np.clip(1.0 - y / 0.55, 0, 1) ** 2
        hi_weight = np.clip((y - 0.18) / 0.82, 0, 1) ** 2
        rgb *= np.exp2(
            (recipe.shadows / 75) * shadow_weight + (recipe.highlights / 90) * hi_weight
        )
    if recipe.contrast:
        y = np.sum(rgb * LUMA, axis=-1, keepdims=True)
        target = 0.18 * np.power(np.maximum(y, 1e-7) / 0.18, 1 + recipe.contrast / 160)
        rgb *= target / np.maximum(y, 1e-7)
    if recipe.saturation:
        gray = np.sum(rgb * LUMA, axis=-1, keepdims=True)
        rgb = gray + (rgb - gray) * (1 + recipe.saturation / 100)
    if recipe.vibrance:
        gray = np.sum(rgb * LUMA, axis=-1, keepdims=True)
        spread = (
            rgb.max(axis=-1, keepdims=True) - rgb.min(axis=-1, keepdims=True)
        ) / np.maximum(rgb.max(axis=-1, keepdims=True), 1e-6)
        rgb = gray + (rgb - gray) * (
            1 + recipe.vibrance / 100 * (1 - np.clip(spread, 0, 1))
        )
    rgb = np.clip(rgb, 0, 1)
    return np.where(
        rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055
    )


@lru_cache(maxsize=12)
def _color_lut(recipe):
    # Compile pointwise operations once; the C implementation applies this same
    # 65^3 interpolated transform to both previews and original-size exports.
    axis = np.linspace(0, 1, 65, dtype=np.float32)
    blue, green, red = np.meshgrid(axis, axis, axis, indexing="ij")
    encoded = np.stack((red, green, blue), axis=-1)
    linear = np.where(
        encoded <= 0.04045, encoded / 12.92, ((encoded + 0.055) / 1.055) ** 2.4
    )
    table = _transform_linear(linear, recipe)
    return ImageFilter.Color3DLUT(65, table, channels=3, target_mode="RGB")


def render(image: Image.Image, recipe: Recipe, strip_height=192) -> Image.Image:
    """Versioned LUT renderer. The reference renderer below checks interpolation error."""
    if not any(recipe.to_dict().values()):
        return image.copy()
    result = image.convert("RGB").filter(_color_lut(recipe))
    # LUT interpolation near a clipped channel is inaccurate when tint and
    # adaptive saturation interact. Correct only transition pixels using the
    # float reference; fully clipped values remain cheap and within LUT tolerance.
    pixels = np.asarray(image.convert("RGB"))
    corrected = np.asarray(result).copy()
    low_transition = 64 if recipe.vibrance and recipe.saturation > 0 else 40
    for top in range(0, image.height, strip_height):
        region = corrected[top : top + strip_height]
        boundary = np.any(
            ((region > 0) & (region < low_transition))
            | ((region > 240) & (region < 255)),
            axis=-1,
        )
        if np.any(boundary):
            exact = _transform_linear(
                LINEAR_LUT[pixels[top : top + strip_height][boundary]].copy(), recipe
            )
            region[boundary] = np.rint(exact * 255).clip(0, 255).astype(np.uint8)
    result = Image.fromarray(corrected)
    result = _detail(result, recipe)
    if image.mode == "RGBA":
        result.putalpha(image.getchannel("A"))
    return result


def _detail(image, recipe):
    radius = max(image.size) / 1600
    if recipe.softness:
        softened = image.filter(ImageFilter.GaussianBlur(max(0.25, radius * 3)))
        image = Image.blend(image, softened, recipe.softness / 100)
    if recipe.sharpness:
        image = image.filter(
            ImageFilter.UnsharpMask(
                radius=max(0.3, radius),
                percent=round(recipe.sharpness * 2),
                threshold=3,
            )
        )
    return image


def render_reference(
    image: Image.Image, recipe: Recipe, strip_height=192
) -> Image.Image:
    if not any(recipe.to_dict().values()):
        return image.copy()
    pixels = np.asarray(image.convert("RGB"))
    output = np.empty_like(pixels)
    for top in range(0, image.height, strip_height):
        encoded = _transform_linear(
            LINEAR_LUT[pixels[top : top + strip_height]].copy(), recipe
        )
        output[top : top + strip_height] = (
            np.rint(encoded * 255).clip(0, 255).astype(np.uint8)
        )
    result = Image.fromarray(output)
    result = _detail(result, recipe)
    if image.mode == "RGBA":
        result.putalpha(image.getchannel("A"))
    return result


def histogram(image: Image.Image) -> list[float]:
    small = preview(image.convert("RGB"), 400)
    y = np.asarray(small, dtype=np.float32) @ LUMA
    bins = np.histogram(y, bins=64, range=(0, 256))[0].astype(float)
    bins = np.log1p(bins)
    return (bins / max(float(bins.max()), 1)).round(4).tolist()


PRESETS = {
    "natural": {
        "exposure": 0.14,
        "shadows": 28,
        "highlights": -24,
        "contrast": 8,
        "saturation": 8,
    },
    "warm": {
        "exposure": 0.08,
        "shadows": 16,
        "highlights": -18,
        "contrast": 6,
        "warmth": 22,
        "saturation": -6,
    },
    "cool": {
        "exposure": -0.08,
        "shadows": 20,
        "highlights": -30,
        "contrast": 15,
        "warmth": -18,
        "saturation": -16,
    },
}


def interpret_local(text: str, current: Recipe, locked=()) -> tuple[Recipe, str]:
    """Explicit keyword rules, NOT an AI/VLM. Never claim spatial understanding."""
    text = text.strip().lower()
    if not text:
        raise ValueError("先写下你希望怎样调整这张照片")
    if any(
        k in text
        for k in ("移除", "去掉", "换天", "换背景", "瘦脸", "磨皮", "祛痘", "扩图")
    ):
        raise ValueError(
            "这项要求涉及局部修复或内容修改，当前引擎只支持已有选区内的参数调整，尚未应用任何变化。"
        )
    if re.search(
        r"(?:不要|别|无需|不用|不想)(?:再)?(?:提亮|变亮|变暗|变暖|变冷|增加饱和)", text
    ):
        raise ValueError(
            "本地关键词规则无法可靠理解这类否定要求，请用滑杆调整，或描述希望增加的效果。"
        )
    data = current.to_dict()
    changes = {}
    if any(k in text for k in ("自然", "通透", "优化", "清晰", "清透", "natural")):
        changes.update(PRESETS["natural"])
    if any(k in text for k in ("亮一点", "提亮", "太暗", "明亮", "bright")):
        changes.update(exposure=min(data["exposure"] + 0.28, 1.2), shadows=28)
    if any(k in text for k in ("暗一点", "压暗", "太亮", "darken")):
        changes["exposure"] = max(data["exposure"] - 0.25, -1.2)
    if any(k in text for k in ("阴影", "暗部", "shadow")):
        changes["shadows"] = 36
    if any(k in text for k in ("高光", "过曝", "天空", "highlight", "sky")):
        changes["highlights"] = -38
    if any(k in text for k in ("暖", "金色", "日落", "warm")):
        changes["warmth"] = 20
    if any(k in text for k in ("冷", "偏黄", "去黄", "cool")):
        changes["warmth"] = -18
    if any(k in text for k in ("鲜艳", "丰富", "浓郁", "vivid")):
        changes["saturation"] = 22
    if any(k in text for k in ("淡", "低饱和", "胶片", "克制", "柔和", "muted")):
        changes.update(saturation=-20, contrast=-8, shadows=18, highlights=-20)
    if any(k in text for k in ("层次", "对比", "contrast")):
        changes["contrast"] = 16
    if any(k in text for k in ("黑白", "去色", "monochrome")):
        raise ValueError("当前原型仅支持有限的颜色调整，完整黑白与高级效果将在后续加入")
    if not changes:
        raise ValueError(
            "本地规则暂未识别这段描述。可以试试“提亮暗部、压低高光、色调暖一点”；云端 AI 尚未连接。"
        )
    changes = {key: value for key, value in changes.items() if key not in locked}
    if not changes:
        return current, "相关参数已由你手动调整并锁定；重置后可重新应用描述。"
    data.update(changes)
    summary = "、".join(
        f"{LABELS[k]} {v:+.2f} EV" if k == "exposure" else f"{LABELS[k]} {v:+.0f}"
        for k, v in changes.items()
    )
    note = "按关键词调整当前选区：" + summary + "。"
    if any(k in text for k in ("脸", "人物", "背景", "天空", "眼", "皮肤")):
        note += " 本地规则不识别目标，只按当前图层已有选区调整。"
    return Recipe.from_dict(data), note


def validate_export_target(source: Source, target: str | Path) -> Path:
    target = Path(target).resolve()
    if target == source.path or (
        target.exists() and os.path.samefile(target, source.path)
    ):
        raise ValueError("请另存为新文件，不能覆盖原图")
    if target.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("导出文件名请使用 .jpg 或 .png 后缀")
    if target.exists():
        raise ValueError("目标文件已存在，请换一个文件名")
    if not target.parent.is_dir():
        raise ValueError("导出目录不存在，请选择可用目录")
    return target


def export_image(
    source: Source, recipe: Recipe, target: str | Path, rendered=None, *, jpeg_quality=100
) -> Path:
    target = validate_export_target(source, target)
    result = rendered if rendered is not None else render(source.image, recipe)
    if result.size != source.image.size:
        raise ValueError("导出尺寸与原图不一致，已停止导出")
    if type(jpeg_quality) is not int or not 80 <= jpeg_quality <= 100:
        raise ValueError("JPEG 质量应为 80～100")
    fmt = "PNG" if target.suffix.lower() == ".png" else "JPEG"
    kwargs = {"icc_profile": SRGB_PROFILE}
    if fmt == "JPEG":
        if result.mode == "RGBA":
            base = Image.new("RGB", result.size, "white")
            base.paste(result, mask=result.getchannel("A"))
            result = base
        kwargs.update(quality=jpeg_quality, subsampling=0, exif=source.exif)
    with atomic_output(target) as file:
        result.save(file, format=fmt, **kwargs)
    return target


def save_recipe(path: Path, source: Source, recipe: Recipe):
    payload = {
        "schema_version": "0.1",
        "engine_version": ENGINE_VERSION,
        "source": str(source.path),
        "source_sha256": source.digest,
        "recipe": recipe.to_dict(),
    }
    with path.open("x", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
