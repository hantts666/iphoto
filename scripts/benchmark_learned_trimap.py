"""Benchmark a verified learned trimap followed by native ViTMatte.

Research command only. Writes diagnostic crops, alpha and composites to the
chosen output directory; does not apply a selection or modify an editor project.
Model exports remain optional local data, outside the application model registry.
"""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from time import perf_counter

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

FILES = {
    'encoder': ('mattepro-encoder.onnx', 855337074,
                '225dcbbaafc6cc0683cb0e1a0e2307da0353270d91f95a4473ea361792d09b83'),
    'decoder': ('mattepro-decoder.onnx', 17694526,
                'b9156bc68139f50e6913623aee6fef18ee75916df5bccc763084567505c55439'),
}


def verified_path(directory, kind):
    name, size, digest = FILES[kind]
    path = directory / name
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError('Learned trimap export missing or incomplete: ' + name)
    hasher = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    if hasher.hexdigest() != digest:
        raise ValueError('Unverified learned trimap export: ' + name)
    return path


def prompts(points, size):
    """Native crop coordinates: 0 background, 1 foreground, 2 unknown identity.

    Unknown prompts use the author's distinct embedding 4. They are not hard
    foreground anchors. Preserve the number of tokens; duplicated padding
    embeddings would change attention and the predicted uncertainty region.
    """
    width, height = size
    if (not isinstance(points, list) or len(points) > 8
            or any(not isinstance(p, list) or len(p) != 3
                   or any(isinstance(v, bool) or not isinstance(v, (int, float))
                          or not math.isfinite(v) for v in p[:2])
                   or type(p[2]) is not int or p[2] not in (0, 1, 2)
                   or not 0 <= p[0] <= width - 1 or not 0 <= p[1] <= height - 1
                   for p in points)):
        raise ValueError('At most eight native foreground/background/unknown points required')
    entries = [[x * 1024 / width, y * 1024 / height, 4 if role == 2 else role]
               for x, y, role in points] or [[-1, -1, -1]]
    coordinates = np.array([[0, 0], [1024, 1024]] + [p[:2] for p in entries],
                           np.float32)[None]
    labels = np.array([2, 3] + [p[2] for p in entries], np.int64)[None]
    return coordinates, labels


