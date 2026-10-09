"""Portable, bounded generated pixels; composited strictly through the layer mask."""

import base64
from functools import lru_cache
from hashlib import sha256
from io import BytesIO

from PIL import Image

MAX_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 2048 * 2048


@lru_cache(maxsize=2)
def _decode(png):
    try:
        data = base64.b64decode(png, validate=True)
        if len(data) > MAX_BYTES:
            raise ValueError('生成图像资源过大')
        with Image.open(BytesIO(data)) as image:
            if (image.format != 'PNG' or image.mode not in ('RGB', 'RGBA')
                    or max(image.size) > 32768 or image.width * image.height > MAX_PIXELS):
                raise ValueError('图像内容必须是有界RGB或RGBA PNG')
            image.load()
            return image.copy()
    except (OSError, TypeError, Image.DecompressionBombError) as exc:
        raise ValueError('生成图像资源无效') from exc


def validate_patch(value):
    fields = {'png', 'canvas_size', 'box', 'sha256'}
    if not isinstance(value, dict) or set(value) not in (fields, fields | {'compositing'}):
        raise ValueError('生成图像结构无效')
    rgba = 'compositing' in value
    if rgba and value['compositing'] != 'replace_rgba':
        raise ValueError('透明内容合成方式无效')
    size, box = value['canvas_size'], value['box']
    if (not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or not 1 <= v <= 32768 for v in size)
            or size[0]*size[1] > 60_000_000 or not isinstance(box, list) or len(box) != 4
            or any(type(v) is not int for v in box)
            or not 0 <= box[0] < box[2] <= size[0] or not 0 <= box[1] < box[3] <= size[1]):
        raise ValueError('生成图像原图坐标无效')
    png = value['png']
    if not isinstance(png, str) or len(png) > MAX_BYTES*4//3+4:
        raise ValueError('生成图像资源过大')
    image = _decode(png)
    if image.mode != ('RGBA' if rgba else 'RGB'):
        raise ValueError('透明内容与图像模式不一致')
    digest = sha256(image.tobytes()).hexdigest()
    if value['sha256'] != digest:
        raise ValueError('生成图像校验失败')
    return {'png': png, 'canvas_size': list(size), 'box': list(box), 'sha256': digest,
            **({'compositing': 'replace_rgba'} if rgba else {})}


def encode_patch(image, canvas_size, box, *, preserve_alpha=False):
    image = image.convert('RGBA' if preserve_alpha else 'RGB')
    if image.width * image.height > MAX_PIXELS:
        raise ValueError('生成图像分辨率超过限制')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return validate_patch({'png': base64.b64encode(buffer.getvalue()).decode('ascii'),
                           'canvas_size': list(canvas_size), 'box': list(box),
                           'sha256': sha256(image.tobytes()).hexdigest(),
                           **({'compositing': 'replace_rgba'} if preserve_alpha else {})})


def render_patch(image, layer, full_size, canvas_box=None):
    from .document import raster_mask_cached
    from .engine import Recipe, render_masked

    patch = layer['pixel_patch']
    native = patch['canvas_size']
    box = patch['box']
    mapped = [round(box[i]*full_size[i % 2]/native[i % 2]) for i in range(4)]
    if mapped[0] >= mapped[2] or mapped[1] >= mapped[3]:
        return image
    generated = _decode(patch['png']).resize((mapped[2]-mapped[0], mapped[3]-mapped[1]), Image.Resampling.LANCZOS)
    region = raster_mask_cached(layer['mask'], full_size)
    if layer['opacity'] < 1:
        region = region.point([round(v * layer['opacity']) for v in range(256)])
    view = canvas_box or (0, 0, *full_size)
    clip = [max(mapped[0], view[0]), max(mapped[1], view[1]), min(mapped[2], view[2]), min(mapped[3], view[3])]
    if clip[0] >= clip[2] or clip[1] >= clip[3]:
        return image
    destination = tuple(clip[i]-view[i % 2] for i in range(4))
    local = tuple(clip[i]-mapped[i % 2] for i in range(4))
    original = image.crop(destination)
    pixels = generated.crop(local)
    rgba = patch.get('compositing') == 'replace_rgba'
    if not rgba and original.mode == 'RGBA':
        pixels.putalpha(original.getchannel('A'))
    from .rgba_content import replace_content
    output = image.convert('RGBA') if rgba else image.copy()
    replacement = (replace_content(pixels, original, region.crop(tuple(clip))) if rgba
                   else Image.composite(pixels, original, region.crop(tuple(clip))))
    output.paste(replacement, destination[:2])
    if any(layer['recipe'].values()):
        mask = region.crop(view) if canvas_box else region
        output = render_masked(output, Recipe.from_dict(layer['recipe']), mask,
                               detail_size=full_size, origin=view[:2])
    return output
