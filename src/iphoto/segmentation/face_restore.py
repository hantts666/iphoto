"""Recompute a visible part within its stored intent; stage additions only."""
from copy import deepcopy

from PIL import ImageChops

from ..ai_grounding import crop_pixels
from ..ai_mask_refinement import validate_restoration
from ..document import raster_mask, validate_mask
from ..masks import encode_bitmap
from .face_detection import validate_features
from .prompts import validate_points


def restore(image, hint, context, *, progress=None):
    from .face_skin import segment

    hint = validate_mask(hint)
    validate_restoration(hint, hint, image.size)
    if not isinstance(context, dict) or set(context)!={'crop','features','anchor','mask'}:
        raise ValueError('五官补选缺少可靠的完整人脸关系，原范围保留')
    features = validate_features(context['features'])
    points = validate_points([[*context['anchor'],1]])
    box = crop_pixels(context['crop'],image.size)
    if (box[2]-box[0])*(box[3]-box[1])>16_000_000:
        raise ValueError('人脸关系范围过大，原范围保留')
    proposal, quality = segment(image,hint['face_part_scope'],points,crop=context['crop'],
        target=hint['semantic_target'],recover_anchor=True,features=features,scope='region',
        context_hint=context['mask'],part=hint['face_part'],progress=progress,require_precision=True)
    old = raster_mask(hint,image.size)
    joined = ImageChops.lighter(old,raster_mask(proposal,image.size))
    result = deepcopy(hint)
    if ImageChops.difference(joined,old).getbbox():
        result.update(bitmap=encode_bitmap(joined,sampling='alpha',preserve_resolution=True),
                      ops=[],feather=0)
        result.pop('edge_shift',None)
    quality['model'] += ' · 原始范围内补选'
    quality['warnings'] = ['保留原有覆盖；新增边缘仍需放大检查']
    return validate_restoration(result,hint,image.size),quality
