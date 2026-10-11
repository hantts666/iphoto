"""Own-colour alpha for explicitly generated white-background portraits.

This never estimates the original photograph's alpha. Keep the generated RGB
and its alpha together; a generated matte must not be put on original pixels.
"""
import cv2
from math import isfinite
import numpy as np
from PIL import Image

from .pixel_patch import MAX_PIXELS

FOREGROUND_PROMPT = """
输出主体人物的白底抠图工作照片，背景必须为均匀纯白RGB，不画棋盘格、蒙版或分屏。
只保留用户指定的人物及其帽子、头发、衣物、手臂和随身物品，去掉背景及其他人物。
保持整张画布比例、人物位置、尺寸、姿势、五官、年龄、表情和衣物样式；不重新构图、换脸或美颜。
保留自然皮肤纹理、织物和帽子编织。细发丝沿原位置自然连续，发丝之间为白色背景；避免宽灰雾、白描边和绿色污染，不凭空新增大量发丝。
这张图将用于恢复生成内容自身的透明度，不输出标记或文字。
"""


def validate_inspection_box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) not in (int, float) or not isfinite(v) or not 0 <= v <= 999 for v in value)
            or value[2]-value[0] < 1 or value[3]-value[1] < 1):
        raise ValueError('AI重建缺少有效的发丝／人物边缘检查位置，照片未改变')
    return list(value)


def available():
    from .matting.models import available as details
    from .matting.portrait_models import MODEL_DIR, NAME, SIZE
    try:
        path = MODEL_DIR / NAME
        return details() and path.is_file() and path.stat().st_size == SIZE
    except (OSError, ValueError):
        return False


