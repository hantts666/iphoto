"""Face skin masks from an explicitly localized face, in the pixel worker.

BiSeNet preprocessing and class IDs follow yakhyo/face-parsing. The bounded
adapter, selection policy and inward alpha edges are specific to iPhoto.
"""

import os
from time import perf_counter

import cv2
import numpy as np
from PIL import Image

from ..ai_grounding import crop_pixels, region_crop
from ..document import empty_mask, validate_mask, raster_mask
from ..masks import encode_bitmap
from .face_models import verified_path
from .face_detection import validate_features
from .prompts import validate_points
from .runtime import prepare_runtime

SKIN_CLASSES = (1, 7, 8, 10)  # face skin, ears and nose; neck is a separate region
PROTECTED_CLASSES = (2, 3, 4, 5, 6, 9, 11, 12, 13, 14, 15, 16, 17, 18)
_backend = None


def feature_guard_pixels(features, labels):
    """Conservative landmark guards supplement ambiguous parsing under a hat."""
    guard = np.zeros((512,512),np.uint8)
    for name,points in features.items():
        points = np.asarray(points,np.float64)
        span = np.linalg.norm(points[1]-points[0])
        if span < 1:
            continue
        center = points.mean(0)
        angle = float(np.degrees(np.arctan2(*(points[1]-points[0])[::-1])))
        # A small oriented disk at each eye; the mouth uses both corners as a
        # capsule. These are protections, not replacement face boundaries.
        if name=='eyes':
            centers = points
            axes = (max(1,round(span*.28)),max(1,round(span*.18)))
        else:
            centers = [center]
            axes = (max(1,round(span*.68)),max(1,round(span*.32)))
        for x,y in centers:
            if 0 <= x < 512 and 0 <= y < 512:
                # Occluded landmark guesses can fall on the nose or hat. Only
                # protect a visible plausible feature/skin centre, not those
                # guesses or background; semantic features remain protected.
                label = labels[min(511,round(y)),min(511,round(x))]
                plausible = (1,2,3,4,5,6) if name=='eyes' else (1,11,12,13)
                if label not in plausible:
                    continue
                cv2.ellipse(guard,(round(x),round(y)),axes,angle,0,360,255,-1)
    return guard>0


class FaceParser:
    def __init__(self):
        prepare_runtime()
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4, os.cpu_count() or 4)
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(verified_path()), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def predict(self, image):
        rgb = cv2.resize(np.asarray(image.convert("RGB")), (512, 512)).astype(np.float32) / 255
        rgb = (rgb - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        logits = self.session.run(None, {
            self.session.get_inputs()[0].name: rgb.transpose(2, 0, 1)[None]
        })[0]
        if logits.shape != (1, 19, 512, 512) or not np.isfinite(logits).all():
            raise ValueError("面部皮肤模型输出无效，照片未改变")
        return logits[0].argmax(0).astype(np.uint8)


def backend():
    global _backend
    if _backend is None:
        _backend = FaceParser()
    return _backend


def segment(image, hint, points, *, crop=None, engine=None, target="face_skin", recover_anchor=False, features=None):
    started = perf_counter()
    hint = validate_mask(hint)
    points = validate_points(points or [])
    if len(points) != 1 or points[0][2] != 1:
        raise ValueError("面部皮肤分区需要一个有效的内部定位点，照片未改变")
    # This broader crop comes from the original face localization. Cropping to
    # the bare-skin box alone removes facial context and can misclassify lips.
    box = crop_pixels(crop or region_crop(hint, (384, 384)), image.size)
    left, top, right, bottom = box
    patch = image.crop(box).convert("RGB")
    labels = np.asarray((engine or backend()).predict(patch))
    if labels.shape != (512, 512) or labels.dtype != np.uint8 or labels.max() > 18:
        raise ValueError("面部皮肤分区无效，照片未改变")
    x, y = points[0][:2]
    px = round((x * image.width - left) / patch.width * 511)
    py = round((y * image.height - top) / patch.height * 511)
    if not (0 <= px < 512 and 0 <= py < 512):
        raise ValueError("面部定位点不在放大范围内，照片未改变")
    if target not in ("face", "face_skin"):
        raise ValueError("面部目标类型无效")
    hard = np.isin(labels, tuple(range(1,14)) if target == "face" else SKIN_CLASSES).astype(np.uint8)
    recovered = False
    if not hard[py,px] and recover_anchor:
        # A detector's nose landmark can lie under a hat. Recover only from
        # the parser's visible nose pixels inside this detector's face hint;
        # never choose an arbitrary skin-coloured region elsewhere in the crop.
        support = raster_mask(hint,image.size).crop(box).resize((512,512),Image.Resampling.NEAREST)
        ys,xs = np.nonzero((labels==10)&(np.asarray(support)>0))
        if len(xs):
            closest = np.argmin((xs-px)**2+(ys-py)**2)
            px,py = int(xs[closest]),int(ys[closest])
            recovered = True
    if not hard[py, px]:
        raise ValueError("面部定位点未落在可识别的皮肤上，照片未改变；请重新描述目标")
    # Keep only the face connected to the requested anchor; another face in
    # the crop must not acquire this person's smoothing layer.
    _, components = cv2.connectedComponents(hard, connectivity=8)
    selected = components == components[py, px]
    if selected.mean() < .005 or not np.any(selected & (labels == 10)):
        raise ValueError("未可靠识别到目标面部，照片未改变；请框住单个人脸再试")
    protected = np.isin(labels, (14,15,16,17,18) if target == "face" else PROTECTED_CLASSES).astype(np.uint8)
    protected = cv2.dilate(protected, np.ones((3, 3), np.uint8)) > 0
    if target=='face_skin' and features is not None:
        features = validate_features(features)
        mapped = {name:[[(x*image.width-left)/patch.width*511,(y*image.height-top)/patch.height*511]
                        for x,y in points] for name,points in features.items()}
        protected |= feature_guard_pixels(mapped,labels)
    hard = (selected & ~protected).astype(np.uint8)
    # An inward transition preserves exact zeros on eyes, lips, hair, clothes
    # and background. RGB-guided outward expansion could fill those holes.
    distance = cv2.distanceTransform(hard, cv2.DIST_L2, 5)
    alpha = Image.fromarray(np.rint(np.minimum(distance / 2, 1) * 255).astype(np.uint8))
    alpha = alpha.resize(patch.size, Image.Resampling.BILINEAR)
    support = Image.fromarray(hard * 255).resize(patch.size, Image.Resampling.NEAREST)
    alpha.paste(0, mask=support.point(lambda v: 255 - v))
    full = Image.new("L", image.size)
    full.paste(alpha, (left, top))
    result = empty_mask()
    result.update(bitmap=encode_bitmap(full, sampling="alpha", preserve_resolution=True),
                  label=(hint["label"] + (" · 人脸" if target == "face" else " · 面部皮肤"))[:200],
                  semantic_target=target)
    quality = {
        "model": "BiSeNet · 人脸" if target == "face" else "BiSeNet · 面部皮肤", "semantic_target": target,
        "protected_features": ["头发", "帽子", "衣物", "颈部"] if target == "face" else ["眼睛", "眉毛", "嘴唇", "头发", "帽子", "衣物"],
        "warnings": [("人脸" if target == "face" else "面部皮肤") + "已自动分区，请放大检查遮挡与边缘"],
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "mask_size": list(image.size), "crop_size": list(patch.size),
        "coverage": round(float(hard.mean()) * patch.width * patch.height / (image.width * image.height) * 100, 2),
        "anchor_recovered": recovered,
        "landmark_protection": target=='face_skin' and features is not None,
    }
    return result, quality
