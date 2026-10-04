"""Image capability registry. Implementations run only in the image worker.

The app does not import plugins from project files or execute AI-supplied code.
Optional inference dependencies are lazy, and no model is downloaded implicitly.
"""

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from .document import validate_mask
from .segmentation.classical import (
    refine,
    wand,
    subject,
    bitmap_mask as bitmap_mask,
    assess as assess,
)  # compatibility exports
from .segmentation.models import available as pixel_available
from .segmentation.face_models import available as face_available
from .segmentation.face_detection import available as face_detection_available
from .segmentation.retinaface import available as profile_detection_available
from .matting.models import available as detail_available

MODEL_DIR = Path(__file__).resolve().parents[2] / "models"


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    description: str
    module: str
    license: str
    source: str
    model: str = ""

    def inspect(self):
        dependency = find_spec(self.module) is not None
        model = not self.model or (MODEL_DIR / self.model).is_file()
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "available": dependency and model,
            "status": "可用 · 本地 CPU"
            if dependency and model
            else "缺少 " + (self.module if not dependency else self.model),
            "license": self.license,
            "source": self.source,
            "model_path": str(MODEL_DIR / self.model) if self.model else "",
        }


CAPABILITIES = (
    Capability(
        "matte",
        "透明边缘 · PyMatting",
        "三分图约束下估计连续透明度，保留不规则过渡；不自动去除背景颜色污染。",
        "pymatting",
        "MIT",
        "https://pymatting.github.io/",
    ),
    Capability(
        "grabcut",
        "边缘优化 · GrabCut",
        "根据照片颜色和边界细化已有选区；相似背景、发丝仍需修正。",
        "cv2",
        "Apache-2.0",
        "https://docs.opencv.org/4.x/d8/d83/tutorial_py_grabcut.html",
    ),
    Capability(
        "wand",
        "魔棒 · 连续颜色",
        "点击相连的相似颜色区域，使用容差控制范围。",
        "cv2",
        "Apache-2.0",
        "https://docs.opencv.org/4.x/d7/d1b/group__imgproc__misc.html",
    ),
    Capability(
        "u2net",
        "主体分割 · U²-Net 小模型",
        "本地 ONNX 主体/背景分割；不理解文字指定的任意物体。",
        "cv2",
        "OpenCV / U²-Net Apache-2.0",
        "https://github.com/xuebinqin/U-2-Net",
        "u2netp.onnx",
    ),
    Capability(
        "inpaint",
        "内容感知填充 · OpenCV",
        "用周围内容合成填充选区，用于移除对象或瑕疵；大孔洞可能留下痕迹，需分次小范围填充。",
        "cv2",
        "Apache-2.0",
        "https://docs.opencv.org/4.x/d7/d05/group__photo__inpaint.html",
    ),
    Capability(
        "raw",
        "RAW 解码 · rawpy",
        "解码相机 RAW（CR2/CR3/NEF/ARW/DNG/RAF 等）为 sRGB 后进入同一修图管线；使用相机白平衡。",
        "rawpy",
        "MIT",
        "https://github.com/letmaier/rawpy",
    ),
)


def capabilities():
    rows = [cap.inspect() for cap in CAPABILITIES]
    ready = pixel_available()
    rows.insert(
        0,
        {
            "id": "pixels",
            "name": "像素选区 · EfficientSAM-S",
            "description": "专用神经分割：文字目标定位后贴边、框选目标、正负点修正。大图和发丝仍需检查。",
            "available": ready,
            "status": "可用 · 本地 CPU" if ready else "需要配置像素选区模型",
            "license": "Apache-2.0 / ONNX Runtime MIT",
            "source": "https://github.com/yformer/EfficientSAM",
            "model_path": str(MODEL_DIR / "segmentation"),
        },
    )
    facial_ready = face_available()
    detection_ready = face_detection_available()
    profile_ready = profile_detection_available()
    rows.append({
        "id": "face_detection", "name": "人脸定位 · 本地 AI",
        "description": "打开照片时在本地定位可见人脸，配合面部分区选择五官或皮肤；侧脸与遮挡可能漏检。",
        "available": detection_ready,
        "status": ("可用 · 侧脸补充已配置" if profile_ready else "可用 · 可配置侧脸补充：scripts/setup_face_detection.py") if detection_ready else "需要配置人脸检测模型：scripts/setup_face_detection.py",
        "license": "MIT / OpenCV Apache-2.0", "source": "https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet",
        "model_path": str(MODEL_DIR / "face-detection"),
    })
    rows.insert(1, {
        "id": "face_skin", "name": "面部皮肤 · BiSeNet",
        "description": "AI 定位单个人脸后自动分离皮肤，保护眉眼嘴唇、头发和衣物；遮挡与边缘仍需检查。",
        "available": facial_ready,
        "status": "可用 · 本地 CPU" if facial_ready else "需要配置面部皮肤模型",
        "license": "MIT / ONNX Runtime MIT", "source": "https://github.com/yakhyo/face-parsing",
        "model_path": str(MODEL_DIR / "face-parsing"),
    })
    detail_ready = detail_available()
    from .segmentation.precise_sam import available as precise_available, MODEL_DIR as precise_dir
    precise_ready = precise_available()
    rows.append({
        "id": "precise_points", "name": "精细排除点 · SAM2.1 Small",
        "description": "已有鼻部、嘴唇分区中，结合蒙版和排除点进行神经修正；保留五官保护，结果仍需检查。",
        "available": precise_ready,
        "status": "可用 · 本地 CPU" if precise_ready else "需要配置精细提示点模型：scripts/setup_precise_points.py",
        "license": "Apache-2.0 / ONNX Runtime MIT", "source": "https://github.com/facebookresearch/sam2",
        "model_path": str(precise_dir),
    })
    rows.insert(2, {
        "id": "details", "name": "细节透明度 · ViTMatte-S",
        "description": "神经模型读取原图与范围约束，细化边缘透明度；智能点选结合分割置信度恢复局部孔洞。已有精细范围修边时保留提示点；相近颜色仍需检查。",
        "available": detail_ready,
        "status": "可用 · 本地 ONNX" if detail_ready else "需要配置细节模型",
        "license": "MIT / ONNX Runtime MIT", "source": "https://github.com/hustvl/ViTMatte",
        "model_path": str(MODEL_DIR / "matting"),
    })
    return rows


def _matte(image, mask, options):
    from .matting.service import refine_alpha

    return refine_alpha(image, mask, options.get("radius", 8))


# Every adapter has the same boundary. Adding metadata without an implementation
# must never silently run another model.
SELECTION_BACKENDS = {
    "matte": _matte,
    "grabcut": lambda image, mask, options: refine(image, mask),
    "wand": lambda image, mask, options: wand(
        image,
        mask,
        options.get("point"),
        options.get("tolerance", 24),
        options.get("mode", "replace"),
    ),
    "u2net": lambda image, mask, options: subject(image),
}


def run_selection(plugin_id, image, mask, **options):
    if plugin_id not in {cap.id for cap in CAPABILITIES}:
        raise ValueError("未知的选区能力")
    mask = validate_mask(mask)
    handler = SELECTION_BACKENDS.get(plugin_id)
    if handler is None:
        raise ValueError("该选区能力尚未接入处理器")
    return handler(image, mask, options)
