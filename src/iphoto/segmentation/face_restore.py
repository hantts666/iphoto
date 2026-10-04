"""Staged neural part restoration or declared complete-part reselection."""
from copy import deepcopy

from PIL import ImageChops

from ..ai_grounding import crop_pixels
from ..ai_mask_refinement import validate_restoration, validate_reselection, validate_result
from ..document import raster_mask, validate_mask, spatial_mask
from ..masks import encode_bitmap
from .face_detection import validate_features
from .prompts import validate_points


def _proposal(image, hint, context, scope, progress):
    from .face_skin import segment
    if not isinstance(context, dict) or set(context)!={'crop','features','anchor','mask'}:
        raise ValueError('五官补选缺少可靠的完整人脸关系，原范围保留')
    features = validate_features(context['features'])
    points = validate_points([[*context['anchor'],1]])
    box = crop_pixels(context['crop'],image.size)
    if (box[2]-box[0])*(box[3]-box[1])>16_000_000:
        raise ValueError('人脸关系范围过大，原范围保留')
    return segment(image,scope,points,crop=context['crop'],
        target=hint['semantic_target'],recover_anchor=True,features=features,scope='region',
        context_hint=context['mask'],part=hint['face_part'],progress=progress,require_precision=True)


def restore(image, hint, context, *, progress=None):
    hint = validate_mask(hint)
    validate_restoration(hint, hint, image.size)
    proposal,quality = _proposal(image,hint,context,hint['face_part_scope'],progress)
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


def reselect(image, hint, context, *, progress=None):
    hint = validate_result(hint,hint,image.size)
    if not isinstance(context,dict) or 'mask' not in context:
        raise ValueError('完整五官重选缺少可靠的人脸关系，原范围保留')
    scope = spatial_mask(context['mask'])
    proposal,quality = _proposal(image,hint,context,scope,progress)
    result = deepcopy(hint)
    result['face_part_scope'] = scope
    old,new = raster_mask(hint,image.size),raster_mask(proposal,image.size)
    if ImageChops.difference(old,new).getbbox():
        result.update(bitmap=proposal['bitmap'],ops=[],feather=0)
        result.pop('edge_shift',None)
    quality['model'] += ' · 完整可见五官重选'
    quality['warnings'] = ['请放大检查真实轮廓、遮挡和漏选']
    return validate_reselection(result,hint,image.size,scope),quality