def native_trimap(probabilities, size):
    """Upsample class probabilities before deciding classes, as the author does."""
    if (not isinstance(probabilities, np.ndarray) or probabilities.dtype != np.float32
            or probabilities.shape != (1, 3, 256, 256)
            or not np.isfinite(probabilities).all()
            or probabilities.min() < -1e-6 or probabilities.max() > 1 + 1e-6
            or np.max(np.abs(probabilities.sum(axis=1) - 1)) > 1e-4):
        raise ValueError('Invalid learned trimap class probabilities')
    scaled = cv2.resize(probabilities[0].transpose(1, 2, 0), size,
                        interpolation=cv2.INTER_LINEAR)
    return np.minimum(scaled.argmax(axis=2) * 128, 255).astype(np.uint8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--crop', type=int, nargs=4, required=True)
    parser.add_argument('--points', type=Path, help='JSON native-crop [x,y,0/1/2] points')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', choices=('cpu', 'dml'), default='cpu')
    args = parser.parse_args()
    output_files = {(args.output / name).resolve() for name in
                    ('source.png', 'trimap.png', 'alpha.png', 'white.png', 'checker.png', 'report.json')}
    if args.source.resolve() in output_files or (args.points and args.points.resolve() in output_files):
        raise ValueError('Diagnostic outputs must not overwrite input files')
    from iphoto.engine import load_source
    from iphoto.segmentation.runtime import prepare_runtime
    from iphoto.matting.neural import solve
    from iphoto.cutout import _recover
    prepare_runtime()
    import onnxruntime as ort

    loaded = load_source(args.source)
    left, top, right, bottom = args.crop
    if (not 0 <= left < right <= loaded.image.width
            or not 0 <= top < bottom <= loaded.image.height
            or (right - left) * (bottom - top) > 1600 * 1600):
        raise ValueError('Crop must be inside the oriented photo and at most 2.56M pixels')
    image = loaded.image.crop(args.crop).convert('RGB')
    points = json.loads(args.points.read_text(encoding='utf8')) if args.points else []
    coordinates, labels = prompts(points, image.size)
    paths = {kind: verified_path(args.models, kind) for kind in FILES}
    provider = 'DmlExecutionProvider' if args.provider == 'dml' else 'CPUExecutionProvider'
    if provider not in ort.get_available_providers():
        raise ValueError('Requested ONNX provider unavailable: ' + provider)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.enable_mem_pattern = options.enable_cpu_mem_arena = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.log_severity_level = 3
    providers = [provider] if args.provider == 'cpu' else [provider, 'CPUExecutionProvider']
    stamp = perf_counter()
    encoder, decoder = [ort.InferenceSession(str(paths[kind]), options, providers=providers)
                        for kind in ('encoder', 'decoder')]
    session_ms = (perf_counter() - stamp) * 1000
    rgb = np.asarray(image, np.float32) / 255
    # Author's float OpenCV square resize; normalization is inside the export.
    inputs = cv2.resize(rgb, (1024, 1024), interpolation=cv2.INTER_LINEAR)
    stamp = perf_counter()
    features = encoder.run(None, {'image': inputs.transpose(2, 0, 1)[None].copy()})
    shapes = [(1, 32, 256, 256), (1, 64, 128, 128), (1, 256, 64, 64)]
    if len(features) != 3 or any(v.shape != shape or v.dtype != np.float32
                                or not np.isfinite(v).all() for v, shape in zip(features, shapes)):
        raise ValueError('Invalid learned trimap image features')
    encoding_ms = (perf_counter() - stamp) * 1000
    feed = dict(zip(('features0', 'features1', 'embedding'), features))
    feed.update(coordinates=coordinates, labels=labels)
    stamp = perf_counter()
    trimap = native_trimap(decoder.run(None, feed)[0], image.size)
    decoding_ms = (perf_counter() - stamp) * 1000
    # Release the large first-stage sessions before loading native matting.
    del encoder, decoder, features, feed
    stamp = perf_counter()
    alpha, tiles = solve(image, trimap)
    matting_ms = (perf_counter() - stamp) * 1000
    alpha_image = Image.fromarray(alpha)
    foreground = _recover(image, alpha_image)
    white = Image.composite(foreground, Image.new('RGB', image.size, 'white'), alpha_image)
    yy, xx = np.indices(alpha.shape)
    checker = np.where(((xx // 20 + yy // 20) % 2)[..., None],
                       np.array([184, 158, 214]), np.array([138, 99, 176])).astype(np.uint8)
    checked = Image.composite(foreground, Image.fromarray(checker), alpha_image)
    if sha256(args.source.read_bytes()).hexdigest() != loaded.digest:
        raise ValueError('Source changed during benchmark; no diagnostic output written')
    args.output.mkdir(parents=True, exist_ok=True)
    for name, picture in [('source', image), ('trimap', Image.fromarray(trimap)),
                          ('alpha', alpha_image), ('white', white), ('checker', checked)]:
        picture.save(args.output / (name + '.png'))
    report = {'source_sha256': loaded.digest, 'crop': args.crop, 'native_size': list(image.size),
              'provider': provider, 'prompt_tokens': int(labels.shape[1]),
              'unknown_pixels': int((trimap == 128).sum()), 'tiles': tiles,
              'session_ms': round(session_ms, 1), 'encoding_ms': round(encoding_ms, 1),
              'decoding_ms': round(decoding_ms, 1), 'matting_ms': round(matting_ms, 1),
              'source_unchanged': True, 'editor_applied': False,
              'quality': 'unreviewed', 'semantic_target_protection': False}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
