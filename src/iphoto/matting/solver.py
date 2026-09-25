"""Bounded CPU adapter for PyMatting's published closed-form implementation."""

from time import perf_counter
import os
from pathlib import Path

import numpy as np


def _enable_numba_cache():
    # Persist numba JIT artifacts so the first transparent-edge refine after a
    # relaunch reuses compiled code instead of recompiling for several seconds.
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    directory = root / "iPhoto" / "numba-cache"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("NUMBA_CACHE_DIR", str(directory))
    except OSError:
        pass


_enable_numba_cache()


def solve_alpha(image, trimap, *, tile_size=256):
    # Lazy import in the image worker only. Avoid a CPU-wide parallel pool.
    os.environ.setdefault("NUMBA_NUM_THREADS", "4")
    from pymatting import estimate_alpha_cf, ichol

    alpha = trimap.copy()
    h, w = trimap.shape
    started = perf_counter()
    tiles = 0
    # Solve small overlapping-context ROIs at the working resolution. The
    # retained core has 64 pixels of context, rather than an artificial hard
    # foreground/background constraint at a tile boundary.
    halo = 64
    for y in range(0, h, tile_size):
        for x in range(0, w, tile_size):
            y1, x1 = min(h, y + tile_size), min(w, x + tile_size)
            unknown = trimap[y:y1, x:x1] == 0.5
            if not unknown.any():
                continue
            top, left = max(0, y - halo), max(0, x - halo)
            bottom, right = min(h, y1 + halo), min(w, x1 + halo)
            region = trimap[top:bottom, left:right].astype(np.float64)
            if not np.any(region == 0) or not np.any(region == 1):
                raise ValueError("这段边缘缺少前景或背景参照，请缩小范围或补充选区")
            if perf_counter() - started > 180:
                raise ValueError("边缘细化超时，原选区保留；请减小范围再试")
            rgb = np.asarray(image.crop((left, top, right, bottom)).convert("RGB"), dtype=np.float64) / 255
            rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
            try:
                matte = estimate_alpha_cf(
                    rgb,
                    region.copy(),
                    preconditioner=lambda matrix: ichol(matrix, max_nnz=4_000_000),
                    laplacian_kwargs={"epsilon": 1e-6},
                    cg_kwargs={"maxiter": 250, "rtol": 1e-5},
                )
            except ValueError as exc:
                raise ValueError(
                    "这段边缘未能稳定求解，原选区保留；请减小范围再试"
                ) from exc
            if not np.isfinite(matte).all():
                raise ValueError("透明度求解失败，原选区保留")
            core = matte[y - top : y1 - top, x - left : x1 - left]
            alpha[y:y1, x:x1][unknown] = core[unknown]
            tiles += 1
    return np.clip(alpha, 0, 1), tiles


def warm():
    """Compile the matting kernels on a tiny trimap so first use is fast."""
    from PIL import Image

    trimap = np.zeros((96, 96), dtype=np.float64)
    trimap[:32] = 1.0
    trimap[32:64] = 0.5
    image = Image.new("RGB", (96, 96), (128, 128, 128))
    solve_alpha(image, trimap, tile_size=64)
