"""Semantic part anchors plus source-detail SAM; only staged mask reductions."""
from copy import deepcopy
from time import perf_counter

import cv2
import numpy as np
from PIL import Image

from ..ai_grounding import crop_pixels
from ..ai_mask_refinement import eligible, validate_result
from ..document import raster_mask, validate_mask
from ..masks import encode_bitmap
from .face_detection import validate_features
from .prompts import from_hint, validate_points
from .edges import guided_edge


def refine(image, hint, context, *, parser=None, engine=None, progress=None):
    from . import face_precision, precise_sam
    from .service import choose_candidate

    started = perf_counter()
    hint = validate_result(validate_mask(hint), hint, image.size)
    if not eligible(hint) or not isinstance(context, dict) or set(context) != {'crop','features','anchor'}:
        raise ValueError('五官边缘修正缺少可靠的人脸关系，原范围保留')
    features = validate_features(context['features'])
    anchor = validate_points([[*context['anchor'],1]])[0][:2]
    face_box = crop_pixels(context['crop'], image.size)
    if (face_box[2]-face_box[0])*(face_box[3]-face_box[1]) > 16_000_000:
        raise ValueError('人脸关系范围过大，原范围保留')
    original = raster_mask(hint, image.size)
    bounds = original.getbbox()
    if not bounds:
        raise ValueError('五官范围为空，未进行边缘修正')
    box = (max(0,bounds[0]-64),max(0,bounds[1]-64),
           min(image.width,bounds[2]+64),min(image.height,bounds[3]+64))
    if (box[2]-box[0])*(box[3]-box[1]) > 4_000_000:
        raise ValueError('五官边缘范围过大，原范围保留')
    left,top=max(box[0],face_box[0]),max(box[1],face_box[1])
    right,bottom=min(box[2],face_box[2]),min(box[3],face_box[3])
    if left>=right or top>=bottom:
        raise ValueError('五官范围与人脸关系不匹配，原范围保留')
    if progress: progress('semantic_parts')
    if parser is None and not face_precision.available():
        raise ValueError('精细五官模型尚未配置，原范围保留')
    face = image.crop(face_box).convert('RGB')
    landmarks = np.asarray([*features['eyes'],anchor,*features['mouth']])*image.size-face_box[:2]
    parser = parser or face_precision.backend()
    field = parser.predict_native(face,landmarks)
    if (field.dtype!=np.uint8 or field.shape!=(face.height,face.width) or field.max()>18):
        raise ValueError('五官语义结果无效，原范围保留')
    patch=image.crop(box).convert('RGB');guide=original.crop(box);prior=np.asarray(guide)
    labels=np.zeros((patch.height,patch.width),np.uint8)
    labels[top-box[1]:bottom-box[1],left-box[0]:right-box[0]]=field[top-face_box[1]:bottom-face_box[1],left-face_box[0]:right-face_box[0]]
    def interior(support):
        distance=cv2.distanceTransform(np.pad(support.astype(np.uint8),1),cv2.DIST_L2,5)[1:-1,1:-1]
        _,depth,_,point=cv2.minMaxLoc(distance)
        return point if depth>=1 else None
    classes=(12,13) if hint['face_part']=='lips' else (10,)
    keeps=[interior((labels==part)&(prior>127)) for part in classes]
    if any(point is None for point in keeps):
        raise ValueError('已有范围未包含可靠的'+('上下唇' if len(classes)==2 else '鼻部')+'保留点，原范围保持')
    points=[[x/max(1,patch.width-1),y/max(1,patch.height-1),1] for x,y in keeps]
    if hint['face_part']=='lips':
        mouth=interior((labels==11)&(prior==0))
        if mouth: points.append([mouth[0]/max(1,patch.width-1),mouth[1]/max(1,patch.height-1),0])
    coords,point_labels=from_hint(guide,points)
    if engine is None and not precise_sam.available():
        raise ValueError('精细提示点模型尚未配置，原范围保留')
    engine=engine or precise_sam.backend(progress=progress)
    logits,scores,timing=engine.predict_with_prior(patch,coords,point_labels,guide,progress=progress)
    hard,quality=choose_candidate(logits,scores,coords,point_labels,guide)
    alpha=np.minimum(np.asarray(guided_edge(patch,hard,radius=2)),prior)
    if not alpha.any(): raise ValueError('边缘修正结果为空，原范围保留')
    for (x,y),label in zip(coords,point_labels):
        if label in (0,1) and (alpha[round(float(y)),round(float(x))]>127)!=bool(label):
            raise ValueError('边缘结果未保留可靠的五官提示点，原范围保留')
    full=original.copy();full.paste(Image.fromarray(alpha),box[:2])
    result=deepcopy(hint)
    if np.any(alpha!=prior):
        result.update(bitmap=encode_bitmap(full,sampling='alpha',preserve_resolution=True),ops=[],feather=0)
    quality.update(timing,model='SAM2.1 Small · 五官分别保留',resolution='source',
        semantic_target=hint['semantic_target'],mask_size=list(image.size),crop_box=list(box),
        semantic_keep_classes=list(classes),elapsed_ms=round((perf_counter()-started)*1000,1))
    quality['warnings'].append('原先未选区域保持；漏选和边缘仍需检查')
    return validate_result(result,hint,image.size),quality
