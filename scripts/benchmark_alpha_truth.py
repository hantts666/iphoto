"""Compare local native alpha files against supplied ground truth; no downloads.

Metrics cover the supplied trimap's unknown region. This is a diagnostic,
not the alphamatting.com submission protocol or a visual quality verdict.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

MAX_PIXELS = 8_000_000


def read_alpha(path):
    with Image.open(path) as image:
        if (image.mode not in ('L', 'RGB', 'P') or 'transparency' in image.info
                or image.width * image.height > MAX_PIXELS):
            raise ValueError('Alpha and trimap must be native 8-bit grayscale, at most 8 MP')
        if image.mode == 'L':
            return np.array(image)
        # Public reference PNGs may store identical gray channels or a palette.
        # Check equality before taking one byte plane; never convert color to luma.
        pixels = np.array(image.convert('RGB'))
        if not (np.array_equal(pixels[..., 0], pixels[..., 1])
                and np.array_equal(pixels[..., 0], pixels[..., 2])):
            raise ValueError('Alpha image contains color, not exact grayscale')
        return pixels[..., 0].copy()


def measure(predicted, truth, trimap):
    if (any(not isinstance(a, np.ndarray) or a.dtype != np.uint8 or a.ndim != 2
            for a in (predicted, truth, trimap))
            or predicted.shape != truth.shape or trimap.shape != truth.shape
            or not np.isin(trimap, [0, 128, 255]).all()):
        raise ValueError('Native alpha sizes or trimap classes differ')
    unknown = trimap == 128
    if not unknown.any():
        raise ValueError('Trimap contains no unknown pixels to evaluate')
    difference = (predicted.astype(np.float64) - truth) / 255
    absolute = np.abs(difference)
    strata = {}
    # Disclose low-opacity losses that a whole-image average can conceal.
    for label, region in (
        ('background', truth == 0),
        ('low_alpha', (truth > 0) & (truth <= 64)),
        ('middle_alpha', (truth > 64) & (truth < 192)),
        ('high_alpha', (truth >= 192) & (truth < 255)),
        ('opaque', truth == 255),
    ):
        selected = region & unknown
        count = int(selected.sum())
        strata[label] = {'pixels': count, 'mae': float(absolute[selected].mean()) if count else None,
                         'signed_bias': float(difference[selected].mean()) if count else None}
    known = ~unknown
    return {
        'unknown_pixels': int(unknown.sum()),
        'unknown_mae': float(absolute[unknown].mean()),
        'unknown_mse': float(np.square(difference[unknown]).mean()),
        'unknown_sad_div_1000': float(absolute[unknown].sum() / 1000),
        'known_constraint_violations': int(np.count_nonzero(predicted[known] != trimap[known])),
        'strata': strata,
    }


def comparison(source, truth, predicted, box):
    x0, y0, x1, y1 = box
    w, h = x1-x0, y1-y0
    if not (0 <= x0 < x1 <= source.width and 0 <= y0 < y1 <= source.height):
        raise ValueError('Comparison crop must stay inside the native image')
    error = np.abs(predicted.astype(np.int16)-truth.astype(np.int16)).astype(np.uint8)
    board = Image.new('RGB', (w*2, (h+24)*2), '#24292f')
    draw = ImageDraw.Draw(board)
    for index, (label, picture) in enumerate([
        ('Native source', source), ('Ground-truth alpha', Image.fromarray(truth)),
        ('Predicted alpha', Image.fromarray(predicted)), ('Absolute error, no amplification', Image.fromarray(error)),
    ]):
        x, y = index % 2*w, index // 2*(h+24)
        draw.text((x+5, y+5), label, fill='white')
        board.paste(picture.crop(box).convert('RGB'), (x, y+24))
    return board


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('source', 'truth', 'trimap', 'output'):
        parser.add_argument('--'+option, required=True, type=Path)
    parser.add_argument('--prediction', action='append', required=True, metavar='NAME=FILE')
    parser.add_argument('--crop', nargs=4, type=int)
    args = parser.parse_args()
    truth, trimap = read_alpha(args.truth), read_alpha(args.trimap)
    with Image.open(args.source) as photo:
        if photo.mode != 'RGB' or photo.size != (truth.shape[1], truth.shape[0]):
            raise ValueError('Source must already be RGB and match native alpha coordinates')
        source = photo.copy()
    entries, names = [], set()
    for entry in args.prediction:
        name, separator, path = entry.partition('=')
        if not separator or not name or not all(c.isalnum() or c in '_-' for c in name) or name in names:
            raise ValueError('Each prediction needs a unique plain NAME=FILE')
        names.add(name)
        path = Path(path)
        prediction = read_alpha(path)
        entries.append((name, path, prediction, measure(prediction, truth, trimap)))
    # Refuse an existing destination, including symlinks. Never overwrite inputs.
    args.output.mkdir(parents=True, exist_ok=False)
    box = args.crop or (0, 0, source.width, source.height)
    report = {'scope': 'supplied unknown region; no visual pass/fail',
              'size': list(source.size), 'comparison_box': list(box),
              'inputs': {label: {'file': path.name, 'sha256': digest(path)} for label, path in
                         [('source', args.source), ('truth', args.truth), ('trimap', args.trimap)]},
              'predictions': {}}
    for name, path, prediction, metrics in entries:
        comparison(source, truth, prediction, box).save(args.output / (name+'-comparison.png'))
        report['predictions'][name] = {'file': path.name, 'sha256': digest(path), **metrics}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
