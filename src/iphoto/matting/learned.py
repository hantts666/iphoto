"""Learn the uncertainty region; native matting still determines coverage.

Sessions live for one crop, then release their large image encoder. No alpha,
foreground colour or editor state is produced here.
"""
import math
import os
from time import perf_counter

import cv2
import numpy as np

from .learned_models import MODEL_DIR, FILES, verified_path


def prompts(points, size):
    width, height = size
    if (not isinstance(points, list) or len(points) > 8
            or any(not isinstance(p, list) or len(p) != 3
                   or any(isinstance(v, bool) or not isinstance(v, (int, float))
                          or not math.isfinite(v) for v in p[:2])
                   or type(p[2]) is not int or p[2] not in (0, 1, 2)
                   or not 0 <= p[0] <= width - 1 or not 0 <= p[1] <= height - 1
                   for p in points)):
        raise ValueError('发丝区域模型需要最多八个有效的原像素参照点')
    # Author's distinct unknown embedding, not a hard foreground prompt.
    entries = [[x * 1024 / width, y * 1024 / height, 4 if role == 2 else role]
               for x, y, role in points] or [[-1, -1, -1]]
    coordinates = np.array([[0, 0], [1024, 1024]] + [p[:2] for p in entries],
                           np.float32)[None]
    labels = np.array([2, 3] + [p[2] for p in entries], np.int64)[None]
    return coordinates, labels


def native_trimap(probabilities, size):
    if (not isinstance(probabilities, np.ndarray) or probabilities.dtype != np.float32
            or probabilities.shape != (1, 3, 256, 256)
            or not np.isfinite(probabilities).all()
            or probabilities.min() < -1e-6 or probabilities.max() > 1 + 1e-6
            or np.max(np.abs(probabilities.sum(axis=1) - 1)) > 1e-4):
        raise ValueError('发丝区域模型输出无效，原范围保留')
    scaled = cv2.resize(probabilities[0].transpose(1, 2, 0), size,
                        interpolation=cv2.INTER_LINEAR)
    return np.minimum(scaled.argmax(axis=2) * 128, 255).astype(np.uint8)


class LearnedTrimap:
    def predict(self, image, points, *, progress=None):
        if image.mode != 'RGB' or image.width * image.height > 1600 * 1600:
            raise ValueError('发丝区域模型需要不超过 256 万像素的局部原图')
        coordinates, labels = prompts(points, image.size)
        if progress: progress(phase='hair_uncertainty')
        from ..segmentation.runtime import prepare_runtime
        prepare_runtime()
        import onnxruntime as ort
        paths = {kind: verified_path(MODEL_DIR, kind) for kind in FILES}
        provider = ('DmlExecutionProvider' if 'DmlExecutionProvider' in ort.get_available_providers()
                    else 'CPUExecutionProvider')
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4, os.cpu_count() or 4)
        options.inter_op_num_threads = 1
        options.enable_mem_pattern = options.enable_cpu_mem_arena = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 3
        providers = [provider] if provider == 'CPUExecutionProvider' else [provider, 'CPUExecutionProvider']
        stamp = perf_counter()
        encoder = decoder = features = feed = None
        try:
            encoder, decoder = [ort.InferenceSession(str(paths[kind]), options, providers=providers)
                                for kind in ('encoder', 'decoder')]
            rgb = cv2.resize(np.asarray(image, np.float32) / 255, (1024, 1024),
                             interpolation=cv2.INTER_LINEAR)
            features = encoder.run(None, {'image': rgb.transpose(2, 0, 1)[None].copy()})
            shapes = [(1, 32, 256, 256), (1, 64, 128, 128), (1, 256, 64, 64)]
            if len(features) != 3 or any(v.shape != shape or v.dtype != np.float32
                                        or not np.isfinite(v).all() for v, shape in zip(features, shapes)):
                raise ValueError('发丝区域模型图像特征无效，原范围保留')
            feed = dict(zip(('features0', 'features1', 'embedding'), features))
            feed.update(coordinates=coordinates, labels=labels)
            trimap = native_trimap(decoder.run(None, feed)[0], image.size)
            return trimap, {'trimap_backend': 'MattePro', 'trimap_provider': provider,
                            'trimap_ms': round((perf_counter() - stamp) * 1000, 1)}
        except Exception as exc:
            raise ValueError('AI 发丝区域预测失败，原范围保留') from exc
        finally:
            del encoder, decoder, features, feed
