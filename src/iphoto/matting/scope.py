"""Source-pixel upper bound for semantic filtering of an existing selection."""

from PIL import ImageChops

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap


def restrict(mask, limit, size):
    """Intersection keeps continuous coverage; never turn the upper bound binary."""
    mask, limit = validate_mask(mask), validate_mask(limit)
    proposed, allowed = raster_mask(mask, size), raster_mask(limit, size)
    alpha = ImageChops.darker(proposed, allowed)
    if not alpha.getbbox():
        raise ValueError('未在原范围内可靠识别到目标，原范围保留')
    # Retain the original semantic protections, identity and source constraints.
    # Spatial fields are replaced with the exact source-resolution intersection.
    result = {**limit, **empty_mask(), 'edge_shift':0, 'label':mask['label'],
              'bitmap':encode_bitmap(alpha,sampling='alpha',preserve_resolution=True)}
    if 'color_recovery' in mask:
        result['color_recovery'] = mask['color_recovery']
    return validate_mask(result)
