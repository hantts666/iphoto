"""Lazy, bounded HSL kernel. The NumPy path remains the colour reference."""
import os

import numpy as np

# This module is imported only when an image process needs HSL. Keep its pool
# small even when a preview, original pixel tile and export run concurrently.
os.environ.setdefault("NUMBA_NUM_THREADS", str(min(4, os.cpu_count() or 1)))
from numba import config, get_num_threads, njit, prange, set_num_threads

if config.DISABLE_JIT:
    raise RuntimeError("Compiled colour processing is disabled")


@njit(cache=True, nogil=True, parallel=True, fastmath=False)
def _fused(rgb, controls):
    result = np.empty_like(rgb)
    centers = np.array((0, 30, 60, 120, 180, 240, 270, 300), dtype=np.float32)
    f = np.float32
    # Explicit float32 intermediate operations follow the vector reference.
    # No per-pixel arrays, quantized colour grid, or spatial interpolation.
    for i in prange(rgb.shape[0]):
        red, green, blue = rgb[i, 0], rgb[i, 1], rgb[i, 2]
        maximum, minimum = max(red, green, blue), min(red, green, blue)
        delta = f(maximum - minimum)
        light = f(f(maximum + minimum) * f(.5))
        safe = max(delta, f(1e-7))
        if maximum == red:
            hue = f(f(f((green - blue) / safe) % f(6)) * f(60))
        elif maximum == green:
            hue = f(f(f((blue - red) / safe) + f(2)) * f(60))
        else:
            hue = f(f(f((red - green) / safe) + f(4)) * f(60))
        sat = f(delta / max(f(f(1) - abs(f(f(2) * light - f(1)))), f(1e-7))) if delta > 0 else f(0)
        neutral = min(f(1), max(f(0), f(sat / f(.1))))
        neutral = f(f(neutral * neutral) * f(f(3) - f(f(2) * neutral)))
        hue_shift = sat_shift = light_shift = f(0)
        for band in range(8):
            if not (controls[band, 0] or controls[band, 1] or controls[band, 2]):
                continue
            center = centers[band]
            previous = centers[band - 1] if band else f(-60)
            following = centers[band + 1] if band < 7 else f(360)
            distance = f(f(f(f(hue - center) + f(180)) % f(360)) - f(180))
            width = f(center - previous) if distance < 0 else f(following - center)
            ratio = min(f(1), max(f(0), f(abs(distance) / width)))
            weight = f(f(f(f(1) + np.cos(f(f(np.pi) * ratio))) * f(.5)) * neutral)
            if controls[band, 0]:
                hue_shift = f(hue_shift + f(weight * controls[band, 0]))
            if controls[band, 1]:
                sat_shift = f(sat_shift + f(weight * controls[band, 1]))
            if controls[band, 2]:
                light_shift = f(light_shift + f(weight * controls[band, 2]))
        if not (hue_shift or sat_shift or light_shift):
            result[i] = rgb[i]
            continue
        hue = f(f(hue + hue_shift) % f(360))
        amount = f(sat_shift / f(100))
        sat = min(f(1), max(f(0), f(sat + f(f(f(1) - sat) * amount if amount >= 0 else f(sat * amount)))))
        amount = f(light_shift / f(100))
        light = min(f(1), max(f(0), f(light + f(f(f(1) - light) * amount if amount >= 0 else f(light * amount)))))
        amplitude = f(sat * min(light, f(f(1) - light)))
        for channel in range(3):
            offset = f(0 if channel == 0 else 8 if channel == 1 else 4)
            k = f(f(offset + f(hue / f(30))) % f(12))
            term = max(f(-1), min(min(f(k - f(3)), f(f(9) - k)), f(1)))
            result[i, channel] = min(f(1), max(f(0), f(light - f(amplitude * term))))
    return result


def run(rgb, controls):
    previous = get_num_threads()
    try:
        set_num_threads(min(previous, 4))
        return _fused(np.ascontiguousarray(rgb).reshape(-1, 3), controls).reshape(rgb.shape)
    finally:
        set_num_threads(previous)


def warm():
    controls = np.zeros((8, 3), np.float32)
    controls[0, 0] = 20
    run(np.array([[.6, .2, .1]], np.float32), controls)
