"""EfficientSAM ONNX backend with one-image embedding reuse and fixed CPU budget.

Input/output conventions follow the authors' EfficientSAM_onnx_example.py.
Weights are verified data; arbitrary local model paths are never accepted.
"""

from hashlib import sha256
from time import perf_counter

import numpy as np

from .models import verified_path
from .runtime import prepare_runtime


class EfficientSAM:
    def __init__(self, variant="s"):
        if variant not in ("s", "ti"):
            raise ValueError("未知像素选区模型")
        prepare_runtime()
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 3
        self.encoder = ort.InferenceSession(
            str(verified_path(f"{variant}_encoder")),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.decoder = ort.InferenceSession(
            str(verified_path(f"{variant}_decoder")),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.variant = variant
        self._key = self._embedding = None
        self.encoding_ms = 0

    def predict(self, image, coords, labels):
        array = np.asarray(image.convert("RGB"), dtype=np.uint8)
        height, width = array.shape[:2]
        key = (width, height, sha256(array.tobytes()).digest())
        reused = key == self._key
        started = perf_counter()
        if not reused:
            batched = (
                np.ascontiguousarray(array.transpose(2, 0, 1)[None], dtype=np.float32)
                / 255
            )
            (self._embedding,) = self.encoder.run(None, {"batched_images": batched})
            if not np.isfinite(self._embedding).all():
                raise ValueError("分割编码结果无效")
            self._key = key
        self.encoding_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        outputs = self.decoder.run(
            None,
            {
                "image_embeddings": self._embedding,
                "batched_point_coords": coords[None, None].astype(np.float32),
                "batched_point_labels": labels[None, None].astype(np.float32),
                "orig_im_size": np.asarray([height, width], np.int64),
            },
        )
        logits, scores = outputs[0][0, 0], outputs[1][0, 0]
        if (
            logits.ndim != 3
            or logits.shape[1:] != (height, width)
            or scores.shape != (logits.shape[0],)
            or not np.isfinite(logits).all()
            or not np.isfinite(scores).all()
        ):
            raise ValueError("分割模型输出尺寸或数值无效")
        return (
            logits,
            scores,
            {
                "model": f"EfficientSAM-{self.variant.upper()}",
                "embedding_cached": reused,
                "encoding_ms": round(self.encoding_ms, 1),
                "decoding_ms": round((perf_counter() - started) * 1000, 1),
            },
        )


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = EfficientSAM("s")
    return _backend
