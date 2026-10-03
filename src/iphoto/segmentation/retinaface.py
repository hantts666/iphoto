"""Pinned RetinaFace MobileNet0.25 fallback, without a PyTorch dependency.

Preprocessing, priors and decode follow yakhyo/retinaface-pytorch (MIT).
See docs/licenses/RetinaFace-MIT.txt. Boxes remain localization hints.
"""
from functools import lru_cache
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path
import os

import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[3] / 'models' / 'face-detection'
NAME = 'retinaface_mv1_0.25.onnx'
SIZE = 1736694
DIGEST = 'b7a7acab55e104dce6f32cdfff929bd83946da5cd869b9e2e9bdffafd1b7e4a5'
REVISION = '4cd6e3471e5bac794637290a530566f463db4762'
URL = 'https://github.com/yakhyo/retinaface-pytorch/releases/download/v0.0.1/' + NAME
_backend = None


def available():
    path = MODEL_DIR / NAME
    return find_spec('onnxruntime') is not None and path.is_file() and path.stat().st_size == SIZE


def verified_path():
    path = MODEL_DIR / NAME
    if not available():
        raise ValueError('补充人脸检测模型尚未配置，请运行 scripts/setup_face_detection.py')
    if sha256(path.read_bytes()).hexdigest() != DIGEST:
        raise ValueError('补充人脸检测模型校验失败，未加载')
    return path


@lru_cache(maxsize=4)
def priors(size):
    width, height = size
    if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 1280 for v in size):
        raise ValueError('人脸检测输入尺寸无效')
    levels = []
    for step, sizes in zip((8,16,32), ((16,32),(64,128),(256,512))):
        yy, xx = np.meshgrid(np.arange((height+step-1)//step), np.arange((width+step-1)//step), indexing='ij')
        centers = np.stack(((xx+.5)*step/width, (yy+.5)*step/height), -1).reshape(-1,2)
        centers = np.repeat(centers,2,axis=0)
        scales = np.tile(np.array(sizes)[:,None]/np.array(size), (len(centers)//2,1))
        levels.append(np.concatenate((centers,scales),axis=1).astype(np.float32))
    result = np.concatenate(levels)
    result.flags.writeable = False
    return result


def decode(outputs, size):
    """Return the common x/y/w/h + five landmarks + score detector protocol."""
    anchors = priors(size)
    if not isinstance(outputs, (list,tuple)) or len(outputs) != 3:
        raise ValueError('补充人脸检测输出无效')
    loc, conf, points = outputs
    for value, columns in ((loc,4),(conf,2),(points,10)):
        if (not isinstance(value,np.ndarray) or value.shape != (1,len(anchors),columns)
                or value.dtype != np.float32 or not np.isfinite(value).all()):
            raise ValueError('补充人脸检测输出无效')
    if conf.min() < 0 or conf.max() > 1:
        raise ValueError('补充人脸检测置信度无效')
    loc, conf, points = loc[0], conf[0], points[0]
    picked = np.flatnonzero(conf[:,1] >= .8)
    picked = picked[np.argsort(-conf[picked,1],kind='stable')][:5000]
    if not len(picked):
        return np.empty((0,15),np.float32)
    a = anchors[picked]
    center = a[:,:2] + loc[picked,:2]*.1*a[:,2:]
    with np.errstate(over='ignore',invalid='ignore'):
        extent = a[:,2:]*np.exp(loc[picked,2:]*.2)
    scale = np.array(size,np.float32)
    with np.errstate(over='ignore',invalid='ignore'):
        boxes = np.concatenate((center-extent/2,center+extent/2),1)*np.tile(scale,2)
        landmarks = (a[:,None,:2]+points[picked].reshape(-1,5,2)*.1*a[:,None,2:])*scale
    if not np.isfinite(boxes).all() or not np.isfinite(landmarks).all() or np.any(extent <= 0):
        raise ValueError('补充人脸检测定位无效')
    scores = conf[picked,1]
    keep, order = [], np.arange(len(picked))
    boxes = boxes.astype(np.float64)
    areas = (boxes[:,2:]-boxes[:,:2]).prod(1)
    while len(order) and len(keep)<16:
        current = order[0];keep.append(current);rest = order[1:]
        overlap = np.maximum(np.minimum(boxes[current,2:],boxes[rest,2:])-
                             np.maximum(boxes[current,:2],boxes[rest,:2]),0).prod(1)
        iou = overlap/np.maximum(areas[current]+areas[rest]-overlap,1e-10)
        order = rest[iou <= .4]
    boxes = boxes[keep].copy();boxes[:,2:] -= boxes[:,:2]
    return np.concatenate((boxes,landmarks[keep].reshape(-1,10),scores[keep,None]),1).astype(np.float32)


class RetinaFace:
    def __init__(self):
        from .runtime import prepare_runtime
        prepare_runtime()
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4,os.cpu_count() or 4)
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(verified_path()),sess_options=options,providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name

    def setInputSize(self, size):
        priors(size)  # Validate and prepare only a bounded grid.

    def detect(self, pixels):
        if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
            raise ValueError('补充人脸检测图像无效')
        size = (pixels.shape[1],pixels.shape[0])
        priors(size)
        tensor = pixels.astype(np.float32)-np.array([104,117,123],np.float32)
        values = self.session.run(None,{self.input_name:np.ascontiguousarray(tensor.transpose(2,0,1)[None])})
        return True, decode(values,size)


def backend():
    global _backend
    if _backend is None:
        _backend = RetinaFace()
    return _backend
