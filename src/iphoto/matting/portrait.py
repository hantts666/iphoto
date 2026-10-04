"""Portrait support guides local hair alpha; it is never a hair selection itself."""
import numpy as np
import cv2
from PIL import Image

from .neural import NativeMatte
from .portrait_models import verified_path
from ..segmentation.runtime import prepare_runtime


class PortraitMatte(NativeMatte):
    def __init__(self):
        prepare_runtime()
        import onnxruntime as ort
        self._ort = ort
        self._path = str(verified_path())
        self.fallback = ''
        try:
            self._session = self._create('DmlExecutionProvider' if 'DmlExecutionProvider' in ort.get_available_providers() else 'CPUExecutionProvider')
        except Exception:
            self.fallback = '显卡人像透明度不可用，已改用 CPU'
            self._session = self._create('CPUExecutionProvider')

    def predict_image(self, image):
        scale = min(1024/min(image.size), 1536/max(image.size))
        width, height = [max(32, int(size*scale)//32*32) for size in image.size]
        # Resize bytes before float conversion, avoiding a 288MB float copy of
        # a 24MP photo. RGB order and normalization follow the author's demo.
        rgb = cv2.resize(np.asarray(image.convert('RGB')), (width,height), interpolation=cv2.INTER_AREA)
        pixels = ((rgb.astype(np.float32)-127.5)/127.5).transpose(2,0,1)[None].copy()
        def run():
            return self._session.run(None, {self._session.get_inputs()[0].name:pixels})[0]
        try:
            result = run()
        except Exception as exc:
            if self.provider == 'CPUExecutionProvider':
                raise ValueError('人像透明度推理失败，原范围保留') from exc
            self._session = self._create('CPUExecutionProvider')
            self.fallback = '显卡人像透明度不可用，已改用 CPU'
            result = run()
        if result.shape != (1,1,height,width) or not np.isfinite(result).all():
            raise ValueError('人像透明度输出无效，原范围保留')
        alpha = np.rint(np.clip(result[0,0],0,1)*255).astype(np.uint8)
        return Image.fromarray(alpha).resize(image.size, Image.Resampling.BILINEAR)
