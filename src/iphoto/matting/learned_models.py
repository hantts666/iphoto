"""Pinned data-only exports of MattePro's foreground/background/unknown model."""
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[3] / 'models' / 'matting'
FILES = {
    'encoder': ('mattepro-encoder.onnx', 855337074,
                '225dcbbaafc6cc0683cb0e1a0e2307da0353270d91f95a4473ea361792d09b83'),
    'decoder': ('mattepro-decoder.onnx', 17694526,
                'b9156bc68139f50e6913623aee6fef18ee75916df5bccc763084567505c55439'),
}


def available():
    return bool(find_spec('onnxruntime') and all(
        (MODEL_DIR / name).is_file() and (MODEL_DIR / name).stat().st_size == size
        for name, size, _ in FILES.values()))


def verified_path(directory, kind):
    name, size, digest = FILES[kind]
    path = Path(directory) / name
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError('发丝区域模型未安装完整：' + name)
    hasher = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    if hasher.hexdigest() != digest:
        raise ValueError('发丝区域模型校验失败，原范围保留：' + name)
    return path
