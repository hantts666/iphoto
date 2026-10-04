"""Verified neural localization; detection boxes are hints, not final masks."""
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

import numpy as np
from PIL import Image

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "face-detection"
NAME = "face_detection_yunet_2023mar.onnx"
SIZE = 232589
DIGEST = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
URL = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/models/face_detection_yunet/{NAME}"
_backend = None


def validate_features(features):
    from ..document import number
    if not isinstance(features,dict) or set(features)!={'eyes','mouth'}:
        raise ValueError('五官定位数据无效')
    clean = {}
    for name,points in features.items():
        if not isinstance(points,list) or len(points)!=2 or any(not isinstance(point,list) or len(point)!=2 for point in points):
            raise ValueError('五官定位点无效')
        clean[name] = [[number(value,0,1) for value in point] for point in points]
    return clean


def match_hint(hint, faces, *, partial=False, anchor=None):
    """Associate only one overlapping local face; ambiguous requests keep AI grounding."""
    from ..document import raster_mask
    bounds = raster_mask(hint,(384,384)).getbbox()
    if not bounds:
        return None
    matches = []
    for face in faces:
        other = raster_mask(face['mask'],(384,384)).getbbox()
        x,y = np.array(face['anchor'])*384
        if not other:
            continue
        if partial:
            # A cheek box need not contain the nose landmark. Require its
            # supplied point and most of its extent to belong to one face.
            if anchor is None:
                continue
            ax, ay = np.array(anchor) * 384
            if not (other[0] <= ax < other[2] and other[1] <= ay < other[3]):
                continue
        elif not (bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3]):
            continue
        overlap = max(0,min(bounds[2],other[2])-max(bounds[0],other[0])) * max(0,min(bounds[3],other[3])-max(bounds[1],other[1]))
        smaller = min((bounds[2]-bounds[0])*(bounds[3]-bounds[1]),(other[2]-other[0])*(other[3]-other[1]))
        area = (bounds[2]-bounds[0])*(bounds[3]-bounds[1]) if partial else smaller
        if overlap/max(1,area) >= (.8 if partial else .5):
            matches.append(face)
    return matches[0] if len(matches)==1 else None


def available():
    path = MODEL_DIR / NAME
    return find_spec("cv2") is not None and path.is_file() and path.stat().st_size == SIZE


def verified_path():
    path = MODEL_DIR / NAME
    if not available():
        raise ValueError("人脸检测模型尚未配置，请运行 scripts/setup_face_detection.py")
    if sha256(path.read_bytes()).hexdigest() != DIGEST:
        raise ValueError("人脸检测模型校验失败，未加载")
    return path


def _same_face(first, second):
    """Overlapping face boxes need a nearby nose before merging identities."""
    bounds = []
    for hint in (first,second):
        points = np.asarray(hint['mask']['ops'][0]['points'])
        bounds.append((points.min(0),points.max(0)))
    (left, right), (other_left, other_right) = bounds
    sizes = np.array([right-left,other_right-other_left])
    overlap = np.maximum(np.minimum(right,other_right)-np.maximum(left,other_left),0).prod()
    areas = sizes.prod(1)
    iou = overlap / max(areas.sum()-overlap,1e-10)
    containment = overlap / max(areas.min(),1e-10)
    distance = np.linalg.norm((np.array(first['anchor'])-second['anchor']) / np.maximum(sizes.min(0),1e-10))
    return (iou > .4 or containment >= .8) and distance <= .5


def _clearer_landmarks(primary, candidate, size):
    """A rotated, collapsed estimate may use a clearer matching neural pass."""
    if not primary.get('detection_rotation') or candidate['detection_score'] < primary['detection_score'] + .05:
        return False
    bounds = np.asarray(primary['mask']['ops'][0]['points']) * size
    width = np.ptp(bounds[:, 0])
    spans = []
    for face in (primary, candidate):
        spans.append([np.linalg.norm(np.diff(np.asarray(face['face_features'][name]) * size, axis=0))
                      for name in ('eyes', 'mouth')])
    old, new = np.asarray(spans)
    # Do not replace a legitimate narrow profile based only on confidence.
    return bool(np.all(old < width * .18) and np.all(new >= old * 2)
                and new[0] >= width * .12 and new[1] >= width * .08)


