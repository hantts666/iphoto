"""Vision points correct semantic constraints; native matting solves coverage.

Only the reviewed crop interiors may change. Both model predictions and the
joined result must satisfy the points; the controller reviews the actual
composition again before it can publish the mask.
"""
from time import perf_counter

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from ..matte_review import REFERENCE_HALO, inside_bounds, point_bounds, point_frame, validate_corrections
from ..segmentation.service import choose_candidate
from . import neural
from .metadata import copy_metadata

HALO = REFERENCE_HALO
JOIN = 32


def correct(image, mask, corrections, boxes, *, semantic=None, matte=None, progress=None, hair=False, hair_context=None, context_points=False):
    started=perf_counter()
    if type(hair) is not bool:
        raise ValueError('局部发丝纠错方法无效')
    if type(context_points) is not bool or context_points and not hair:
        raise ValueError('上下文发丝参照方法无效，原范围保留')
    if not isinstance(boxes,list) or not 1<=len(boxes)<=4:
        raise ValueError('局部抠图缺少已核对的边缘，原范围保留')
    for box in boxes:
        if (not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box)
                or not 0<=box[0]<box[2]<=image.width or not 0<=box[1]<box[3]<=image.height
                or box[2]-box[0]>512 or box[3]-box[1]>512):
            raise ValueError('局部抠图边缘坐标无效，原范围保留')
    frames=[point_frame(core,image.size) if context_points else core for core in boxes]
    bounds={str(index):point_bounds(core,frame) for index,(core,frame) in enumerate(zip(boxes,frames),1)} if context_points else None
    validate_corrections(corrections,len(boxes),strand_points=hair,bounds=bounds)
    if context_points:
        for patch in corrections:
            core=boxes[patch['edge']-1];frame=frames[patch['edge']-1]
            if any(not inside_bounds(p,point_bounds(core,frame,0)) for p in patch['points'] if p[2]!=1):
                raise ValueError('只有不透明保留参照可位于修改范围外，原范围保留')
    mask=validate_mask(mask)
    original=raster_mask(mask,image.size)
    output=np.array(original)
    protected=not mask['inverted'] and mask.get('semantic_target') in ('face','face_skin','body_skin')
    if hair and (protected or mask['inverted']):
        raise ValueError('人物发丝纠错不能修改皮肤或反选范围')
    records=[];warnings=[]
    for patch in corrections:
        core=boxes[patch['edge']-1]
        frame=frames[patch['edge']-1]
        box=[max(0,core[0]-HALO),max(0,core[1]-HALO),
             min(image.width,core[2]+HALO),min(image.height,core[3]+HALO)]
        guide=original.crop(box)
        previous=np.asarray(guide)
        coords=np.array([[frame[0]-box[0]+p[0]/999*(frame[2]-frame[0]-1),
                          frame[1]-box[1]+p[1]/999*(frame[3]-frame[1]-1)] for p in patch['points']],np.float32)
        labels=np.array([p[2] for p in patch['points']],np.float32)
        if any(previous[round(float(y)),round(float(x))]<245 for (x,y),label in zip(coords,labels) if label==1):
            raise ValueError('局部纠错缺少可靠的不透明保留点，原范围保留')
        if protected and any(previous[round(float(y)),round(float(x))]==0 for (x,y),label in zip(coords,labels) if label):
            raise ValueError('保留点位于受保护的皮肤分区外，原范围保留')
        if progress:progress(phase='semantic')
        if semantic is None:
            from ..segmentation.precise_sam import backend
            semantic=backend()
        # A coarse mask is a hint. Saturated logits must not make its wrong
        # opaque foreground override the new explicit negative points.
        model_guide=guide.point(lambda value:max(64,min(191,value)))
        # Label 2 belongs to our hair contract, not SAM's box-corner tokens.
        # It must be an included semantic point, without claiming opaque alpha.
        semantic_labels=np.where(labels==2,1,labels).astype(np.float32)
        logits,scores,timing=semantic.predict_with_prior(image.crop(box),coords,semantic_labels,model_guide)
        hard,quality=choose_candidate(logits,scores,coords,semantic_labels,guide)
        if quality['predicted_iou']<.75:
            raise ValueError('局部纠错模型信心不足，原范围保留')
        inside=distance_transform_edt(np.pad(hard,1))[1:-1,1:-1]
        outside=distance_transform_edt(np.pad(~hard,1))[1:-1,1:-1]
        trimap=np.full(hard.shape,128,np.uint8)
        trimap[inside>patch['radius']]=255
        trimap[outside>patch['radius']]=0
        yy,xx=np.ogrid[:hard.shape[0],:hard.shape[1]]
        for (x,y),label in zip(coords,labels):
            # An excluded background pixel can lie between fine hairs. A
            # broad negative disk would delete those neighboring strands.
            radius=8 if label==1 else 2
            trimap[(xx-x)**2+(yy-y)**2<=radius**2]=128 if label==2 else 255 if label else 0
        if protected:trimap[previous==0]=0
        def report(tile,tiles):
            if progress:progress(tile,tiles)
        matte=matte or neural.backend()
        if hair:
            if hair_context is None:
                from .hair import HairContext
                hair_context=HairContext(image,original,progress=progress)
            global_points=[[(box[0]+float(x))/max(1,image.width-1),
                            (box[1]+float(y))/max(1,image.height-1),int(label)] for (x,y),label in zip(coords,labels)]
            pixels,detail=hair_context.solve(image,previous,box,semantic=hard,semantic_radius=patch['radius'],points=global_points,engine=matte,progress=progress)
            tiles=detail['tiles']
        else:
            pixels,tiles=neural.solve(image.crop(box),trimap,engine=matte,progress=report)
        scope=np.zeros(hard.shape,bool)
        sx,sy=core[0]-box[0],core[1]-box[1]
        scope[sy:sy+core[3]-core[1],sx:sx+core[2]-core[0]]=True
        if protected:scope &= previous>0
        weight=np.clip(distance_transform_edt(np.pad(scope,1))[1:-1,1:-1]/JOIN,0,1)
        weight=weight*weight*(3-2*weight)
        joined=np.rint(previous+weight*(pixels.astype(np.float32)-previous)).astype(np.uint8)
        if protected and mask.get('face_part_scope'):
            joined=np.minimum(joined,np.asarray(raster_mask(mask['face_part_scope'],image.size).crop(box)))
        if any(joined[round(float(y)),round(float(x))]==0 for (x,y),label in zip(coords,labels) if label==2):
            raise ValueError('AI 发丝定位未通过透明度验证，原范围保留')
        if any(bool(joined[round(float(y)),round(float(x))]>127)!=bool(label) for (x,y),label in zip(coords,labels) if label!=2):
            raise ValueError('局部透明结果未满足纠错点，原范围保留')
        changed=scope & (joined!=previous)
        output[box[1]:box[3],box[0]:box[2]][changed]=joined[changed]
        warnings.extend(quality['warnings'])
        records.append({'edge':patch['edge'],'box':core,'changed_pixels':int(changed.sum()),
                        'strand_points':int((labels==2).sum()),
                        'context_references':int(sum(label==1 and not (core[0]<=box[0]+float(x)<core[2] and core[1]<=box[1]+float(y)<core[3]) for (x,y),label in zip(coords,labels))),
                        'point_frame_box':frame,
                        'tiles':tiles,'predicted_iou':quality['predicted_iou'],'timing':timing})
    if not any(record['changed_pixels'] for record in records):
        raise ValueError('局部纠错没有产生有效改变，原范围保留')
    result=copy_metadata(mask, empty_mask())
    result.update(label=mask['label'],bitmap=encode_bitmap(Image.fromarray(output),sampling='alpha',preserve_resolution=True))
    if matte.fallback:warnings.append(matte.fallback)
    return validate_mask(result),{'backend':'SAM2.1 Small + MODNet + BiSeNet + ViTMatte-S' if hair else 'SAM2.1 Small + ViTMatte-S',
                                 'hair_matting':hair,'provider':matte.provider,'corrections':records,
                                 'elapsed_ms':round((perf_counter()-started)*1000,1),'warnings':warnings}
