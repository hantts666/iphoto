"""Channel-derived alpha guided by semantic coverage and optional neural matting."""

from time import perf_counter
from copy import deepcopy
import math

import cv2
import numpy as np
from PIL import Image

from ..document import raster_mask, empty_mask, validate_mask
from ..masks import encode_bitmap

CHANNELS = ('red', 'green', 'blue', 'luminance', 'red_green', 'red_blue', 'green_blue')
CHANNEL_NAMES = {'red':'红','green':'绿','blue':'蓝','luminance':'亮度',
                 'red_green':'红−绿','red_blue':'红−蓝','green_blue':'绿−蓝'}


def validate_options(value):
    required = {'channel', 'black', 'white', 'gamma', 'invert', 'radius', 'ai', 'interior'}
    if not isinstance(value, dict) or not required <= set(value) or not set(value) <= required | {'detail','color','whole'}:
        raise ValueError('通道抠图参数无效')
    if value['channel'] not in (*CHANNELS, 'auto') or any(type(value[k]) is not bool for k in ('invert','ai','interior')):
        raise ValueError('通道抠图方法无效')
    if any(type(value[k]) is not bool for k in ('detail','color','whole') if k in value):
        raise ValueError('通道细节与去背景串色选项无效')
    if (any(type(value[k]) is not int for k in ('black', 'white', 'radius'))
            or not 0 <= value['black'] < value['white'] <= 255 or not 1 <= value['radius'] <= 256
            or isinstance(value['gamma'], bool) or not isinstance(value['gamma'], (int, float))
            or not math.isfinite(value['gamma']) or not .2 <= value['gamma'] <= 5):
        raise ValueError('通道黑场需小于白场，灰度应为0.2～5，边缘范围为1～256px')
    return dict(value)


def whole_options():
    return {'channel':'luminance','black':0,'white':255,'gamma':1.,'invert':False,'radius':32,
            'ai':False,'interior':True,'detail':False,'color':False,'whole':True}


def channel_planes(image):
    """Native byte planes shared by channel alpha and visual reference sampling."""
    rgb = np.asarray(image.convert('RGB'), dtype=np.uint8)
    planes = [rgb[..., i] for i in range(3)]
    planes.append(np.rint(rgb[...,0]*.2126 + rgb[...,1]*.7152 + rgb[...,2]*.0722).astype(np.uint8))
    # Photoshop-style Subtract, scale=1/offset=128. Cast before subtraction:
    # uint8 wraparound would invent foreground/background contrast.
    for first, second in ((0,1),(0,2),(1,2)):
        planes.append(np.clip(rgb[...,first].astype(np.int16)-rgb[...,second]+128,0,255).astype(np.uint8))
    return planes


def channel_plane(image, channel):
    """Compute the requested native plane without allocating six unused planes."""
    if channel not in CHANNELS:
        raise ValueError('通道抠图方法无效')
    rgb = np.asarray(image.convert('RGB'), dtype=np.uint8)
    if channel in CHANNELS[:3]:
        return rgb[..., CHANNELS.index(channel)]
    if channel == 'luminance':
        return np.rint(rgb[...,0]*.2126 + rgb[...,1]*.7152 + rgb[...,2]*.0722).astype(np.uint8)
    first, second = {'red_green':(0,1),'red_blue':(0,2),'green_blue':(1,2)}[channel]
    return np.clip(rgb[...,first].astype(np.int16)-rgb[...,second]+128,0,255).astype(np.uint8)


