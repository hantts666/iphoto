"""Verified facial parsing weights released by yakhyo/face-parsing (MIT)."""

from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "face-parsing"
NAME = "resnet18.onnx"
SIZE = 53205364
DIGEST = "0d9bd318e46987c3bdbfacae9e2c0f461cae1c6ac6ea6d43bbe541a91727e33f"
URL = "https://github.com/yakhyo/face-parsing/releases/download/weights/resnet18.onnx"


def available():
    path = MODEL_DIR / NAME
    return find_spec("onnxruntime") is not None and path.is_file() and path.stat().st_size == SIZE


def verified_path():
    path = MODEL_DIR / NAME
    if not available():
        raise ValueError("面部皮肤模型尚未配置，请运行 scripts/setup_face_parsing.py")
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != DIGEST:
        raise ValueError("面部皮肤模型校验失败，未加载；请重新配置模型")
    return path
