"""Local topology recovery from explicit, well-separated positive/negative colors.

Low contrast or incoherent samples decline refinement. Large excluded regions
are not reopened. RGB propagation is anchored to the user's negative marks;
unrelated components and all pixels outside the local support stay unchanged.
This is a conservative high-contrast adapter, not a semantic sky classifier.
"""
from time import perf_counter

import cv2
import numpy as np
from PIL import Image

from ..document import empty_mask, raster_mask
from ..masks import encode_bitmap
from ..matting.neural import solve, backend

MAX_ROI = 2_000_000


def recover(image, mask, points, *, engine=None, progress=None):
    positive = [p for p in points if p[2] == 1]
    negative = [p for p in points if p[2] == 0]
    if not positive or not negative:
        return None
    started = perf_counter()
    size = image.size
    def locations(group):
        return [(round(p[0]*(size[0]-1)), round(p[1]*(size[1]-1))) for p in group]
    pos, neg = locations(positive), locations(negative)
    samples = []
    for group in (pos, neg):
        colors = []
        for x, y in group:
            box = (max(0,x-2), max(0,y-2), min(size[0],x+3), min(size[1],y+3))
            patch = np.asarray(image.crop(box).convert("RGB"), np.float32)
            center = patch[y-box[1],x-box[0]]
            nearby = patch.reshape(-1,3)
            # Thin branches naturally share a patch with background. Use the
            # clicked color's cluster, not the median of both semantic classes.
            coherent = nearby[np.linalg.norm(nearby-center,axis=1)<40]
            if len(coherent)<3:
                return None
            color = np.median(coherent,axis=0)
            colors.append(color)
        median = np.median(colors,axis=0)
        if max(np.linalg.norm(c-median) for c in colors) > 45:
            return None
        samples.append(median)
    foreground, background = samples
    delta = foreground-background
    separation = float(np.linalg.norm(delta))
    if separation < 100:
        return None
    original = np.asarray(raster_mask(mask, size))
    hard = original > 127
    if any(not hard[y,x] for x,y in pos) or any(hard[y,x] for x,y in neg):
        return None
    _, labels, stats, _ = cv2.connectedComponentsWithStats((~hard).astype(np.uint8), connectivity=8)
    ids = set(int(labels[y,x]) for x,y in neg)
    if 0 in ids or any(stats[i,cv2.CC_STAT_AREA] > hard.size*.15 for i in ids):
        return None
    left = min(int(stats[i,0]) for i in ids)
    top = min(int(stats[i,1]) for i in ids)
    right = max(int(stats[i,0]+stats[i,2]) for i in ids)
    bottom = max(int(stats[i,1]+stats[i,3]) for i in ids)
    expansion = min(512, max(64, round(max(size)/1600*192)))
    left, top = max(0,left-expansion), max(0,top-expansion)
    right, bottom = min(size[0],right+expansion), min(size[1],bottom+expansion)
    if (right-left)*(bottom-top) > MAX_ROI:
        return None
    support = np.isin(labels[top:bottom,left:right], list(ids))
    del labels
    local = image.crop((left,top,right,bottom))
    rgb = np.asarray(local.convert("RGB"),np.float32)
    projection = ((rgb-background)*delta).sum(axis=2)/(separation*separation)
    residual = np.linalg.norm(rgb-(background+projection[:,:,None]*delta),axis=2)
    colors = ((projection<.35)&(residual<35)).astype(np.uint8)
    colors = cv2.morphologyEx(colors,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)))
    _, regions = cv2.connectedComponents(colors,connectivity=8)
    anchors = {int(regions[y-top,x-left]) for x,y in neg} - {0}
    if not anchors:
        return None
    grown = np.isin(regions,list(anchors)).astype(np.uint8)
    support |= cv2.dilate(grown,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(25,25))) > 0
    # The excluded object is matting foreground; invert it back to the user's
    # retained region afterwards. Strong original colors provide constraints.
    trimap = (~hard[top:bottom,left:right]).astype(np.uint8)*255
    trimap[support] = 128
    trimap[support&(projection<.20)&(residual<25)] = 255
    trimap[support&(projection>.82)&(residual<25)] = 0
    if not ((trimap==0)&support).any() or not ((trimap==255)&support).any():
        return None
    pixels, tiles = solve(local,trimap,engine=engine,progress=progress)
    pixels = 255-pixels
    result_pixels = original.copy()
    region = result_pixels[top:bottom,left:right]
    region[support] = pixels[support]
    # No result is published if inference violates a user's anchor.
    if any(result_pixels[y,x]<230 for x,y in pos) or any(result_pixels[y,x]>25 for x,y in neg):
        raise ValueError("细节结果未满足提示点，原选区保留；请把点放在区域内部")
    result = empty_mask()
    result.update(bitmap=encode_bitmap(Image.fromarray(result_pixels),sampling="alpha",preserve_resolution=True),label=mask["label"])
    if "edge_protection" in mask:
        result["edge_protection"] = mask["edge_protection"]
    model = engine or backend()
    return result, {"backend": "ViTMatte-S · ONNX", "provider": model.provider,
                    "elapsed_ms": round((perf_counter()-started)*1000,1), "tiles": tiles,
                    "mask_size": list(size), "roi": [left,top,right,bottom],
                    "color_separation": round(separation,1), "support_pixels": int(support.sum()),
                    "reopened_pixels": int(((result_pixels>127)&(~hard)).sum()),
                    "coverage": round(float((result_pixels>0).mean())*100,1),
                    "partial_pixels": int(((result_pixels>0)&(result_pixels<255)).sum()),
                    "warnings": [model.fallback] if getattr(model,"fallback","") else []}
