"""Restore enclosed whole-face deficits only with a second semantic opinion.

The basic parser owns the exterior. The fine parser must confirm every pixel
of an enclosed region, including its faded rim; partial filling leaves a ring.
This is semantic selection, not an estimate of optical transparency.
"""
import cv2
import numpy as np
from ..masks import MAX_MASK_PIXELS

MAX_PIXELS=4_000_000


def _validate(alpha,context):
    if (not isinstance(alpha,np.ndarray) or alpha.ndim!=2 or alpha.dtype!=np.uint8
            or not isinstance(context,np.ndarray) or context.shape!=alpha.shape or context.dtype!=np.uint8
            or not alpha.size or alpha.size>MAX_MASK_PIXELS):
        raise ValueError('面部内部范围超过局部预算或格式无效')


def _regions(alpha,context):
    _validate(alpha,context)
    # Flood the padded exterior in bytes. A full source-sized int32 label map
    # would waste memory even when only a tiny cheek hole needs checking.
    work=np.pad((alpha<255).astype(np.uint8),1,constant_values=1)
    cv2.floodFill(work,None,(0,0),2,flags=8)
    holes=(work[1:-1,1:-1]==1).astype(np.uint8)
    del work
    contours,hierarchy=cv2.findContours(holes,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
    budget=0
    for index,contour in enumerate(contours):
        if hierarchy[0,index,3]!=-1:continue
        x,y,w,h=cv2.boundingRect(contour)
        if w*h>MAX_PIXELS:raise ValueError('单个面部内部范围超过局部预算')
        local=holes[y:y+h,x:x+w].copy()
        point=contour[0,0]
        cv2.floodFill(local,None,(int(point[0])-x,int(point[1])-y),2,flags=8)
        component=local==2
        if np.any(context[y:y+h,x:x+w][component]==0):continue
        budget+=int(component.sum())
        if budget>MAX_PIXELS:raise ValueError('面部内部候选像素超过局部预算')
        yield (slice(y,y+h),slice(x,x+w)),component


def candidates(alpha,context):
    _validate(alpha,context)
    result=np.zeros(np.shape(alpha),bool)
    for region,component in _regions(alpha,context):result[region]|=component
    return result


def restore(alpha,context,confirmed):
    _validate(alpha,context)
    if not isinstance(confirmed,np.ndarray) or confirmed.shape!=alpha.shape or confirmed.dtype!=np.bool_:
        raise ValueError('精细面部分区与当前内部范围不一致')
    result=alpha.copy();total=restored=pixels=0
    for region,component in _regions(alpha,context):
        total+=1
        if not confirmed[region][component].all():continue
        result[region][component]=255
        restored+=1;pixels+=int(component.sum())
    return result,{'candidate_regions':total,'restored_regions':restored,
        'restored_pixels':pixels,'retained_regions':total-restored}
