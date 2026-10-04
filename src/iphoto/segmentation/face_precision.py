"""Optional FaRL LaPa semantic parts, with the author's alignment and warp.

Coordinate formulas follow FacePerceiver/facer (MIT), revision
ddd35c76ff840174b8a5403ad1c1255e37b8782b. Runtime uses NumPy and ONNX only.
This eleven-class model is used for nose/lips, not whole-face skin protection.
"""
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path
import os
import zlib

import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[3] / 'models' / 'face-parsing'
NAME = 'farl-lapa-448.onnx'
SIZE = 645022368
DIGEST = '60a239ea923ec79d26d966015ef2b462e13d3963f8d551fc7c15bae6d7fff279'
MAX_CACHE_BYTES = 16_000_000
CLASS_MAP = np.array([0, 1, 3, 2, 5, 4, 10, 12, 11, 13, 17], np.uint8)
CLASS_MAP.setflags(write=False)
_backend = None


def available():
    path = MODEL_DIR / NAME
    return find_spec('onnxruntime') is not None and path.is_file() and path.stat().st_size == SIZE


def verified_path():
    if not available():
        raise ValueError('精细五官模型尚未配置，请运行 scripts/setup_face_precision.py')
    path = MODEL_DIR / NAME
    hasher = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    if hasher.hexdigest() != DIGEST:
        raise ValueError('精细五官模型校验失败，未加载')
    return path


def alignment(points):
    points = np.asarray(points, np.float32)
    if points.shape != (5, 2) or not np.isfinite(points).all():
        raise ValueError('精细五官定位点无效')
    target = np.array([[196,226],[316,226],[256,286],[220,360.4],[292,360.4]], np.float32) / 256 - 1
    target = (target + 1) * 447 / 2
    center = points.mean(0);destination = target.mean(0)
    origin = points - center;delta = target - destination
    extent = (origin ** 2).sum()
    if extent < 1:
        raise ValueError('五官定位点过于集中，请重新选择人脸')
    a = (delta * origin).sum() / extent
    b = (delta[:,0] * origin[:,1] - delta[:,1] * origin[:,0]).sum() / extent
    matrix = np.array([[a,b,destination[0]-a*center[0]-b*center[1]],
                       [-b,a,destination[1]+b*center[0]-a*center[1]],
                       [0,0,1]], np.float32)
    if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-8:
        raise ValueError('五官对齐无效，请重新选择人脸')
    return matrix


def warp(coords, inverse=False):
    """Piecewise tanh/atanh, author LaPa warp_factor=0.8, shape=448."""
    coords = np.asarray(coords, np.float32) / 448 * 2 - 1
    ratio_high = (coords - 1 + .8) / .8
    ratio_low = (coords + 1 - .8) / .8
    operation = (lambda value: np.arctanh(np.clip(value, -.999, .999))) if inverse else np.tanh
    high = operation(ratio_high) * .8 + 1 - .8
    low = operation(ratio_low) * .8 - 1 + .8
    return (np.where(coords > .2, high, np.where(coords < -.2, low, coords)) + 1) / 2 * 448


def sample_bilinear(pixels, coords):
    """Float32 bilinear sampling with zero padding; no OpenCV 1/32 rounding."""
    height, width = pixels.shape[:2]
    x, y = coords[...,0], coords[...,1]
    x0 = np.floor(x).astype(np.int32);y0 = np.floor(y).astype(np.int32)
    dx = (x-x0).astype(np.float32);dy = (y-y0).astype(np.float32)
    result = np.zeros((*x.shape,pixels.shape[2]),np.float32)
    for xx,yy,weight in ((x0,y0,(1-dx)*(1-dy)),(x0+1,y0,dx*(1-dy)),
                         (x0,y0+1,(1-dx)*dy),(x0+1,y0+1,dx*dy)):
        valid = (xx >= 0) & (xx < width) & (yy >= 0) & (yy < height)
        result += pixels[np.clip(yy,0,height-1),np.clip(xx,0,width-1)] * (weight*valid)[...,None]
    return result


def aligned_image(image, matrix):
    yy,xx = np.mgrid[:448,:448].astype(np.float32)
    coords = warp(np.stack((xx+.5,yy+.5),-1), inverse=True)
    inverse = np.linalg.inv(matrix)
    coords = coords @ inverse[:2,:2].T + inverse[:2,2]
    # Author grid_sample uses boundary coordinates and align_corners=False.
    size = np.array(image.size,np.float32)
    grid = coords / size * 2 - 1
    coords = ((grid + 1) * size - 1) / 2
    pixels = sample_bilinear(np.asarray(image.convert('RGB'),np.float32)/255,coords)
    return np.ascontiguousarray(pixels.transpose(2,0,1)[None])


