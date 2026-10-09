"""Coverage-aware replacement for content whose own alpha can change.

The edit mask is a blend weight between two contents, separate from their
intrinsic coverage. Work in bounded rows and premultiplied colors, then restore
straight RGBA for storage. Ordinary equal-alpha adjustments retain their exact
legacy rounding.
"""
import numpy as np
from PIL import Image, ImageChops


def replace_content(content, original, weight):
    if content.size != original.size or weight.size != original.size or weight.mode != 'L':
        raise ValueError('透明内容与编辑范围尺寸不一致')
    if content.mode == original.mode == 'RGB':
        return Image.composite(content, original, weight)
    new, old = content.convert('RGBA'), original.convert('RGBA')
    if ImageChops.difference(new.getchannel('A'), old.getchannel('A')).getbbox() is None:
        return Image.composite(new, old, weight)
    extrema = weight.getextrema()
    if extrema == (0, 0):
        return old
    if extrema == (255, 255):
        return new
    result = Image.new('RGBA', original.size)
    # At most 65536 pixels per float work block, even on a wide panorama.
    rows = max(1, 65536 // original.width)
    for top in range(0, original.height, rows):
        box = (0, top, original.width, min(top + rows, original.height))
        n = np.array(new.crop(box), dtype=np.float32)
        o = np.array(old.crop(box), dtype=np.float32)
        w = np.asarray(weight.crop(box), dtype=np.float32)[..., None] / 255
        na, oa = n[..., 3:] / 255, o[..., 3:] / 255
        alpha = na * w + oa * (1 - w)
        rgb = (n[..., :3] * na * w + o[..., :3] * oa * (1 - w)) / np.maximum(alpha, 1e-8)
        pixels = np.rint(np.clip(np.concatenate((rgb, alpha * 255), axis=2), 0, 255)).astype(np.uint8)
        # Zero/full edit weights are precise copies, including invisible RGB.
        values = np.asarray(weight.crop(box))
        pixels[values == 0] = o[values == 0].astype(np.uint8)
        pixels[values == 255] = n[values == 255].astype(np.uint8)
        result.paste(Image.fromarray(pixels), box[:2])
    return result
