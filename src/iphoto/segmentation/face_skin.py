"""Face skin masks from an explicitly localized face, in the pixel worker.

BiSeNet preprocessing and class IDs follow yakhyo/face-parsing. The bounded
adapter, scope policy and semantic part transitions are specific to iPhoto.
"""

import os
from time import perf_counter

import cv2
import numpy as np
from PIL import Image

from ..ai_grounding import crop_pixels, region_crop
from ..document import empty_mask, validate_mask, raster_mask, spatial_mask
from ..masks import encode_bitmap
from .face_models import verified_path
from .face_detection import validate_features
from .face_parts import PARTS, validate_part
from .prompts import validate_points
from .runtime import prepare_runtime

SKIN_CLASSES = (1, 7, 8, 10)  # face skin, ears and nose; neck is a separate region
PROTECTED_CLASSES = (2, 3, 4, 5, 6, 9, 11, 12, 13, 14, 15, 16, 17, 18)
_backend = None


def feature_guard_pixels(features, labels, face_width=None):
    """Conservative landmark guards supplement ambiguous parsing under a hat."""
    height, width = labels.shape
    guard = np.zeros(labels.shape,np.uint8)
    if face_width is None:
        face_width = cv2.boundingRect(np.isin(labels, tuple(range(1,14))).astype(np.uint8))[2]
    for name,points in features.items():
        points = np.asarray(points,np.float64)
        span = np.linalg.norm(points[1]-points[0])
        if span < 1:
            continue
        center = points.mean(0)
        # Profile/occlusion guesses can collapse two landmarks onto a cheek.
        # Such a pair needs nearby semantic evidence. Well-separated frontal
        # landmarks may still protect features misclassified as plain skin.
        uncertain = span < max(1, face_width) * .18
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
            if 0 <= x < width and 0 <= y < height:
                # Occluded landmark guesses can fall on the nose or hat. Only
                # protect a visible plausible feature/skin centre, not those
                # guesses or background; semantic features remain protected.
                label = labels[min(height-1,round(y)),min(width-1,round(x))]
                plausible = (1,2,3,4,5,6) if name=='eyes' else (1,11,12,13)
                if label not in plausible:
                    continue
                if uncertain and label == 1:
                    radius = max(2, round(span * .35))
                    nearby = labels[max(0,round(y)-radius):min(height,round(y)+radius+1),
                                    max(0,round(x)-radius):min(width,round(x)+radius+1)]
                    feature_classes = (2,3,4,5,6) if name == 'eyes' else (11,12,13)
                    if not np.isin(nearby, feature_classes).any():
                        continue
                cv2.ellipse(guard,(round(x),round(y)),axes,angle,0,360,255,-1)
    return guard>0


