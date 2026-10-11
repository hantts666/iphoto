"""Check global foreground framing without warping either its RGB or alpha.

This measures reliable opaque texture correspondences. It cannot certify facial
identity, nonrigid changes or original fine hair, which still need image review.
"""
from math import atan2, degrees, hypot, isfinite

import cv2
import numpy as np
from PIL import Image

from .pixel_patch import MAX_PIXELS


UNCERTAIN = '无法可靠核对AI重建人物的位置，照片未改变；可重试或使用原图通道抠图'
DRIFT = 'AI重建改变了人物位置或大小，未应用；可重试或使用原图通道抠图'


def validate_position(facts, original_size, generated_size, pixel_sha):
    """Validate worker evidence and bind it to this exact generated patch."""
    if (not isinstance(facts, dict) or facts.get('status') != 'consistent'
            or facts.get('original_size') != list(original_size)
            or facts.get('generated_size') != list(generated_size)
            or facts.get('pixel_sha256') != pixel_sha):
        raise ValueError('AI重建人物的位置参照不一致，照片未改变')
    reference = facts.get('reference_size')
    spread = facts.get('spread')
    if (not isinstance(reference, list) or len(reference) != 2
            or any(type(v) is not int or not 1 <= v <= 1280 for v in reference)
            or any(reference[i] > generated_size[i] for i in (0,1))
            or abs((reference[0]/reference[1])/(generated_size[0]/generated_size[1])-1) > .02
            or not isinstance(spread, list) or len(spread) != 2
            or any(type(v) not in (int,float) or not isfinite(v) or not reference[i]*.35 <= v <= reference[i]
                   for i,v in enumerate(spread))):
        raise ValueError(UNCERTAIN)
    count, total = facts.get('inliers'), facts.get('matches')
    names = ('inlier_ratio','scale','rotation_degrees','displacement_p95','fit_error_p95')
    if (type(count) is not int or type(total) is not int or not 24 <= count <= total <= 6000
            or any(type(facts.get(name)) not in (int,float) or not isfinite(facts[name]) for name in names)
            or not .2 <= facts['inlier_ratio'] <= 1
            or abs(facts['inlier_ratio']-count/total) > .0001
            or not 0 <= facts['fit_error_p95'] <= 2.5):
        raise ValueError(UNCERTAIN)
    if (abs(facts['scale']-1) > .02 or abs(facts['rotation_degrees']) > 1
            or not 0 <= facts['displacement_p95'] <= .015*hypot(*reference)):
        raise ValueError(DRIFT)
    return facts


def check_position(original, rgba, pixel_sha):
    """Refuse uncertain or drifting framing before cloud review can accept it."""
    if (original.mode not in ('RGB','RGBA') or rgba.mode != 'RGBA' or min(rgba.size) < 64
            or rgba.width*rgba.height > MAX_PIXELS):
        raise ValueError(UNCERTAIN)
    picture = rgba.copy()
    picture.thumbnail((1280,1280),Image.Resampling.LANCZOS)
    reference = original.resize(picture.size,Image.Resampling.LANCZOS)
    reference_mask = None
    if reference.mode == 'RGBA':
        # Invisible RGB is not a framing anchor. The generator and reviewer
        # see the actual reference composited on white, not those hidden bytes.
        reference_mask = cv2.erode(np.uint8(np.asarray(reference.getchannel('A')) > 240),np.ones((7,7),np.uint8))*255
        reference = Image.alpha_composite(Image.new('RGBA',reference.size,'white'),reference).convert('RGB')
    core = cv2.erode(np.uint8(np.asarray(picture.getchannel('A')) > 240),np.ones((7,7),np.uint8))*255
    detector = cv2.SIFT_create(nfeatures=6000)
    source_points, source_features = detector.detectAndCompute(
        cv2.cvtColor(np.asarray(reference),cv2.COLOR_RGB2GRAY),reference_mask)
    points, features = detector.detectAndCompute(
        cv2.cvtColor(np.asarray(picture.convert('RGB')),cv2.COLOR_RGB2GRAY),core)
    if source_features is None or features is None:
        raise ValueError(UNCERTAIN)
    matcher = cv2.BFMatcher()
    forward = matcher.knnMatch(features,source_features,k=2)
    backward = matcher.knnMatch(source_features,features,k=2)
    reverse = {pair[0].queryIdx:pair[0].trainIdx for pair in backward
               if len(pair)==2 and pair[0].distance < pair[1].distance*.7}
    matches = [pair[0] for pair in forward if len(pair)==2 and pair[0].distance < pair[1].distance*.7
               and reverse.get(pair[0].trainIdx)==pair[0].queryIdx]
    if len(matches) < 24:
        raise ValueError(UNCERTAIN)
    starts = np.float32([points[m.queryIdx].pt for m in matches])
    ends = np.float32([source_points[m.trainIdx].pt for m in matches])
    matrix, flags = cv2.estimateAffinePartial2D(starts,ends,method=cv2.RANSAC,
        ransacReprojThreshold=2,maxIters=4000,confidence=.999)
    if matrix is None or flags is None or not np.isfinite(matrix).all():
        raise ValueError(UNCERTAIN)
    inliers = flags.ravel() > 0
    count = int(inliers.sum())
    if not count:
        raise ValueError(UNCERTAIN)
    errors = np.linalg.norm(starts @ matrix[:,:2].T+matrix[:,2]-ends,axis=1)
    displacement = np.linalg.norm(starts[inliers]-ends[inliers],axis=1)
    facts = {'status':'consistent','original_size':list(original.size),'generated_size':list(rgba.size),
             'reference_size':list(reference.size),'pixel_sha256':pixel_sha,
             'matches':len(matches),'inliers':count,'inlier_ratio':round(count/len(matches),6),
             'spread':np.ptp(ends[inliers],axis=0).astype(float).tolist(),
             'scale':hypot(matrix[0,0],matrix[1,0]),
             'rotation_degrees':degrees(atan2(matrix[1,0],matrix[0,0])),
             'displacement_p95':float(np.percentile(displacement,95)),
             'fit_error_p95':float(np.percentile(errors[inliers],95))}
    return validate_position(facts,original.size,rgba.size,pixel_sha)
