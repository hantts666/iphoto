"""Native channel drafts and staged results share one comparison frame."""
from PIL import Image

from ..document import raster_mask

MAX_NATIVE_VIEW = 8_000_000


def native_previews(source, alpha, reference, radius, *, layers=None, mask=None, whole=False, channel=None, content=None):
    """One native frame for draft/result comparisons, including export colours.

    Large views are delivered at display size. A view advertised as 100% is
    never resampled; it contains the original pixels of the bounded crop.
    """
    from ..document import render_detail_tile
    from ..cutout import background_view
    from ..engine import preview

    if alpha.mode != 'L' or alpha.size != source.size:
        raise ValueError('通道预览与照片尺寸不一致')

    box = (0, 0, source.width, source.height)
    if not whole:
        bounds = raster_mask(reference, source.size).getbbox()
        if bounds:
            margin = max(16, radius*3)
            box = (max(0, bounds[0]-margin), max(0, bounds[1]-margin),
                   min(source.width, bounds[2]+margin), min(source.height, bounds[3]+margin))
    native = (box[2]-box[0])*(box[3]-box[1]) <= MAX_NATIVE_VIEW
    if content is not None and content.size!=source.size:
        raise ValueError('图像内容与照片坐标不一致')
    original = (source if content is None else content).crop(box).convert('RGB')
    cropped = alpha.crop(box)
    def pictures():
        yield 'alpha', cropped
        yield 'source', original
        if channel is not None:
            from .channels import channel_plane
            # Compute before display resizing: clipped colour differences at
            # fine edges are not equivalent to a plane of a resized RGB image.
            yield 'channel', Image.fromarray(channel_plane(original, channel))
        if mask is None:
            for color in ('white', 'black'):
                yield color, Image.composite(original, Image.new('RGB', original.size, color), cropped)
        else:
            rendered = render_detail_tile(source, layers, box)
            for color in ('white', 'black'):
                yield color, background_view(source, layers, mask, color, rendered, box)
    views = ((name, picture if native else preview(picture, 1600)) for name, picture in pictures())
    return views, {'native': native, 'box': list(box), 'focused': box != (0, 0, *source.size)}


def channel_choices(source, box):
    """Small, read-only overviews; the selected raw view remains native above."""
    from ..engine import preview
    from .channels import CHANNELS, CHANNEL_NAMES, channel_planes
    overview = preview(source.crop(box).convert('RGB'), 192)
    yield '', '图像内容', overview
    for name, plane in zip(CHANNELS, channel_planes(overview)):
        yield name, CHANNEL_NAMES[name] + '通道', Image.fromarray(plane)


def previews(image, alpha, mask, radius, *, whole=False):
    if alpha.mode!='L' or alpha.size!=image.size:
        raise ValueError('通道预览与照片尺寸不一致')
    box=(0,0,image.width,image.height)
    if not whole:
        bounds=raster_mask(mask,image.size).getbbox()
        if bounds:
            margin=max(16,radius*3)
            box=(max(0,bounds[0]-margin),max(0,bounds[1]-margin),
                 min(image.width,bounds[2]+margin),min(image.height,bounds[3]+margin))
    source=image.crop(box).convert('RGB')
    cropped=alpha.crop(box)
    views={'alpha':cropped,'source':source}
    for color in ('white','black'):
        views[color]=Image.composite(source,Image.new('RGB',source.size,color),cropped)
    return views,box!=(0,0,image.width,image.height)
