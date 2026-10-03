"""Verified, locally exported ViTMatte data; no executable model plugins."""
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "matting"
NAME = "vitmatte-small-640.onnx"
SIZE = 103_959_533
DIGEST = "dbbe16723638209f1883d1499060f43e249afe55a2411a70bfb6e7932560ffdb"
REPOSITORY = "hustvl/vitmatte-small-composition-1k"
REVISION = "6a58ad7646403c1df626fbd746900aec7361ea1d"


def available():
    path = MODEL_DIR / NAME
    return find_spec("onnxruntime") is not None and path.is_file() and path.stat().st_size == SIZE


def verified_path():
    path = MODEL_DIR / NAME
    if not path.is_file() or path.stat().st_size != SIZE:
        raise ValueError("细节模型未安装完整，请运行 scripts/setup_matting.py")
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != DIGEST:
        raise ValueError("细节模型校验失败，未加载；请重新配置模型")
    return path
