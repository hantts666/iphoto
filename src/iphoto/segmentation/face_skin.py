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
from ..document import empty_mask, validate_mask
from ..masks import encode_bitmap
from .face_models import verified_path
from .prompts import validate_points
from .runtime import prepare_runtime

SKIN_CLASSES = (1, 7, 8, 10, 14)  # skin, ears, nose, neck
PROTECTED_CLASSES = (2, 3, 4, 5, 6, 9, 11, 12, 13, 15, 16, 17, 18)
_backend = None


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


def segment(image, hint, points, *, crop=None, engine=None):
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
    hard = np.isin(labels, SKIN_CLASSES).astype(np.uint8)
    if not hard[py, px]:
        raise ValueError("面部定位点未落在可识别的皮肤上，照片未改变；请重新描述目标")
    # Keep only the face connected to the requested anchor; another face in
    # the crop must not acquire this person's smoothing layer.
    _, components = cv2.connectedComponents(hard, connectivity=8)
    selected = components == components[py, px]
    if selected.mean() < .005 or not np.any(selected & (labels == 10)):
        raise ValueError("未可靠识别到目标面部，照片未改变；请框住单个人脸再试")
    protected = np.isin(labels, PROTECTED_CLASSES).astype(np.uint8)
    protected = cv2.dilate(protected, np.ones((3, 3), np.uint8)) > 0
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
                  label=(hint["label"] + " · 面部皮肤")[:200])
    quality = {
        "model": "BiSeNet · 面部皮肤", "semantic_target": "face_skin",
        "protected_features": ["眼睛", "眉毛", "嘴唇", "头发", "帽子", "衣物"],
        "warnings": ["面部皮肤已自动分区，请放大检查遮挡与边缘"],
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "mask_size": list(image.size), "crop_size": list(patch.size),
        "coverage": round(float(hard.mean()) * patch.width * patch.height / (image.width * image.height) * 100, 2),
    }
    return result, quality
