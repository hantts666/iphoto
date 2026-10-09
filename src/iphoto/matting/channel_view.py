"""Native channel drafts and staged results share one comparison frame."""
from PIL import Image

from ..document import raster_mask

MAX_NATIVE_VIEW = 8_000_000


def native_previews(source, alpha, reference, radius, *, layers=None, mask=None, whole=False):
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
    original = source.crop(box).convert('RGB')
    cropped = alpha.crop(box)
    def pictures():
        yield 'alpha', cropped
        yield 'source', original
        if mask is None:
            for color in ('white', 'black'):
                yield color, Image.composite(original, Image.new('RGB', original.size, color), cropped)
        else:
            rendered = render_detail_tile(source, layers, box)
            for color in ('white', 'black'):
                yield color, background_view(source, layers, mask, color, rendered, box)
    views = ((name, picture if native else preview(picture, 1600)) for name, picture in pictures())
    return views, {'native': native, 'box': list(box), 'focused': box != (0, 0, *source.size)}


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
