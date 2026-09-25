"""Bounded, portable grayscale mask assets. No paths or executable metadata."""

import base64
from functools import lru_cache
from io import BytesIO

from PIL import Image

MAX_MASK_SIDE = 32768
MAX_MASK_PIXELS = 60_000_000
MAX_MASK_BYTES = 16 * 1024 * 1024


@lru_cache(maxsize=8)
def _decode(png, width, height):
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


def validate_bitmap(value):
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
    _decode(value["png"], value["width"], value["height"])
    return dict(value)


def decode_bitmap(value, size):
    image = _decode(value["png"], value["width"], value["height"])
    if image.size != size and value.get("sampling") == "alpha":
        # Interpolate continuous coverage without introducing ringing or
        # modifying the protected zero support at the export resolution.
        support = image.point([0] + [255] * 255).resize(size, Image.Resampling.NEAREST)
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
    image = image.convert("L")
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
    return {
        "png": base64.b64encode(data).decode("ascii"),
        "width": image.width,
        "height": image.height,
        **({"sampling": sampling} if sampling else {}),
    }
