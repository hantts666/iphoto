"""Straight foreground colors, shared by cutout export and background checks.

The photo and alpha remain immutable. Recovery is evaluated at native pixels,
with the same surrounding context for both a viewport and the whole export.
"""
from hashlib import sha256
from importlib.util import find_spec
import os

import numpy as np
from PIL import Image, ImageChops

MAX_AREA = 8_000_000
CONTEXT = 96
_cache = None


def available():
    return find_spec('pymatting') is not None


def recovery_box(alpha):
    partial = alpha.point([255 if 0 < v < 255 else 0 for v in range(256)])
    box = partial.getbbox()
    if box is None:
        return None
    box = (max(0, box[0]-CONTEXT), max(0, box[1]-CONTEXT),
           min(alpha.width, box[2]+CONTEXT), min(alpha.height, box[3]+CONTEXT))
    if (box[2]-box[0])*(box[3]-box[1]) > MAX_AREA:
        raise ValueError('去背景串色范围超过800万像素，请分区域处理或关闭去背景串色')
    local = np.asarray(alpha.crop(box))
    if np.count_nonzero(local == 255) < 16 or np.count_nonzero(local == 0) < 16:
        raise ValueError('缺少不透明主体或透明背景参照，无法可靠恢复前景颜色')
    return box


def _recover(rgb, alpha):
    global _cache
    key = (rgb.size, sha256(rgb.tobytes()).digest(), sha256(alpha.tobytes()).digest())
    if _cache and _cache[0] == key:
        return _cache[1].copy()
    os.environ.setdefault('NUMBA_NUM_THREADS', '4')
    from .matting.solver import _enable_numba_cache
    _enable_numba_cache()
    try:
        from pymatting import estimate_foreground_ml
    except ImportError as exc:
        raise ValueError('去背景串色组件未安装，请更新依赖或关闭此选项') from exc
    pixels = np.array(rgb.convert('RGB'))
    a = np.asarray(alpha, dtype=np.float32)/255
    foreground = estimate_foreground_ml(pixels.astype(np.float32)/255, a)
    if foreground.shape != pixels.shape or not np.isfinite(foreground).all():
        raise ValueError('前景颜色恢复失败，原照片和选区保留')
    partial = (a > 0) & (a < 1)
    pixels[partial] = np.rint(np.clip(foreground[partial], 0, 1)*255).astype(np.uint8)
    result = Image.fromarray(pixels)
    _cache = (key, result.copy())
    return result


def color_patch(source, layers, mask, alpha=None):
    if not mask.get('color_recovery'):
        return None
    from .document import raster_mask_cached, render_detail_tile
    alpha = alpha if alpha is not None else raster_mask_cached(mask, source.size)
    box = recovery_box(alpha)
    if box is None:
        return None
    rendered = render_detail_tile(source, layers, box)
    corrected = _recover(rendered.convert('RGB'), alpha.crop(box)).convert('RGBA')
    corrected.putalpha(ImageChops.multiply(rendered.convert('RGBA').getchannel('A'), alpha.crop(box)))
    return box, corrected


def compose_cutout(rendered, alpha, patch=None):
    """Apply recovered straight RGB, then alpha exactly once; zero/opaque RGB is exact."""
    rgba = rendered.convert('RGBA')
    if patch is not None:
        box, colors = patch
        original = rgba.crop(box)
        partial = alpha.crop(box).point([255 if 0 < v < 255 else 0 for v in range(256)])
        adjusted = colors.convert('RGB').convert('RGBA')
        adjusted.putalpha(original.getchannel('A'))
        rgba.paste(Image.composite(adjusted, original, partial), box[:2])
    rgba.putalpha(ImageChops.multiply(rgba.getchannel('A'), alpha))
    return rgba


def background_view(source, layers, mask, mode, rendered, box=None):
    """An opaque black/white check, using native recovery even in a small preview."""
    from .document import raster_mask_cached
    canvas = source.size if box else rendered.size
    view = box or (0, 0, *canvas)
    alpha = raster_mask_cached(mask, canvas).crop(view)
    foreground = compose_cutout(rendered, alpha)
    result = Image.alpha_composite(Image.new('RGBA', rendered.size, mode), foreground)
    patch = color_patch(source, layers, mask)
    if patch:
        native_box, pixels = patch
        mapped = tuple(round(native_box[i]*canvas[i % 2]/source.size[i % 2]) for i in range(4))
        if mapped[0] < mapped[2] and mapped[1] < mapped[3]:
            # Pillow resizes RGBA through premultiplied alpha. Resizing straight
            # RGB and alpha independently would reintroduce background colors.
            pixels = pixels.resize((mapped[2]-mapped[0], mapped[3]-mapped[1]), Image.Resampling.LANCZOS)
            clip = (max(mapped[0], view[0]), max(mapped[1], view[1]),
                    min(mapped[2], view[2]), min(mapped[3], view[3]))
            if clip[0] < clip[2] and clip[1] < clip[3]:
                local = tuple(clip[i]-mapped[i % 2] for i in range(4))
                piece = pixels.crop(local)
                piece = Image.alpha_composite(Image.new('RGBA', piece.size, mode), piece)
                result.paste(piece, (clip[0]-view[0], clip[1]-view[1]))
    return result.convert('RGB')
