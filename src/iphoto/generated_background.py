"""Observe a generated near-white work background; never use on source photos.

This is a colour reference, not semantic coverage. The portrait model supplies
the low-confidence background samples. A fitted plane is only accepted for a
bright, nearly neutral, spatially supported and nearly uniform background.
"""
import math

import cv2
import numpy as np

from .pixel_patch import MAX_PIXELS

INVALID = '生成图缺少可靠的人物或近白背景，未转成透明前景；照片未改变'


def validate(field, size):
    if (not isinstance(field, np.ndarray) or field.dtype != np.float32
            or field.shape != (size[1], size[0], 3) or not np.isfinite(field).all()
            or field.min() < 245 or field.max() > 255):
        raise ValueError(INVALID)
    return field


def observe(image, coarse):
    """Keep the existing exact-white path; calibrate only when it lacks anchors."""
    if (image.mode != 'RGB' or coarse.mode != 'L' or image.size != coarse.size
            or min(image.size) < 64 or image.width*image.height > MAX_PIXELS):
        raise ValueError(INVALID)
    pixels = np.asarray(image)
    low = np.asarray(coarse) < 16
    minimum = max(16, image.width*image.height//100)
    strict = cv2.erode(np.uint8((pixels.min(axis=2) >= 254) & low), np.ones((3,3), np.uint8))
    if np.count_nonzero(strict) >= minimum:
        return None, {'background_reference': 'requested_white'}
    step = max(1, math.ceil(max(image.size)/128))
    sampled = pixels[::step, ::step].astype(np.float32)
    eligible = ((sampled.min(axis=2) >= 245) & (np.ptp(sampled,axis=2) <= 5)
                & low[::step, ::step])
    yy, xx = np.nonzero(eligible)
    if len(xx) < 64:
        raise ValueError(INVALID)
    x, y = xx*step/(image.width-1), yy*step/(image.height-1)
    if (np.ptp(x) < .6 or np.ptp(y) < .6
            or np.count_nonzero((x < .04) | (x > .96) | (y < .04) | (y > .96)) < 8):
        raise ValueError(INVALID)
    design = np.stack((np.ones(len(x)), x, y),axis=1)
    data = sampled[eligible]
    keep = np.ones(len(x),dtype=bool)
    for _ in range(3):
        coefficients, _, rank, _ = np.linalg.lstsq(design[keep],data[keep],rcond=None)
        if rank != 3:
            raise ValueError(INVALID)
        errors = np.max(np.abs(design @ coefficients-data),axis=1)
        keep = errors <= max(1.5,float(np.percentile(errors,90)))
    corners = np.array([[1,0,0],[1,1,0],[1,0,1],[1,1,1]]) @ coefficients
    residual = float(np.percentile(errors[keep],95))
    if (keep.mean() < .8 or np.ptp(x[keep]) < .6 or np.ptp(y[keep]) < .6
            or residual > 1.5 or corners.min() < 244.5 or corners.max() > 255.5
            or np.ptp(corners,axis=1).max() > 5 or np.ptp(corners,axis=0).max() > 8):
        raise ValueError(INVALID)
    coefficients = coefficients.astype(np.float32)
    field = (coefficients[0] + np.arange(image.width,dtype=np.float32)[None,:,None]/(image.width-1)*coefficients[1]
             + np.arange(image.height,dtype=np.float32)[:,None,None]/(image.height-1)*coefficients[2])
    field = np.clip(field,245,255).astype(np.float32)
    return field, {'background_reference': 'observed_near_white', 'background_samples': len(x),
                   'background_residual_p95': round(residual,4),
                   'background_range': [round(float(field.min()),3),round(float(field.max()),3)]}