def detect(image, *, engine=None, retry_rotated=True, warnings=None):
    """A bounded 1280px neural pass, normalized back to the photograph."""
    global _backend
    import cv2
    native_engine = engine is None
    if engine is None:
        if _backend is None:
            # Loading bytes avoids OpenCV's Unicode model path limitations.
            _backend = cv2.FaceDetectorYN.create("onnx", np.frombuffer(verified_path().read_bytes(),np.uint8),
                                                np.empty(0,np.uint8),(1280,1280),.8,.3,5000)
        engine = _backend
    proxy = image.copy()
    proxy.thumbnail((1280,1280))
    engine.setInputSize(proxy.size)
    _, rows = engine.detect(cv2.cvtColor(np.asarray(proxy.convert("RGB")),cv2.COLOR_RGB2BGR))
    if rows is None:
        rows = np.empty((0,15),np.float32)
    rows = np.asarray(rows)
    if rows.ndim != 2 or rows.shape[1] != 15 or not np.isfinite(rows).all():
        raise ValueError("人脸检测结果无效")
    from ..document import empty_mask

    hints = []
    for row in sorted(rows,key=lambda r:float(r[-1]),reverse=True)[:16]:
        x,y,width,height = map(float,row[:4]);score=float(row[-1])
        if width < 8 or height < 8 or not .8 <= score <= 1:
            continue
        left,top,right,bottom = max(0,x/proxy.width),max(0,y/proxy.height),min(1,(x+width)/proxy.width),min(1,(y+height)/proxy.height)
        anchor = [float(row[8])/proxy.width,float(row[9])/proxy.height]
        if not (left <= anchor[0] <= right and top <= anchor[1] <= bottom and left < right and top < bottom):
            continue
        crop = [max(0,(x-width*.5)/proxy.width),max(0,(y-height*.5)/proxy.height),
                min(1,(x+width*1.5)/proxy.width),min(1,(y+height*1.5)/proxy.height)]
        mask = empty_mask()
        mask.update(label="人脸",ops=[{"kind":"polygon","mode":"add","points":[[left,top],[right,top],[right,bottom],[left,bottom]]}])
        # A face partly outside the photograph can have valid detection bounds
        # and off-image landmarks. Keep all published geometry in the image.
        landmarks = np.clip(np.asarray(row[4:14]).reshape(5,2) / np.array(proxy.size),0,1).tolist()
        hints.append({"name":"人脸","category":"人脸","mask_target":"face","mask":mask,"anchor":anchor,
                      "face_features":{"eyes":landmarks[:2],"mouth":landmarks[3:]},
                      "skin_crop":crop,"detection_score":score})
    if not hints and native_engine and retry_rotated:
        # Re-run the same neural detector on bounded rotated inputs. This
        # addresses head roll; it cannot infer an invisible or back-facing face.
        for angle in (-30,30):
            transform = cv2.getRotationMatrix2D((proxy.width/2,proxy.height/2),angle,1)
            cosine,sine = abs(transform[0,0]),abs(transform[0,1])
            size = (int(np.ceil(proxy.height*sine+proxy.width*cosine)),
                    int(np.ceil(proxy.height*cosine+proxy.width*sine)))
            transform[:,2] += np.array(size)/2 - np.array(proxy.size)/2
            rotated = cv2.warpAffine(np.asarray(proxy.convert("RGB")),transform,size)
            matches = detect(Image.fromarray(rotated),engine=engine,retry_rotated=False)
            inverse = cv2.invertAffineTransform(transform)
            for hint in matches:
                def mapping(points):
                    values = np.asarray(points)*np.array(size)
                    return np.clip((values @ inverse[:,:2].T + inverse[:,2])/np.array(proxy.size),0,1).tolist()
                hint['anchor'] = mapping([hint['anchor']])[0]
                hint['face_features'] = {name:mapping(points) for name,points in hint['face_features'].items()}
                hint['mask']['ops'][0]['points'] = mapping(hint['mask']['ops'][0]['points'])
                left,top,right,bottom = hint['skin_crop']
                corners = mapping([[left,top],[right,top],[right,bottom],[left,bottom]])
                hint['skin_crop'] = [min(p[0] for p in corners),min(p[1] for p in corners),
                                     max(p[0] for p in corners),max(p[1] for p in corners)]
                hint['detection_rotation'] = angle
            if matches:
                hints = matches
                break
    hints.sort(key=lambda h:(h["anchor"][0],h["anchor"][1]))
    if native_engine and len(hints) < 16:
        from . import retinaface
        try:
            if retinaface.available():
                candidates = detect(proxy,engine=retinaface.backend(),retry_rotated=False)
                extra = []
                replacements = []
                for known in hints:
                    matches = [candidate for candidate in candidates if _same_face(candidate, known)]
                    if (len(matches) == 1 and sum(_same_face(matches[0], other) for other in hints) == 1
                            and _clearer_landmarks(known, matches[0], np.array(proxy.size))):
                        replacements.append((known, matches[0]))
                for hint in sorted(candidates,key=lambda h:h['detection_score'],reverse=True):
                    if any(_same_face(hint,known) for known in hints+extra):
                        continue
                    hint['detection_model'] = 'RetinaFace MobileNet0.25'
                    extra.append(hint)
                    if len(hints)+len(extra) == 16:
                        break
                # Keep primary geometry, order and IDs. Supplementary faces
                # receive new IDs even when they appear to its left or above.
                hints.extend(sorted(extra,key=lambda h:(h['anchor'][0],h['anchor'][1])))
                for known, candidate in replacements:
                    # Preserve the primary face boundary, crop, order and ID.
                    # The nose and feature points are one coherent estimate.
                    known['anchor'] = candidate['anchor']
                    known['face_features'] = candidate['face_features']
                    known['landmark_model'] = 'RetinaFace MobileNet0.25'
        except Exception:
            # An optional model cannot discard another model's valid faces.
            if warnings is not None:
                warnings.append('补充人脸检测未完成，已保留现有定位；可用文字继续选择' if hints else
                                '本地补充人脸检测未完成，可用文字定位或框选人脸')
    for index,hint in enumerate(hints,1):
        hint.update(id=f"local-face-{index}",name=f"人脸 {index}")
        hint["mask"]["label"]=hint["name"]
    return hints
