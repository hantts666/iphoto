"""Bounded, shape-preserving curves in encoded sRGB (0..255)."""
from functools import lru_cache
import math

import numpy as np

FIELDS = ('curve_rgb', 'curve_red', 'curve_green', 'curve_blue')
LABELS = dict(zip(FIELDS, ('RGB 总曲线', '红通道曲线', '绿通道曲线', '蓝通道曲线')))
MAX_POINTS = 16
_accelerator_failed = False
SCHEMA = {'type': 'array', 'maxItems': MAX_POINTS, 'items': {
    'type': 'array', 'minItems': 2, 'maxItems': 2,
    'items': {'type': 'integer', 'minimum': 0, 'maximum': 255}}}


def validate(points):
    if not isinstance(points, (list, tuple)) or len(points) == 1 or len(points) > MAX_POINTS:
        raise ValueError('曲线应为空（保持原值）或包含2～16个控制点')
    clean = []
    for point in points:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or not 0 <= v <= 255 or int(v) != v for v in point)):
            raise ValueError('曲线输入和输出应为0～255的整数')
        clean.append(tuple(int(v) for v in point))
    if clean and (clean[0][0] != 0 or clean[-1][0] != 255
                  or any(a[0] >= b[0] for a, b in zip(clean, clean[1:]))):
        raise ValueError('曲线输入必须从0到255严格递增，不得重复')
    # Keep the default endpoint identity empty, preserving editable middle knots.
    return () if clean == [(0, 0), (255, 255)] else tuple(clean)


@lru_cache(maxsize=64)
def _coefficients(points):
    x, y = np.asarray(points, dtype=np.float64).T
    h = np.diff(x)
    slopes = np.diff(y) / h
    d = np.zeros_like(x)
    if len(x) == 2:
        d[:] = slopes[0]
    else:
        for i in range(1, len(x) - 1):
            if slopes[i-1] * slopes[i] > 0:
                w1, w2 = 2*h[i] + h[i-1], h[i] + 2*h[i-1]
                d[i] = (w1+w2)/(w1/slopes[i-1]+w2/slopes[i])
        for i, h0, h1, m0, m1 in ((0,h[0],h[1],slopes[0],slopes[1]),
                                   (-1,h[-1],h[-2],slopes[-1],slopes[-2])):
            edge = ((2*h0+h1)*m0-h0*m1)/(h0+h1)
            if edge*m0 <= 0:
                edge = 0
            elif m0*m1 <= 0 and abs(edge) > abs(3*m0):
                edge = 3*m0
            d[i] = edge
    # Polynomial in distance from each left knot; Horner evaluation.
    c3 = (d[:-1]+d[1:]-2*slopes)/(h*h)
    c2 = (3*slopes-2*d[:-1]-d[1:])/h
    arrays = (x, c3, c2, d[:-1].copy(), y[:-1].copy())
    for array in arrays:
        array.setflags(write=False)
    return arrays


def evaluate(values, points):
    """Evaluate C1 PCHIP without overshooting neighbouring output knots."""
    if not points:
        return values
    x, c3, c2, c1, c0 = _coefficients(points)
    values = np.clip(values, 0, 1) * 255
    index = np.searchsorted(x, values, side='right').clip(1, len(x)-1)-1
    t = values-x[index]
    result = ((c3[index]*t+c2[index])*t+c1[index])*t+c0[index]
    return (np.clip(result, 0, 255)/255).astype(np.float32)


def apply(encoded, recipe):
    channels = (recipe.curve_red, recipe.curve_green, recipe.curve_blue)
    if recipe.curve_rgb or any(channels):
        encoded = encoded.copy()
        for index, points in enumerate(channels):
            if recipe.curve_rgb:
                # Evaluate one channel at a time to bound large-strip temporaries.
                encoded[..., index] = evaluate(encoded[..., index], recipe.curve_rgb)
            if points:
                encoded[..., index] = evaluate(encoded[..., index], points)
    return encoded


@lru_cache(maxsize=16)
def _packed_coefficients(curves):
    """A small immutable kernel input, shared by preview/tile/export threads."""
    packed = np.zeros((4, 5, MAX_POINTS), np.float64)
    counts = np.zeros(4, np.int64)
    for index, points in enumerate(curves):
        if points:
            counts[index] = len(points)
            for row, values in enumerate(_coefficients(points)):
                packed[index, row, :len(values)] = values
    packed.setflags(write=False)
    counts.setflags(write=False)
    return packed, counts


def apply_fast(encoded, recipe):
    """Evaluate large strips without float64 gather/polynomial temporaries."""
    global _accelerator_failed
    curves = tuple(getattr(recipe, key) for key in FIELDS)
    if not any(curves):
        return encoded
    if _accelerator_failed or encoded.dtype != np.float32 or encoded.size < 3 * 4096:
        return apply(encoded, recipe)
    try:
        from .color_accel import run_curves
        return run_curves(encoded, *_packed_coefficients(curves))
    except Exception:
        # Editing remains available with the same reference math if the optional
        # compiler/cache is unavailable. Do not retry a failure on every strip.
        _accelerator_failed = True
        return apply(encoded, recipe)


@lru_cache(maxsize=64)
def _max_slope(points):
    """Bound the gain of each continuous polynomial, including inner extrema."""
    if not points:
        return 1.
    x, c3, c2, c1, _ = _coefficients(points)
    width = np.diff(x)
    slopes = [np.abs(c1), np.abs((3*c3*width + 2*c2)*width + c1)]
    vertex = np.divide(-c2, 3*c3, out=np.zeros_like(c2), where=c3 != 0)
    inside = (c3 != 0) & (vertex > 0) & (vertex < width)
    slopes.append(np.abs(((3*c3*vertex + 2*c2)*vertex + c1)[inside]))
    return max(float(np.max(values, initial=0)) for values in slopes)


@lru_cache(maxsize=16)
def _rounding_window(curves):
    master = _max_slope(curves[0])
    channel = np.array([_max_slope(points) for points in curves[1:]], np.float64)
    # Carry the HSL kernel's existing .003-byte ambiguity margin through both
    # curves, plus a float32 rounding margin between and after the curves.
    window = np.maximum(.003, .003*master*channel + .0001*(1+channel)).astype(np.float32)
    window.setflags(write=False)
    return window


def rounding_window(recipe):
    return _rounding_window(tuple(getattr(recipe, key) for key in FIELDS))