def native_labels(scores, size, bounds=None):
    """Lift neural scores before argmax; never enlarge a binary label map.

    Two-dimensional blocks bound working score memory to 64x1024x19 floats,
    including very wide photos. The model itself still runs at 512 pixels.
    """
    from ..masks import MAX_MASK_PIXELS, MAX_MASK_SIDE
    if (scores.shape != (19, 512, 512) or scores.dtype != np.float32
            or not np.isfinite(scores).all()):
        raise ValueError("面部分区模型分数无效")
    width, height = size
    if (any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= MAX_MASK_SIDE for v in size)
            or width * height > MAX_MASK_PIXELS):
        raise ValueError("面部边界范围超过尺寸上限")
    labels = np.zeros((height, width), np.uint8)
    x0, y0, x1, y1 = bounds or (0, 0, width, height)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("面部边界计算范围无效")
    source = np.ascontiguousarray(scores.transpose(1, 2, 0))
    for top in range(y0, y1, 64):
        bottom = min(top + 64, y1)
        y = (np.arange(top, bottom, dtype=np.float32) + .5) * 512 / height - .5
        for left in range(x0, x1, 1024):
            right = min(left + 1024, x1)
            x = (np.arange(left, right, dtype=np.float32) + .5) * 512 / width - .5
            xx = np.broadcast_to(x, (bottom-top, right-left))
            yy = np.broadcast_to(y[:, None], xx.shape)
            block = cv2.remap(source, xx, yy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            labels[top:bottom, left:right] = block.argmax(2).astype(np.uint8)
    return labels


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

    def _scores(self, image):
        rgb = cv2.resize(np.asarray(image.convert("RGB")), (512, 512)).astype(np.float32) / 255
        rgb = (rgb - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        logits = self.session.run(None, {
            self.session.get_inputs()[0].name: rgb.transpose(2, 0, 1)[None]
        })[0]
        if logits.shape != (1, 19, 512, 512) or not np.isfinite(logits).all():
            raise ValueError("面部皮肤模型输出无效，照片未改变")
        return logits[0]

    def predict(self, image):
        return self._scores(image).argmax(0).astype(np.uint8)

    def predict_native(self, image, *, progress=None):
        if progress is not None:
            progress('face_infer')
        scores = self._scores(image)
        rough = np.isin(scores.argmax(0), tuple(range(1,14))).astype(np.uint8)
        x, y, w, h = cv2.boundingRect(rough)
        if not w or not h:
            return np.zeros((image.height, image.width), np.uint8)
        # Spend the native classification work on the model's face context.
        # Include two model cells and two source pixels around it for score
        # interpolation and protection; other crop content remains background.
        bounds = (max(0, int(np.floor((x-2)*image.width/512))-2),
                  max(0, int(np.floor((y-2)*image.height/512))-2),
                  min(image.width, int(np.ceil((x+w+2)*image.width/512))+2),
                  min(image.height, int(np.ceil((y+h+2)*image.height/512))+2))
        if progress is not None:
            progress('face_boundary')
        return native_labels(scores, image.size, bounds)


def backend(*, progress=None):
    global _backend
    if _backend is None:
        if progress is not None:
            progress('face_model')
        _backend = FaceParser()
    return _backend


def segment(image, hint, points, *, crop=None, engine=None, target="face_skin", recover_anchor=False, features=None, scope="full", context_hint=None, part="all", progress=None, require_precision=False):
    started = perf_counter()
    hint = validate_mask(hint)
    if scope not in ('full', 'region'):
        raise ValueError("面部编辑范围无效")
    part = validate_part(part, target, scope)
    context_hint = validate_mask(context_hint) if context_hint is not None else hint
    points = validate_points(points or [])
    if len(points) != 1 or points[0][2] != 1:
        raise ValueError("面部皮肤分区需要一个有效的内部定位点，照片未改变")
    if progress is not None:
        progress('face_prepare')
    # This broader crop comes from the original face localization. Cropping to
    # the bare-skin box alone removes facial context and can misclassify lips.
    box = crop_pixels(crop or region_crop(hint, (384, 384)), image.size)
    left, top, right, bottom = box
    patch = image.crop(box).convert("RGB")
    precision = False
    fallback = False
    feature_refinement = False
    part_alpha = None
    if features is not None:
        features = validate_features(features)
    if engine is None and part in PARTS and features is not None:
        from . import face_precision
        if face_precision.available():
            landmarks = np.asarray([*features['eyes'], points[0][:2], *features['mouth']]) * image.size - (left,top)
            try:
                parser=face_precision.backend(progress=progress) if progress is not None else face_precision.backend()
                if callable(getattr(parser,'predict_part',None)):
                    labels,part_alpha=parser.predict_part(patch,landmarks,part,progress=progress)
                elif progress is None:
                    labels = parser.predict_native(patch,landmarks)
                else:
                    labels = parser.predict_native(patch,landmarks,progress=progress)
                precision = True
            except Exception:
                fallback = True
                if progress is not None and not require_precision:
                    progress('face_fallback')
    if require_precision and (not precision or part_alpha is None):
        raise ValueError('精细五官模型未能提供连续范围，原范围保留')
    provided_engine = engine is not None
    if engine is None and not precision:
        engine = backend(progress=progress) if progress is not None else backend()
    if precision:
        native = True
    else:
        native = callable(getattr(engine, "predict_native", None))
        if native and progress is not None and not provided_engine:
            labels = np.asarray(engine.predict_native(patch,progress=progress))
        else:
            labels = np.asarray(engine.predict_native(patch) if native else engine.predict(patch))
    expected = (patch.height, patch.width) if native else (512, 512)
    if labels.shape != expected or labels.dtype != np.uint8 or labels.max() > 18:
        raise ValueError("面部皮肤分区无效，照片未改变")
    continuous = precision and part_alpha is not None
    if continuous and (part_alpha.shape!=expected or part_alpha.dtype!=np.uint8):
        raise ValueError('连续五官边缘无效，照片未改变')
    if progress is not None:
        progress('face_protect')
    grid_height, grid_width = labels.shape
    x, y = points[0][:2]
    scale_x, scale_y = (grid_width, grid_height) if native else (511, 511)
    px = round((x * image.width - left) / patch.width * scale_x)
    py = round((y * image.height - top) / patch.height * scale_y)
    if not (0 <= px < grid_width and 0 <= py < grid_height):
        raise ValueError("面部定位点不在放大范围内，照片未改变")
    if target not in ("face", "face_skin"):
        raise ValueError("面部目标类型无效")
    hard = np.isin(labels, tuple(range(1,14)) if target == "face" else SKIN_CLASSES).astype(np.uint8)
    recovered = False
    if not hard[py,px] and recover_anchor:
        # A detector's nose landmark can lie under a hat. Recover only from
        # the parser's visible nose pixels inside this detector's face hint;
        # never choose an arbitrary skin-coloured region elsewhere in the crop.
        support = raster_mask(context_hint,image.size).crop(box).resize((grid_width,grid_height),Image.Resampling.NEAREST)
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
    if part == 'lips':
        # Confirm the complete face at its nose first, then isolate its lips.
        # The mouth class includes the interior, not a separate teeth class.
        protected = (~np.isin(labels, (1,12,13) if continuous else PARTS[part]['classes'])).astype(np.uint8)
    protected = protected>0 if continuous and part=='lips' else cv2.dilate(protected, np.ones((3, 3), np.uint8)) > 0
    if target=='face_skin' and features is not None:
        features = validate_features(features)
        mapped = {name:[[(x*image.width-left)/patch.width*scale_x,(y*image.height-top)/patch.height*scale_y]
                        for x,y in points] for name,points in features.items()}
        face_width = cv2.boundingRect(selected.astype(np.uint8))[2]
        protected |= feature_guard_pixels(mapped,labels,face_width)
    hard = (selected & ~protected).astype(np.uint8)
    allowed = hard.copy() if continuous else None
    if part in PARTS:
        hard &= np.isin(labels, PARTS[part]['classes']).astype(np.uint8)
        # Nearby faces can touch in the parser's complete-face component.
        # A supplied identity context limits parts to the localized person.
        context = raster_mask(context_hint, image.size).crop(box)
        hard &= (np.asarray(context.resize((grid_width,grid_height),Image.Resampling.NEAREST)) > 0).astype(np.uint8)
        if continuous:
            allowed &= np.isin(labels,(1,*PARTS[part]['classes'])).astype(np.uint8)
            allowed &= (np.asarray(context)>0).astype(np.uint8)
    scope_alpha = None
    scope_feather = 0
    if scope == 'region':
        # Context identifies the complete face. Only the requested part may be
        # edited; even a recovered nose anchor cannot expand this boundary.
        scope_alpha = raster_mask(hint, image.size).crop(box)
        grid_scope = np.asarray(scope_alpha.resize((grid_width,grid_height), Image.Resampling.NEAREST))
        hard &= (grid_scope > 0).astype(np.uint8)
        if continuous:
            allowed &= (grid_scope>0).astype(np.uint8)
        if not hard.any():
            label = PARTS[part]['label'] if part in PARTS else '面部皮肤'
            raise ValueError(f"指定部位内未识别到{label}，照片未改变；请调整描述")
        # Local complexion/light edits need a soft transition inside their
        # spatial limit, without widening semantic eye/lip protection holes.
        bounds = scope_alpha.getbbox()
        scope_feather = 2 if part == 'lips' else max(2,min(48,round(min(bounds[2]-bounds[0],bounds[3]-bounds[1])*.12)))
        extent = (np.asarray(scope_alpha)>0).astype(np.uint8)
        inside = cv2.distanceTransform(np.pad(extent,1),cv2.DIST_L2,5)[1:-1,1:-1]
        from PIL import ImageChops
        fade = Image.fromarray(np.rint(np.minimum(inside/scope_feather,1)*255).astype(np.uint8))
        scope_alpha = ImageChops.multiply(scope_alpha,fade)
    # Precision parts use continuous neural scores without the extra binary
    # erosion. Anatomy, person and spatial limits still gate every pixel.
    # The base parser retains its existing source-pixel inward transition.
    if continuous:
        alpha=Image.fromarray(np.where(allowed>0,part_alpha,0).astype(np.uint8))
    else:
        distance = cv2.distanceTransform(hard, cv2.DIST_L2, 5)
        alpha = Image.fromarray(np.rint(np.minimum(distance / 2, 1) * 255).astype(np.uint8))
        alpha = alpha.resize(patch.size, Image.Resampling.BILINEAR)
        support = Image.fromarray(hard * 255).resize(patch.size, Image.Resampling.NEAREST)
        alpha.paste(0, mask=support.point(lambda v: 255 - v))
    if (engine is not None and not provided_engine and native and target == 'face_skin'
            and part == 'all' and features is not None):
        from . import face_precision
        if face_precision.available():
            try:
                from .face_feature_fusion import MAX_PIXELS, refine
                if patch.width * patch.height > MAX_PIXELS:
                    raise ValueError('精细面部皮肤范围超过局部预算')
                parser = face_precision.backend(progress=progress) if progress is not None else face_precision.backend()
                if callable(getattr(parser, 'predict_skin', None)):
                    landmarks = np.asarray([*features['eyes'], points[0][:2], *features['mouth']]) * image.size - (left, top)
                    fine_labels, skin_alpha = parser.predict_skin(patch, landmarks, progress=progress)
                    context = np.asarray(raster_mask(context_hint, image.size).crop(box))
                    alpha = Image.fromarray(refine(np.asarray(alpha), labels, fine_labels, skin_alpha, selected, context))
                    feature_refinement = True
            except Exception:
                fallback = True
                if progress is not None:
                    progress('face_fallback')
    if scope_alpha is not None:
        from PIL import ImageChops
        alpha = ImageChops.multiply(alpha, scope_alpha)
    if (continuous or feature_refinement) and not alpha.getbbox():
        raise ValueError('五官类别分数不足以生成可靠范围，照片未改变')
    full = Image.new("L", image.size)
    full.paste(alpha, (left, top))
    result = empty_mask()
    if progress is not None:
        progress('face_encode')
    result.update(bitmap=encode_bitmap(full, sampling="alpha", preserve_resolution=True),
                  label=(hint["label"] + (" · " + PARTS[part]['label'] if part in PARTS else " · 面部局部" if scope == 'region' else " · 人脸" if target == "face" else " · 面部皮肤"))[:200],
                  semantic_target=target)
    if part in PARTS:
        result["face_part"] = part
        result['face_part_scope'] = spatial_mask(hint)
    quality = {
        "model": ("BiSeNet + FaRL LaPa" if feature_refinement else "FaRL LaPa" if precision else "BiSeNet") + (" · " + PARTS[part]['label'] if part in PARTS else " · 人脸" if target == "face" else " · 面部皮肤"), "semantic_target": target,
        "protected_features": ["嘴内", "面部皮肤", "鼻子", "眼睛", "眉毛", "头发", "帽子", "衣物"] if part == 'lips' else ["头发", "帽子", "衣物", "颈部"] if target == "face" else ["眼睛", "眉毛", "嘴唇", "头发", "帽子", "衣物"],
        "warnings": [("嘴唇" if part == 'lips' else "人脸" if target == "face" else "面部皮肤") + "已自动分区，请放大检查遮挡与边缘"],
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "mask_size": list(image.size), "crop_size": list(patch.size),
        "boundary_grid": "source" if native else "model", "boundary_size": [grid_width, grid_height],
        "coverage": round((np.count_nonzero(np.asarray(alpha)) if continuous or feature_refinement else float(hard.mean())*patch.width*patch.height) / (image.width * image.height) * 100, 2),
        "continuous_boundary": continuous or feature_refinement,
        "skin_feature_refinement": feature_refinement,
        "anchor_recovered": recovered,
        "landmark_protection": target=='face_skin' and features is not None,
        "face_scope": scope,
        "face_part": part,
        "scope_feather_px": scope_feather,
    }
    if fallback:
        quality['warnings'].insert(0,'精细五官模型未完成，已使用基础面部分区；请放大检查边缘')
    return result, quality