def white_guide(image, coarse):
    """Known background needs observable white AND low portrait confidence.

    White garments are not background simply because they are white. Conversely,
    a coarse zero does not lock coloured wisps to background. Both tests apply.
    """
    if image.mode != 'RGB' or coarse.mode != 'L' or coarse.size != image.size:
        raise ValueError('生成前景的颜色或人物参照无效，照片未改变')
    confidence = np.asarray(coarse)
    foreground = cv2.erode(np.uint8(confidence > 229), np.ones((17, 17), np.uint8),
                           borderType=cv2.BORDER_REPLICATE) > 0
    white = np.asarray(image).min(axis=2) >= 254
    background = cv2.erode(np.uint8(white & (confidence < 16)), np.ones((3, 3), np.uint8),
                           borderType=cv2.BORDER_REPLICATE) > 0
    minimum = max(16, image.width * image.height // 100)
    if np.count_nonzero(foreground) < minimum or np.count_nonzero(background) < minimum:
        raise ValueError('生成图缺少可靠的人物或纯白背景，未转成透明前景；照片未改变')
    guide = np.full(confidence.shape, 128, np.uint8)
    guide[background & ~foreground] = 0
    guide[foreground] = 255
    return guide


def white_foreground(image, matte):
    """Unmix only the partial foreground from an explicitly white work image.

    In byte RGB, I = a*F + (1-a)*255 implies a >= 1-min(I)/255
    for a nonnegative F. The learned alpha can violate that lower bound. Make
    only its partial pixels feasible before inverse composition; clipping a
    negative foreground alone darkens the edge and cannot reproduce the input.

    Zero support and opaque RGB stay exact. A nearly white generated background
    is not a reason to grow alpha everywhere. This neither recovers model-missed
    wisps nor applies to an original photo with an unknown/coloured background.
    """
    if (image.mode != 'RGB' or matte.mode != 'L' or image.size != matte.size
            or image.width * image.height > MAX_PIXELS):
        raise ValueError('白底生成前景的颜色或透明度无效，照片未改变')
    pixels = np.array(image)
    coverage = np.array(matte)
    partial = (coverage > 0) & (coverage < 255)
    samples = pixels[partial].astype(np.float32)
    previous = coverage[partial]
    feasible = np.maximum(previous, 255-samples.min(axis=1))
    colours = 255-(255-samples)*255/feasible[:, None]
    pixels[partial] = np.rint(np.clip(colours, 0, 255)).astype(np.uint8)
    coverage[partial] = feasible.astype(np.uint8)
    rgba = Image.fromarray(pixels).convert('RGBA')
    rgba.putalpha(Image.fromarray(coverage))
    return rgba, int(np.count_nonzero(feasible != previous))


def extract(image):
    if (image.mode not in ('RGB', 'RGBA') or min(image.size) < 64
            or image.width * image.height > MAX_PIXELS):
        raise ValueError('生成前景的格式或分辨率无效，照片未改变')
    if image.mode == 'RGBA' and image.getchannel('A').getextrema() != (255, 255):
        low, high = image.getchannel('A').getextrema()
        if low != 0 or high != 255:
            raise ValueError('生成透明图缺少确定背景或不透明人物，照片未改变')
        return image.copy(), {'method': 'generated_rgba', 'size': list(image.size), 'geometry_verified': False}
    from .matting.portrait import PortraitMatte
    from .matting.neural import NativeMatte, solve
    rgb = image.convert('RGB')
    portrait = PortraitMatte()
    guide = white_guide(rgb, portrait.predict_image(rgb))
    # Release the coarse session before loading the tiled detail session.
    portrait_provider = portrait.provider
    del portrait
    engine = NativeMatte()
    alpha, tiles = solve(rgb, guide, engine=engine)
    if (alpha.dtype != np.uint8 or alpha.shape != guide.shape
            or not np.array_equal(alpha[guide != 128], guide[guide != 128])):
        raise ValueError('生成前景的透明度求解无效，照片未改变')
    rgba, adjusted = white_foreground(rgb, Image.fromarray(alpha))
    return rgba, {'method': 'generated_white_portrait', 'size': list(rgba.size), 'tiles': tiles,
                  'portrait_provider': portrait_provider, 'detail_provider': engine.provider,
                  'colour_recovery': 'known_white', 'coverage_adjusted_pixels': adjusted,
                  'geometry_verified': False}


def review_images(baseline, rgba, inspection_box):
    """At most eight real RGB composites, including native generated edges.

    Full-frame previews alone hide halos. Evidence is deliberately made from
    the paired content at its own resolution, not from an upscaled 24MP export.
    """
    from .engine import preview
    if rgba.mode != 'RGBA':
        raise ValueError('生成前景缺少透明通道，照片未改变')
    composites = {name: Image.alpha_composite(Image.new('RGBA', rgba.size, name), rgba).convert('RGB')
                  for name in ('white', 'black')}
    images = [(preview(baseline.convert('RGB'), 1280), '修改前整图'),
              (preview(composites['white'], 1280), 'AI重建前景 · 白底整图'),
              (preview(composites['black'], 1280), 'AI重建前景 · 黑底整图')]
    width, height = min(512, rgba.width), min(512, rgba.height)
    # AI selects the structure to inspect on the original canvas. These boxes
    # only choose evidence; they never select, trim or manufacture output alpha.
    selected = []
    left, top, right, bottom = [v / 999 for v in validate_inspection_box(inspection_box)]
    y = max(0, min(rgba.height-height, round((top+bottom)/2*rgba.height-height/2)))
    for side in (left, right):
        x = max(0, min(rgba.width-width, round(side*rgba.width-width/2)))
        box = (x, y, x+width, y+height)
        if box not in selected:
            selected.append(box)
    for index, box in enumerate(selected):
        label = f'生成分辨率边缘{index+1}'
        if index == 0:
            native = tuple(round(value * baseline.size[i % 2] / rgba.size[i % 2]) for i, value in enumerate(box))
            images.append((baseline.crop(native).convert('RGB').resize((width,height), Image.Resampling.LANCZOS),
                           label+' · 原片同画布位置（未验证几何一致）'))
        images.extend((picture.crop(box), label+' · '+name) for name, picture in composites.items())
    return images
