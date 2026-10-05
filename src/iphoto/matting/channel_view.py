"""Fast channel drafts share one crop across alpha, source and background views.

These views do not run matting or color recovery, and cannot alter a mask.
Native application remains separate from the small interactive preview.
"""
from PIL import Image

from ..document import raster_mask


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
