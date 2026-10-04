"""Fixed native tiles with a bounded ONNX session, RGB and an explicit trimap.

This model estimates alpha; it cannot infer missing semantic constraints.
Only the unknown pixels are changed. Photo data is never modified.
"""
import os
from time import perf_counter

import numpy as np
from PIL import Image

from ..segmentation.runtime import prepare_runtime
from .models import verified_path
from .metadata import copy_metadata

TILE = 512
STRIDE = 384
HALO = 64
INPUT = TILE + 2 * HALO
MAX_TILES = 128
MAX_UNKNOWN = 4_000_000


class NativeMatte:
    def __init__(self):
        prepare_runtime()
        import onnxruntime as ort

        self._ort = ort
        self._path = str(verified_path())
        self.fallback = ""
        try:
            self._session = self._create("DmlExecutionProvider" if "DmlExecutionProvider" in ort.get_available_providers() else "CPUExecutionProvider")
        except Exception:
            self.fallback = "显卡细化不可用，已改用 CPU"
            self._session = self._create("CPUExecutionProvider")

    def _create(self, provider):
        options = self._ort.SessionOptions()
        options.intra_op_num_threads = min(4, os.cpu_count() or 4)
        options.inter_op_num_threads = 1
        # DirectML requires sequential execution and disabled memory patterns.
        options.execution_mode = self._ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 3
        return self._ort.InferenceSession(self._path, sess_options=options,
                                         providers=[provider] if provider == "CPUExecutionProvider" else [provider, "CPUExecutionProvider"])

    @property
    def provider(self):
        return self._session.get_providers()[0]

    def predict(self, pixels):
        try:
            result = self._session.run(None, {"pixel_values": pixels})[0]
        except Exception as exc:
            if self.provider == "CPUExecutionProvider":
                raise ValueError("细节模型推理失败，原选区保留") from exc
            self._session = self._create("CPUExecutionProvider")
            self.fallback = "显卡细化不可用，已改用 CPU"
            try:
                result = self._session.run(None, {"pixel_values": pixels})[0]
            except Exception as retry:
                raise ValueError("细节模型推理失败，原选区保留") from retry
        if result.shape != (1, 1, INPUT, INPUT) or not np.isfinite(result).all():
            raise ValueError("细节模型输出尺寸或数值无效，原选区保留")
        return np.clip(result[0, 0], 0, 1)


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = NativeMatte()
    return _backend


