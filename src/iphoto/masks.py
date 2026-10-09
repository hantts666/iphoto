"""Bounded, portable grayscale mask assets. No paths or executable metadata."""

import base64
from collections import OrderedDict
from hashlib import sha256
from io import BytesIO

from PIL import Image

MAX_MASK_SIDE = 32768
MAX_MASK_PIXELS = 60_000_000
MAX_MASK_BYTES = 16 * 1024 * 1024
MAX_DECODE_CACHE_BYTES = 64 * 1024 * 1024
_DECODE_CACHE = OrderedDict()
_DECODE_CACHE_BYTES = 0
_VALIDATED = OrderedDict()
MAX_VALIDATED_KEYS = 128


def clear_decode_cache():
    global _DECODE_CACHE_BYTES
    _DECODE_CACHE.clear()
    _DECODE_CACHE_BYTES = 0


def _cache_key(png, width, height):
    try:
        return (sha256(png.encode("ascii")).digest(), width, height)
    except UnicodeEncodeError as exc:
        raise ValueError("蒙版 PNG 无效") from exc


def _remember_valid(key):
    _VALIDATED[key] = None
    _VALIDATED.move_to_end(key)
    if len(_VALIDATED) > MAX_VALIDATED_KEYS:
        _VALIDATED.popitem(last=False)


def _decode_uncached(png, width, height):
    try:
        data = base64.b64decode(png, validate=True)
        if len(data) > MAX_MASK_BYTES:
            raise ValueError("蒙版资源过大")
        with Image.open(BytesIO(data)) as image:
            if (
                image.format != "PNG"
                or image.mode != "L"
                or image.size != (width, height)
            ):
                raise ValueError("蒙版必须是尺寸匹配的灰度 PNG")
            image.load()
            return image.copy()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("蒙版 PNG 无效") from exc


def _decode(png, width, height):
    global _DECODE_CACHE_BYTES
    key = _cache_key(png, width, height)
    cached = _DECODE_CACHE.get(key)
    if cached is not None:
        _DECODE_CACHE.move_to_end(key)
        return cached[0]
    image = _decode_uncached(png, width, height)
    _remember_valid(key)
    weight = width * height + len(png)
    if weight <= MAX_DECODE_CACHE_BYTES:
        while _DECODE_CACHE and _DECODE_CACHE_BYTES + weight > MAX_DECODE_CACHE_BYTES:
            _, (_, old_weight) = _DECODE_CACHE.popitem(last=False)
            _DECODE_CACHE_BYTES -= old_weight
        _DECODE_CACHE[key] = (image, weight)
        _DECODE_CACHE_BYTES += weight
    return image


def validate_bitmap(value, *, cache_decoded=False):
    if not isinstance(value, dict) or set(value) not in (
        {"png", "width", "height"},
        {"png", "width", "height", "sampling"},
    ):
        raise ValueError("蒙版资源结构无效")
    if "sampling" in value and value["sampling"] != "alpha":
        raise ValueError("蒙版采样方式无效")
    for key in ("width", "height"):
        if type(value[key]) is not int or not 1 <= value[key] <= MAX_MASK_SIDE:
            raise ValueError("蒙版边长超过 32768 像素限制")
    if value["width"] * value["height"] > MAX_MASK_PIXELS:
        raise ValueError("蒙版超过 6000 万像素限制")
    if (
        not isinstance(value["png"], str)
        or len(value["png"]) > MAX_MASK_BYTES * 4 // 3 + 4
    ):
        raise ValueError("蒙版资源过大")
    key = _cache_key(value["png"], value["width"], value["height"])
    if cache_decoded:
        # A foreground result will be rasterized immediately. Keep that one
        # validated decode in the bounded cache instead of decoding it twice.
        # Ordinary project validation still retains no pixel data.
        _decode(value["png"], value["width"], value["height"])
    elif key not in _VALIDATED:
        _decode_uncached(value["png"], value["width"], value["height"])
    _remember_valid(key)
    return dict(value)


def decode_bitmap(value, size):
    image = _decode(value["png"], value["width"], value["height"])
    if image.size != size and value.get("sampling") == "alpha":
        # A smaller display pixel covers several source pixels. Gating this
        # average by one nearest pixel can delete an entire subpixel strand.
        # Bilinear's nonnegative filter retains coverage without ringing;
        # native alpha is still returned exactly, including protected holes.
        if size[0] <= image.width and size[1] <= image.height:
            return image.resize(size, Image.Resampling.BILINEAR)
        # Enlargement keeps the existing conservative zero support. A mask
        # saved below source resolution must not grow into excluded pixels.
        support = image.resize(size, Image.Resampling.NEAREST).point([0] + [255] * 255)
        resized = image.resize(size, Image.Resampling.BILINEAR)
        resized.paste(0, mask=support.point(lambda v: 255 - v))
        return resized
    # Nearest preserves the defined zero support when scaled for full-size export.
    return (
        image.resize(size, Image.Resampling.NEAREST)
        if image.size != size
        else image.copy()
    )


def encode_bitmap(image, *, sampling=None, preserve_resolution=False):
    if sampling not in (None, "alpha"):
        raise ValueError("蒙版采样方式无效")
    image = image if preserve_resolution and image.mode == "L" else image.convert("L")
    if preserve_resolution:
        if max(image.size) > MAX_MASK_SIDE or image.width * image.height > MAX_MASK_PIXELS:
            raise ValueError("蒙版尺寸超过原图处理限制，未缩小保存")
    else:
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()
    if len(data) > MAX_MASK_BYTES:
        raise ValueError("蒙版资源过大，请降低选区分辨率")
    result = {
        "png": base64.b64encode(data).decode("ascii"),
        "width": image.width,
        "height": image.height,
        **({"sampling": sampling} if sampling else {}),
    }
    _remember_valid(_cache_key(result["png"], image.width, image.height))
    return result
