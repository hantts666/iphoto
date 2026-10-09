"""Register generated pixels from unchanged context before selection compositing.

The cloud may change framing despite an unchanged output size. Only reference
features outside the allowed edit area can anchor the fit. Uncertain geometry
or missing generated coverage fails before any layer is published.
"""
from math import atan2, degrees, hypot

import cv2
import numpy as np
from PIL import Image

MAX_PIXELS = 2048 * 2048


def align_generated(reference, generated, allowed):
    if (reference.mode not in ('RGB', 'RGBA') or generated.mode not in ('RGB', 'RGBA') or allowed.mode != 'L'
            or allowed.size != reference.size or max(reference.size) > 1280
            or min(reference.size) < 64 or generated.width * generated.height > MAX_PIXELS
            or min(generated.size) < 64
            or abs((generated.width/generated.height)/(reference.width/reference.height)-1) > .02):
        raise ValueError('生成图位置参照无效，照片未改变')
    permitted = np.asarray(allowed) > 0
    if not permitted.any():
        raise ValueError('生成精修范围为空，照片未改变')
    if allowed.getextrema() == (255, 255):
        # Replacing the whole frame has no boundary against retained content.
        # Its appearance/framing still goes through the existing image review.
        return generated.copy(), {'status': 'whole_frame_replacement', 'inliers': 0}
    pixels = np.asarray(generated)
    # A spatially constant paint has no geometry to displace. Do not pretend
    # its position was measured or allow a featureless reference to approve a
    # structured replacement such as a new face.
    if np.all(pixels == pixels[0, 0]):
        return generated.copy(), {'status': 'uniform_pixels', 'inliers': 0}
    stable = cv2.dilate(permitted.astype(np.uint8), np.ones((15, 15), np.uint8)) == 0
    if reference.mode == 'RGBA':
        stable &= np.asarray(reference.getchannel('A')) > 240
    if np.count_nonzero(stable) < reference.width * reference.height * .08:
        raise ValueError('范围外的定位参照不足，请保留周围内容后再精修；照片未改变')
    normalized = generated.resize(reference.size, Image.Resampling.LANCZOS)
    detector = cv2.SIFT_create(nfeatures=6000)
    reference_points, reference_features = detector.detectAndCompute(
        cv2.cvtColor(np.asarray(reference.convert('RGB')), cv2.COLOR_RGB2GRAY), stable.astype(np.uint8)*255)
    generated_points, generated_features = detector.detectAndCompute(
        cv2.cvtColor(np.asarray(normalized.convert('RGB')), cv2.COLOR_RGB2GRAY),
        np.uint8(np.asarray(normalized.getchannel('A')) > 240) * 255 if generated.mode == 'RGBA' else None)
    if reference_features is None or generated_features is None:
        raise ValueError('无法核对生成图与原图的位置，照片未改变')
    matcher = cv2.BFMatcher()
    forward = matcher.knnMatch(generated_features, reference_features, k=2)
    backward = matcher.knnMatch(reference_features, generated_features, k=2)
    reverse = {pair[0].queryIdx: pair[0].trainIdx for pair in backward
               if len(pair) == 2 and pair[0].distance < pair[1].distance*.7}
    matches = [pair[0] for pair in forward if len(pair) == 2
               and pair[0].distance < pair[1].distance*.7
               and reverse.get(pair[0].trainIdx) == pair[0].queryIdx]
    if len(matches) < 24:
        raise ValueError('生成图缺少可靠的位置对应，照片未改变')
    starts = np.float32([generated_points[m.queryIdx].pt for m in matches])
    ends = np.float32([reference_points[m.trainIdx].pt for m in matches])
    matrix, flags = cv2.estimateAffinePartial2D(starts, ends, method=cv2.RANSAC,
        ransacReprojThreshold=2, maxIters=4000, confidence=.999)
    if matrix is None or flags is None or not np.isfinite(matrix).all():
        raise ValueError('生成图位置校正失败，照片未改变')
    inliers = flags.ravel() > 0
    count = int(inliers.sum())
    ratio = count/len(matches)
    scale = hypot(matrix[0, 0], matrix[1, 0])
    angle = degrees(atan2(matrix[1, 0], matrix[0, 0]))
    spread = np.ptp(ends[inliers], axis=0) if count else np.zeros(2)
    errors = np.linalg.norm(starts @ matrix[:, :2].T + matrix[:, 2] - ends, axis=1)
    if (count < 24 or ratio < .2 or not .8 <= scale <= 1.25 or abs(angle) > 8
            or hypot(*matrix[:, 2]) > .2 * hypot(*reference.size)
            or np.any(spread < np.array(reference.size)*.35)
            or np.percentile(errors[inliers], 95) > 2.5):
        raise ValueError('生成图构图变化过大或定位不稳定，照片未改变')
    # Match on the <=1280px reference, then transform original generated pixels
    # at their own resolution; do not reduce the patch to the matching proxy.
    width, height = generated.size
    resize = np.diag([width/reference.width, height/reference.height, 1.])
    transform = (resize @ np.vstack([matrix, [0, 0, 1]]) @ np.linalg.inv(resize))[:2]
    coverage = cv2.warpAffine(np.ones((height, width), np.float32), transform, generated.size,
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    target = np.asarray(allowed.resize(generated.size, Image.Resampling.NEAREST)) > 0
    if np.any(coverage[target] < .999):
        raise ValueError('校正后的生成图未覆盖完整选区，照片未改变')
    if generated.mode == 'RGBA':
        rgba = pixels.astype(np.float32)
        rgba[..., :3] *= rgba[..., 3:] / 255
        moved = cv2.warpAffine(rgba, transform, generated.size, flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        moved[..., :3] /= np.maximum(moved[..., 3:] / 255, 1e-8)
        moved = np.rint(np.clip(moved, 0, 255)).astype(np.uint8)
    else:
        moved = cv2.warpAffine(pixels, transform, generated.size, flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    # Missing border pixels occur only outside the allowed range. Keep their
    # reference pixels instead of storing an artificial black border.
    original = np.asarray(reference.convert(generated.mode).resize(generated.size, Image.Resampling.LANCZOS))
    moved[coverage < .999] = original[coverage < .999]
    return Image.fromarray(moved), {'status': 'aligned', 'matches': len(matches),
        'inliers': count, 'inlier_ratio': round(ratio, 4), 'scale': round(scale, 6),
        'rotation_degrees': round(angle, 4), 'translation_reference_px': matrix[:, 2].round(4).tolist(),
        'reference_size': list(reference.size), 'output_size': list(generated.size),
        'inlier_error_p95': round(float(np.percentile(errors[inliers], 95)), 4)}
