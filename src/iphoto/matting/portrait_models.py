"""Pinned, data-only portrait matte from the author's ONNX release."""
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[3] / 'models' / 'matting'
NAME = 'modnet-photographic.onnx'
SIZE = 25_888_640
DIGEST = '07c308cf0fc7e6e8b2065a12ed7fc07e1de8febb7dc7839d7b7f15dd66584df9'
URL = 'https://drive.usercontent.google.com/download?id=1cgycTQlYXpTh26gB9FTnthE7AvruV8hd&export=download&confirm=t'


def available():
    from .models import available as detail_available
    from ..segmentation.face_models import available as parser_available
    path = MODEL_DIR / NAME
    return bool(find_spec('onnxruntime') and path.is_file() and path.stat().st_size == SIZE
                and detail_available() and parser_available())


def verified_path():
    path = MODEL_DIR / NAME
    if not path.is_file() or path.stat().st_size != SIZE:
        raise ValueError('人物发丝模型未配置，请运行 scripts/setup_hair_matting.py')
    digest = sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != DIGEST:
        raise ValueError('人物发丝模型校验失败，原范围保留')
    return path
