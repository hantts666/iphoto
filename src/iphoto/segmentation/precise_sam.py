"""Optional Meta SAM2.1 Small for neural correction with a dense mask prior.

Apache-2.0 weights; ONNX-only application runtime. Export conventions follow
Meta's predictor and Microsoft's MIT ONNX wrappers, with single-mask decoding.
"""

from hashlib import sha256
from importlib.util import find_spec
import os
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from .runtime import prepare_runtime

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "precise-points"
FILES = {
    "encoder": ("sam21-small-encoder.onnx", 138018674, "73f90685bf0a2a252a6cdf04d5bd5e0f77a96aada8b0c47dd328fc420bb45194"),
    "decoder": ("sam21-small-decoder.onnx", 16564074, "0abf6e5a21ce63ee4faad1943ef676d7985b8d1f5fa66c1115e787f631daa31b"),
}
_backend = None


def available():
    return find_spec("onnxruntime") is not None and all(
        (MODEL_DIR/name).is_file() and (MODEL_DIR/name).stat().st_size == size
        for name, size, digest in FILES.values())


def verified_path(kind):
    name, size, expected = FILES[kind]
    path = MODEL_DIR/name
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError("精细提示点模型未完整配置")
    hasher = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != expected:
        raise ValueError("精细提示点模型校验失败，未加载")
    return path


def preprocess(image):
    rgb = np.asarray(image.convert("RGB"), np.float32)/255
    # Float PIL bilinear uses the same antialiased triangle filter as the
    # author's tensor resize; uint8 resizing would round before normalization.
    resized = np.stack([np.asarray(Image.fromarray(rgb[:, :, c]).resize((1024, 1024), Image.Resampling.BILINEAR))
                        for c in range(3)])
    return ((resized-np.array([.485, .456, .406], np.float32)[:, None, None])
            / np.array([.229, .224, .225], np.float32)[:, None, None])[None]


class PreciseSAM:
    def __init__(self):
        prepare_runtime()
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = min(8, os.cpu_count() or 4)
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 3
        self.encoder = ort.InferenceSession(str(verified_path("encoder")), options, providers=["CPUExecutionProvider"])
        self.decoder = ort.InferenceSession(str(verified_path("decoder")), options, providers=["CPUExecutionProvider"])
        self._key = self._features = None

    def predict_with_prior(self, image, coords, labels, guide, *, progress=None):
        from .prompts import validate_points
        coords, labels = np.asarray(coords), np.asarray(labels)
        if (image.size != guide.size or image.width*image.height > 1600*1600
                or guide.mode != "L" or coords.dtype.kind not in "fiu"
                or labels.dtype.kind not in "fiu"
                or coords.ndim != 2 or coords.shape[1] != 2 or not 1 <= len(coords) <= 8
                or labels.shape != (len(coords),) or not np.isfinite(coords).all()
                or not np.isfinite(labels).all() or not np.isin(labels, [0, 1, 2, 3]).all()
                or np.any(coords < 0) or np.any(coords[:, 0] > image.width-1)
                or np.any(coords[:, 1] > image.height-1)):
            raise ValueError("精细提示点输入无效，原范围保留")
        # Validate point numbers independently; box corners are model tokens.
        validate_points([[float(x)/max(1, image.width-1), float(y)/max(1, image.height-1), int(label)]
                         for (x, y), label in zip(coords, labels) if label in (0, 1)])
        started = perf_counter()
        key = (image.size, sha256(image.tobytes()).digest())
        cached = key == self._key
        if not cached:
            if progress is not None:
                progress("semantic_encode")
            features = self.encoder.run(None, {"image": preprocess(image)})
            shapes = [(1, 32, 256, 256), (1, 64, 128, 128), (1, 256, 64, 64)]
            if len(features) != 3 or any(v.shape != shape or v.dtype != np.float32 or not np.isfinite(v).all()
                                         for v, shape in zip(features, shapes)):
                raise ValueError("精细模型编码无效，原范围保留")
            self._features, self._key = features, key
        encoding_ms = (perf_counter()-started)*1000
        raw = np.asarray(guide.resize((256, 256), Image.Resampling.BILINEAR), np.float32)/255
        prior = np.log(np.clip(raw, .001, .999)/np.clip(1-raw, .001, .999))[None, None]
        feed = dict(zip(["features0", "features1", "embedding"], self._features))
        feed.update(point_coords=(coords/np.array(image.size, np.float32)*1024)[None].astype(np.float32),
                    point_labels=labels[None].astype(np.int32), input_masks=prior,
                    has_input_masks=np.ones(1, np.float32), original_image_size=np.array([image.height, image.width], np.int32))
        if progress is not None:
            progress("semantic_points")
        stamp = perf_counter()
        outputs = self.decoder.run(None, feed)
        if (len(outputs) != 3 or outputs[0].shape != (1, 1, image.height, image.width)
                or outputs[1].shape != (1, 1) or outputs[2].shape != (1, 1, 256, 256)
                or any(v.dtype != np.float32 or not np.isfinite(v).all() for v in outputs)
                or np.any(outputs[1] < 0) or np.any(outputs[1] > 1)):
            raise ValueError("精细模型预测无效，原范围保留")
        return outputs[0][0], outputs[1][0], {
            "model": "SAM2.1 Small", "embedding_cached": cached,
            "encoding_ms": round(encoding_ms, 1), "decoding_ms": round((perf_counter()-stamp)*1000, 1),
        }


def backend(*, progress=None):
    global _backend
    if _backend is None:
        if progress is not None:
            progress("semantic_model")
        _backend = PreciseSAM()
    return _backend