def native_labels(scores, matrix, size):
    from ..masks import MAX_MASK_PIXELS, MAX_MASK_SIDE
    if scores.shape != (1,11,512,512) or scores.dtype != np.float32 or not np.isfinite(scores).all():
        raise ValueError('精细五官模型分数无效')
    if np.shape(matrix) != (3,3) or not np.isfinite(matrix).all():
        raise ValueError('精细五官对齐无效')
    if (len(size) != 2 or any(isinstance(v,bool) or not isinstance(v,int) or not 1 <= v <= MAX_MASK_SIDE for v in size)
            or size[0]*size[1] > MAX_MASK_PIXELS):
        raise ValueError('精细五官范围超过尺寸上限')
    width,height = size
    labels = np.zeros((height,width),np.uint8)
    pixels = np.ascontiguousarray(scores[0].transpose(1,2,0))
    for top in range(0,height,64):
        bottom = min(top+64,height)
        for left in range(0,width,1024):
            right = min(left+1024,width)
            yy,xx = np.mgrid[top:bottom,left:right].astype(np.float32)
            coords = np.stack((xx+.5,yy+.5),-1) @ matrix[:2,:2].T + matrix[:2,2]
            coords = warp(coords)
            grid = coords / 448 * 2 - 1
            coords = ((grid + 1) * 512 - 1) / 2
            block = sample_bilinear(pixels, coords)
            labels[top:bottom,left:right] = CLASS_MAP[block.argmax(2)]
    return labels


def native_part_alpha(scores, matrix, labels, part):
    """Continuous class transition; semantic likelihood is not matting alpha.

    Resample logits before softmax, as for labels. Work only around the native
    target and in bounded blocks; never allocate an eleven-channel source map.
    """
    if part not in ('nose', 'lips'):
        raise ValueError('连续五官边缘的目标无效')
    if (scores.shape != (1,11,512,512) or scores.dtype != np.float32 or not np.isfinite(scores).all()
            or np.shape(matrix) != (3,3) or not np.isfinite(matrix).all()
            or labels.ndim != 2 or labels.dtype != np.uint8 or labels.max(initial=0)>18):
        raise ValueError('连续五官边缘的语义输入无效')
    classes = (7,9) if part=='lips' else (6,)
    target = np.isin(labels, CLASS_MAP[list(classes)])
    result = np.zeros(labels.shape, np.uint8)
    ys,xs = np.nonzero(target)
    if not len(xs):
        return result
    height,width = labels.shape
    left,top=max(0,int(xs.min())-64),max(0,int(ys.min())-64)
    right,bottom=min(width,int(xs.max())+65),min(height,int(ys.max())+65)
    pixels = np.ascontiguousarray(scores[0].transpose(1,2,0))
    for y in range(top,bottom,64):
        end_y=min(y+64,bottom)
        for x in range(left,right,1024):
            end_x=min(x+1024,right)
            yy,xx=np.mgrid[y:end_y,x:end_x].astype(np.float32)
            coords=warp(np.stack((xx+.5,yy+.5),-1)@matrix[:2,:2].T+matrix[:2,2])
            coords=((coords/448)*512-.5)
            block=sample_bilinear(pixels,coords)
            weights=np.exp(block-block.max(2,keepdims=True))
            likelihood=weights[:,:,classes].sum(2)/weights.sum(2)
            # A bounded transition, not a claim of calibrated probability or
            # physical transparency. Confident other classes remain zero.
            alpha=np.rint(np.clip((likelihood-.25)/.5,0,1)*255).astype(np.uint8)
            permitted=np.isin(labels[y:end_y,x:end_x],(1,12,13) if part=='lips' else (1,10))
            alpha[~permitted]=0
            result[y:end_y,x:end_x]=alpha
    return result


class FaceParser:
    name = 'FaRL LaPa'

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
        self._cached = None

    def _prediction(self, image, points, *, progress=None):
        matrix = alignment(points)
        # Nose/lips jobs share one face's semantic output. Hash pixels as well
        # as its transform so another photo or moved crop cannot reuse it.
        pixels = np.asarray(image if image.mode == 'RGB' else image.convert('RGB'))
        key = (image.size, sha256(pixels).digest(), matrix.tobytes())
        if self._cached is not None and self._cached[0] == key:
            if progress is not None:
                progress('face_cached')
            labels=self._cached[1]
            if isinstance(labels,tuple):
                labels=np.frombuffer(zlib.decompress(labels[1]),np.uint8).reshape(labels[0])
            return labels, self._cached[2], matrix
        if progress is not None:
            progress('face_infer')
        scores = self.session.run(None,{'image':aligned_image(image,matrix)})[0]
        if progress is not None:
            progress('face_boundary')
        labels = native_labels(scores,matrix,image.size)
        labels.setflags(write=False)
        scores.setflags(write=False)
        cached_labels=labels
        if labels.nbytes+scores.nbytes>MAX_CACHE_BYTES and scores.nbytes<MAX_CACHE_BYTES:
            # Native class maps are mostly constant regions. Lossless packing
            # retains exact classes and logits within the same 16 MB budget.
            cached_labels=(labels.shape,zlib.compress(labels.tobytes(),level=1))
        stored_size=cached_labels.nbytes if isinstance(cached_labels,np.ndarray) else len(cached_labels[1])
        self._cached = (key,cached_labels,scores) if stored_size+scores.nbytes <= MAX_CACHE_BYTES else None
        return labels,scores,matrix

    def predict_native(self, image, points, *, progress=None):
        return self._prediction(image,points,progress=progress)[0]

    def predict_part(self, image, points, part, *, progress=None):
        labels,scores,matrix=self._prediction(image,points,progress=progress)
        if progress is not None:
            progress('face_continuous')
        return labels,native_part_alpha(scores,matrix,labels,part)


def backend(*, progress=None):
    global _backend
    if _backend is None:
        if progress is not None:
            progress('face_model')
        _backend = FaceParser()
    return _backend