def solve(image, trimap, *, engine=None, progress=None):
    """0/128/255 constraints; tiles are original pixels, never resized.

    Halo context is retained from the original image. Padding occurs only
    outside the image and matches the official processor's zero padding.
    Final core pixels never come from this padding.
    """
    trimap = np.asarray(trimap)
    if (trimap.dtype != np.uint8 or trimap.shape != (image.height, image.width)
            or not np.isin(trimap, [0, 128, 255]).all()):
        raise ValueError("细节模型需要与照片一致的三分图")
    unknown = trimap == 128
    if np.count_nonzero(unknown) > MAX_UNKNOWN:
        raise ValueError("待判断的细节过多，请缩小范围或补充提示点")
    cores = [(x, y) for y in range(0, image.height, STRIDE)
             for x in range(0, image.width, STRIDE)
             if unknown[y:y+TILE, x:x+TILE].any()]
    if len(cores) > MAX_TILES:
        raise ValueError("细节范围过大，请分区域修正")
    output = np.where(trimap == 255, 255, 0).astype(np.uint8)
    if not cores:
        return output, 0
    # Accumulate only unknown pixels (at most 4M), rather than allocating
    # multiple full-resolution float canvases for a 24MP photograph.
    indices = np.flatnonzero(unknown)
    total = np.zeros(indices.size,np.float32)
    mass = np.zeros(indices.size,np.float32)
    overlap = TILE-STRIDE
    ramp = .5-.5*np.cos(np.pi*(np.arange(overlap,dtype=np.float32)+.5)/overlap)
    engine = engine or backend()
    started = perf_counter()
    for index, (x, y) in enumerate(cores,1):
        if perf_counter() - started > 180:
            raise ValueError("细节细化超时，原选区保留；请缩小范围")
        x1, y1 = min(x + TILE, image.width), min(y + TILE, image.height)
        left, top = max(0, x-HALO), max(0, y-HALO)
        right, bottom = min(image.width, x1+HALO), min(image.height, y1+HALO)
        height, width = bottom-top, right-left
        pixels = np.zeros((1, 4, INPUT, INPUT), np.float32)
        rgb = np.asarray(image.crop((left, top, right, bottom)).convert("RGB"), np.float32)
        pixels[0, :3, :height, :width] = (rgb / 127.5 - 1).transpose(2, 0, 1)
        pixels[0, 3, :height, :width] = trimap[top:bottom, left:right] / 255
        alpha = engine.predict(pixels)[y-top:y1-top, x-left:x1-left]
        active = unknown[y:y1, x:x1]
        wx,wy = np.ones(x1-x,np.float32),np.ones(y1-y,np.float32)
        if x>0:
            wx[:min(overlap,len(wx))] *= ramp[:min(overlap,len(wx))]
        if x1<image.width:
            wx[-overlap:] *= ramp[::-1]
        if y>0:
            wy[:min(overlap,len(wy))] *= ramp[:min(overlap,len(wy))]
        if y1<image.height:
            wy[-overlap:] *= ramp[::-1]
        weights = wy[:,None]*wx[None,:]
        rows,columns = np.nonzero(active)
        positions = np.searchsorted(indices,(rows+y)*image.width+columns+x)
        total[positions] += alpha[active]*weights[active]
        mass[positions] += weights[active]
        if progress is not None:
            progress(index,len(cores))
    output.ravel()[indices] = np.rint(total/np.maximum(mass,1e-8)*255).astype(np.uint8)
    return output, len(cores)


def refine(image, mask, radius=8, *, points=None, progress=None):
    from ..document import empty_mask, raster_mask
    from ..masks import encode_bitmap
    from .trimap import make_trimap
    from ..segmentation.prompts import validate_points

    started = perf_counter()
    points = validate_points([] if points is None else points)
    original = raster_mask({**mask, "feather": 0}, image.size)
    guide = make_trimap(original, radius)
    trimap = np.rint(guide * 255).astype(np.uint8)
    del guide
    if mask.get("semantic_target") in ("face", "face_skin", "body_skin"):
        trimap[np.asarray(original)==0] = 0
    del original
    anchors = {}
    for x, y, label in points:
        coordinate = (round(y * (image.height - 1)), round(x * (image.width - 1)))
        value = 255 if label else 0
        if coordinate in anchors and anchors[coordinate] != value:
            raise ValueError("保留点和排除点重叠，请先修正提示点")
        if trimap[coordinate] not in (128, value):
            raise ValueError("提示点与已有确定范围冲突，请先补点修正轮廓")
        anchors[coordinate] = value
        trimap[coordinate] = value
    pixels, tiles = solve(image, trimap,progress=progress)
    result = copy_metadata(mask, empty_mask())
    result.update(bitmap=encode_bitmap(Image.fromarray(pixels), sampling="alpha", preserve_resolution=True), label=mask["label"])
    model = backend()
    return result, {"backend": "ViTMatte-S · ONNX", "provider": model.provider,
                    "elapsed_ms": round((perf_counter()-started)*1000, 1), "radius": radius,
                    "tiles": tiles, "mask_size": list(image.size),
                    "partial_pixels": int(((pixels>0)&(pixels<255)).sum()),
                    "unknown_pixels": int((trimap==128).sum()),
                    "coverage": round(float((pixels>0).mean())*100,1),
                    "warnings": [model.fallback] if model.fallback else []}
