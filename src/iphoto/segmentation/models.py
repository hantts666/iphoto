"""Pinned public EfficientSAM ONNX exports from the authors' official Space."""

from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "segmentation"
REVISION = "d8dbb1eee73bfb3392aa6f6e8944aeb13f3f4036"
BASE_URL = f"https://huggingface.co/spaces/yunyangx/EfficientSAM/resolve/{REVISION}"
FILES = {
    "s_encoder": (
        "efficientsam_s_encoder.onnx",
        89558337,
        "3eea4544db4647570ca5bea8ab6bbe057f2fc767ad089c14b8f67f482a2493ed",
    ),
    "s_decoder": (
        "efficientsam_s_decoder.onnx",
        16565728,
        "d40a537f617c8ced3b2034b6971bc16cc291c49d3d80f740a394cdb78f0e1d06",
    ),
    "ti_encoder": (
        "efficientsam_ti_encoder.onnx",
        24799761,
        "84ed466ffcc5c1f8d08409bc34a23bb364ab2c15e402cb12d4335a42be0e0951",
    ),
    "ti_decoder": (
        "efficientsam_ti_decoder.onnx",
        16565728,
        "a62f8fa5ea080447c0689418d69e58f1e83e0b7adf9c142e2bd9bcc8045c0b11",
    ),
}


def available(variant="s"):
    return find_spec("onnxruntime") is not None and all(
        (MODEL_DIR / FILES[f"{variant}_{part}"][0]).is_file()
        and (MODEL_DIR / FILES[f"{variant}_{part}"][0]).stat().st_size
        == FILES[f"{variant}_{part}"][1]
        for part in ("encoder", "decoder")
    )


def verified_path(key):
    name, size, digest = FILES[key]
    path = MODEL_DIR / name
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError("像素选区模型未安装完整，请运行 scripts/setup_segmentation.py")
    with path.open("rb") as source:
        hasher = sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != digest:
        raise ValueError("像素选区模型校验失败，未加载；请重新配置模型")
    return path