def _fields(image, mask, radius):
    alpha = np.asarray(raster_mask({**mask, 'feather': 0}, image.size))
    hard = (alpha > 32).astype(np.uint8)
    if not hard.any() or hard.all():
        raise ValueError('通道抠图需要先选中目标，保留部分可见背景作为参照')
    inside = cv2.distanceTransform(np.pad(hard, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    outside = cv2.distanceTransform(1-hard, cv2.DIST_L2, 5)
    fg = (inside >= max(2, radius/2)) & (alpha > 240)
    bg = (outside > max(2, radius/2)) & (outside < max(8, radius*3)) & (alpha == 0)
    if fg.sum() < 16 or bg.sum() < 16:
        raise ValueError('缺少可靠的主体内部或周围背景，请先补选/擦除后再试')
    planes = channel_planes(image)
    metrics = []
    for channel, plane in zip(CHANNELS, planes):
        f, b = plane[fg][::max(1, int(fg.sum())//10000)], plane[bg][::max(1, int(bg.sum())//10000)]
        fm, bm = float(np.median(f)), float(np.median(b))
        noise = float(np.median(np.abs(f.astype(float)-fm)) + np.median(np.abs(b.astype(float)-bm)))
        low, high = ((float(np.percentile(f,80)),float(np.percentile(b,10))) if fm<bm
                     else (float(np.percentile(b,90)),float(np.percentile(f,20))))
        if high-low < 8:
            low,high = min(fm,bm),max(fm,bm)
        metrics.append({'channel': channel, 'black': round(low), 'white': round(high),
                        'invert': fm < bm, 'score': round(abs(fm-bm)/(noise+6), 3)})
    return alpha, inside, outside, planes, metrics


def suggest(image, mask, radius=32):
    _, _, _, _, metrics = _fields(image, mask, radius)
    return _suggest(metrics, radius), metrics


def _suggest(metrics, radius):
    best = max(metrics, key=lambda item: item['score'])
    if best['white']-best['black'] < 8:
        raise ValueError('主体和背景通道差异太小，不能可靠自动抠图；请局部修边或选择其他范围')
    return {**{k:v for k,v in best.items() if k != 'score'}, 'gamma': 1., 'radius': radius, 'ai': True, 'interior':False}


def _curve(plane,options):
    values=plane.astype(np.float32)
    values=np.clip((values-options['black'])/(options['white']-options['black']),0,1)
    if options['invert']:
        values=1-values
    return values**(1/options['gamma'])


def _channel_alpha(planes, metrics, options):
    channel = options['channel']
    if channel == 'auto':
        channel = max(metrics, key=lambda item: item['score'])['channel']
    values = _curve(planes[CHANNELS.index(channel)],options)
    score = next(item['score'] for item in metrics if item['channel'] == channel)
    return values, channel, score


def _whole_pixels(image,mask,options):
    if (options['channel']=='auto' or options['ai'] or options.get('detail') or options.get('color')):
        raise ValueError('先用通道生成目标范围，再使用 AI 修细节或去背景串色')
    if image.width*image.height>32_000_000:
        raise ValueError('整图通道超过3200万像素，请先选择目标区域')
    if mask.get('semantic_target') or raster_mask(mask,image.size).getextrema()!=(255,255):
        raise ValueError('整图通道只用于尚未限定目标的全选范围，局部范围请使用常规通道抠图')
    plane=channel_plane(image, options['channel'])
    return np.rint(_curve(plane,options)*255).astype(np.uint8)


def _region(image, mask, radius):
    original = raster_mask({**mask, 'feather': 0}, image.size)
    bounds = original.getbbox()
    if not bounds:
        raise ValueError('原范围为空，通道抠图未应用')
    box = (max(0,bounds[0]-radius*3), max(0,bounds[1]-radius*3),
           min(image.width,bounds[2]+radius*3), min(image.height,bounds[3]+radius*3))
    if (box[2]-box[0])*(box[3]-box[1]) > 32_000_000:
        raise ValueError('通道处理范围过大，请分区域处理')
    local_mask = {**empty_mask(), 'bitmap': encode_bitmap(original.crop(box), sampling='alpha', preserve_resolution=True)}
    return original, box, image.crop(box), local_mask


def _constraints(alpha, inside, outside, values, options, score, semantic):
    allowed = outside <= options['radius']
    if semantic:
        allowed &= alpha > 0
    opaque_core = (inside > options['radius']) & (alpha == 255)
    known_fg = opaque_core & (values >= .98) if options['interior'] else opaque_core
    known_bg = ~allowed | ((values < .015) & (alpha == 0))
    if options['interior'] and score >= 4:
        known_bg |= (values < .015) & ~known_fg
    return allowed, known_fg, known_bg


def estimate(image, mask, options, *, neural=None, progress=None):
    """Only a bounded region near the selected target may grow; distant alpha stays exact."""
    started = perf_counter()
    options = validate_options(options)
    if options.get('whole'):
        result=_whole_pixels(image,mask,options)
        final=validate_mask({**empty_mask(),'label':'通道 · '+CHANNEL_NAMES[options['channel']],
                             'bitmap':encode_bitmap(Image.fromarray(result),sampling='alpha',preserve_resolution=True)})
        return final,{'channel_mask':True,'whole':True,'backend':'整图通道透明度','channel':options['channel'],
                      'contrast_score':0.,'native_detail':False,'color_recovery':False,'mask_size':list(image.size),
                      'tiles':0,'elapsed_ms':round((perf_counter()-started)*1000,1),
                      'unknown_pixels':0,'partial_pixels':int(((result>0)&(result<255)).sum()),'warnings':[]}
    radius = options['radius']
    original, box, cropped, local_mask = _region(image, mask, radius)
    alpha, inside, outside, planes, metrics = _fields(cropped, local_mask, radius)
    values, channel, score = _channel_alpha(planes, metrics, options)
    # A face/skin mask includes protected eyes, lips, hair and clothes. Channels
    # can refine its current support but cannot bypass those semantic exclusions.
    allowed, known_fg, known_bg = _constraints(alpha,inside,outside,values,options,score,bool(mask.get('semantic_target')))
    trimap = np.full(alpha.shape, 128, dtype=np.uint8)
    trimap[known_fg], trimap[known_bg] = 255, 0
    unknown = trimap == 128
    if options['ai']:
        if known_fg.sum() < 16 or known_bg.sum() < 16:
            raise ValueError('缺少可靠的保留/移除参照，AI 透明度未应用；请调整通道或补充不透明主体范围')
        if neural is None:
            from .neural import solve
            neural = solve
        refined, tiles = neural(cropped, trimap, progress=progress)
        # Reliable channel contrast carries real subpixel brightness. The AI
        # resolves spatial ambiguity; avoid treating every gray hair as opaque.
        weight = min(.8, max(0., (score-1.5)/8))
        channel_pixels = np.rint(values*255).astype(np.uint8)
        mixed = np.rint(refined*(1-weight) + channel_pixels*weight).astype(np.uint8)
        ambiguous_bg = (alpha == 0) & (channel_pixels > 245) & (refined < 12)
        mixed[ambiguous_bg] = 0
        result = np.where(unknown, mixed, refined).astype(np.uint8)
        backend = '通道 + ViTMatte-S · ONNX'
    else:
        result = np.rint(values*255).astype(np.uint8)
        result[known_fg] = 255
        result[known_bg] = 0
        tiles, backend = 0, '通道透明度'
    warnings = ['通道区分较弱，请放大检查颜色相近的边缘'] if score < 2 else []
    polished = False
    if options.get('detail') and options['ai'] and score < 4 and ((result>0)&(result<255)).any():
        # Explicit opt-in: color lines replace continuous learned coverage and
        # can lose fine strands. Completion is not evidence of improved quality.
        # Keep the original hard constraints.
        from .service import refine_alpha
        if progress is not None:
            progress(phase='polish')
        try:
            seed = {**empty_mask(), 'bitmap':encode_bitmap(Image.fromarray(result), sampling='alpha', preserve_resolution=True)}
            # This alpha comes from an RGB-composition model and channel values.
            # Match that domain instead of changing its coverage with a second
            # gamma transform. Standalone physical matting keeps its linear mode.
            detailed, _ = refine_alpha(cropped, seed, 4, linear=False)
            result = np.asarray(raster_mask(detailed, cropped.size)).copy()
            result[known_fg], result[known_bg] = 255, 0
            polished = True
        except (ValueError, ImportError) as exc:
            warnings.append('细纹理求解未完成，保留 AI 透明度：' + str(exc))
    result[~allowed] = alpha[~allowed]
    output = original.copy()
    output.paste(Image.fromarray(result), box[:2])
    final = validate_mask({**mask, 'base':'empty', 'ops':[], 'inverted':False, 'feather':0,
                           'edge_shift':0, 'bitmap':encode_bitmap(output, sampling='alpha', preserve_resolution=True)})
    final.pop('color_recovery', None)
    if options.get('color'):
        from ..cutout import recovery_box, available
        try:
            if not available():
                raise ValueError('前景颜色组件未安装，请更新依赖')
            recovery_box(output)
            final['color_recovery'] = True
        except ValueError as exc:
            warnings.append('未去背景串色：' + str(exc))
    return final, {'channel_mask':True, 'backend':backend, 'channel':channel, 'contrast_score':score,
                   'native_detail':polished, 'color_recovery':final.get('color_recovery',False),
                   'mask_size':list(image.size), 'tiles':tiles, 'elapsed_ms':round((perf_counter()-started)*1000,1),
                   'unknown_pixels':int(unknown.sum()), 'partial_pixels':int(((result>0)&(result<255)).sum()),
                   'warnings':warnings}


def preview(image, mask, options, radius):
    options=validate_options(options)
    if options.get('whole'):
        return Image.fromarray(_whole_pixels(image,mask,options)),options['channel'],0.
    fields = _fields(image, mask, radius)
    return _preview_fields(fields, options, radius, bool(mask.get('semantic_target')))


def _preview_fields(fields, options, radius, semantic):
    alpha, inside, outside, planes, metrics = fields
    values, channel, score = _channel_alpha(planes, metrics, options)
    allowed, known_fg, known_bg = _constraints(alpha,inside,outside,values,{**options,'radius':radius},score,semantic)
    pixels = np.rint(values*255).astype(np.uint8)
    pixels[known_fg], pixels[known_bg] = 255, 0
    pixels[~allowed] = alpha[~allowed]
    return Image.fromarray(pixels), channel, score


def native_preview(image, mask, options, *, initial=False, cache=None):
    """Derive levels and gray coverage before any display resize.

    RGB resampling followed by clipping/gamma is not the displayed coverage
    of a native channel curve. Share the application's original ROI, radius
    and reference pixels; the worker resizes only the resulting gray image.
    This draft does not include neural refinement or foreground recovery.
    """
    options = validate_options(options)
    if initial and not mask.get('semantic_target') and raster_mask(mask,image.size).getextrema()==(255,255):
        options = whole_options()
    if options.get('whole'):
        if cache is not None:
            cache.clear()
        return Image.fromarray(_whole_pixels(image,mask,options)), options, options['channel'], 0.
    original, box, fields = native_reference(image,mask,options['radius'],cache=cache)
    if initial:
        flags = {key:options[key] for key in ('interior','detail','color') if key in options}
        options = {**_suggest(fields[-1],options['radius']), **flags}
    pixels, channel, score = _preview_fields(fields,options,options['radius'],bool(mask.get('semantic_target')))
    output = original.copy()
    output.paste(pixels,box[:2])
    return output, options, channel, score


def native_reference(image, mask, radius, *, cache=None):
    """Share one bounded native reference between preview and AI evidence."""
    reuse = (cache is not None and cache.get('image') is image and cache.get('mask') == mask
             and cache.get('radius') == radius)
    if reuse:
        original, box, fields = cache['original'], cache['box'], cache['fields']
    else:
        if cache is not None:
            cache.clear()
        original, box, cropped, local_mask = _region(image,mask,radius)
        fields = _fields(cropped,local_mask,radius)
        # One bounded reference crop per worker; changing levels reuses native
        # samples/distances. Larger crops are computed without retaining them.
        if cache is not None and cropped.width*cropped.height <= 4_000_000:
            cache.update(image=image,mask=deepcopy(mask),radius=radius,
                         original=original,box=box,fields=fields)
    return original, box, fields
