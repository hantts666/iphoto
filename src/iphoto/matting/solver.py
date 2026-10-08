"""Bounded CPU adapter for PyMatting's published closed-form implementation."""

from time import perf_counter
import os
from pathlib import Path

import numpy as np

MAX_REFERENCE_AREA = 1_000_000
MAX_REFERENCE_UNKNOWN = 128_000
MAX_REFERENCE_FILL = 16_000_000
MAX_REFERENCE_SIDE = 1280


def _joint_references(trimap, tile_size):
    """A small source crop can share distant anchors in a single solve.

    Never widen a whole-photo solve or replace a working local context. A joint
    solve is reserved for bounded crops that would otherwise lose a class.
    """
    h, w = trimap.shape
    if (h*w > MAX_REFERENCE_AREA or max(h,w) > MAX_REFERENCE_SIDE
            or np.count_nonzero(trimap == .5) > MAX_REFERENCE_UNKNOWN
            or not np.any(trimap == 0) or not np.any(trimap == 1)):
        return False
    for y in range(0,h,tile_size):
        for x in range(0,w,tile_size):
            if not np.any(trimap[y:y+tile_size,x:x+tile_size] == .5):
                continue
            local = trimap[max(0,y-64):min(h,y+tile_size+64),max(0,x-64):min(w,x+tile_size+64)]
            if not np.any(local == 0) or not np.any(local == 1):
                return True
    return False


def _reference_box(trimap, core):
    """Find real color anchors without manufacturing constraints at a seam.

    Keep the established 64px context when it has both classes. Only a missing
    class permits expansion, bounded independently of the full photograph.
    """
    h, w = trimap.shape
    x, y, x1, y1 = core
    for halo in (64, 128, 256, 384, 512):
        box = (max(0, x-halo), max(0, y-halo), min(w, x1+halo), min(h, y1+halo))
        left, top, right, bottom = box
        if halo > 64 and ((right-left)*(bottom-top) > MAX_REFERENCE_AREA
                          or max(right-left,bottom-top) > MAX_REFERENCE_SIDE):
            break
        region = trimap[top:bottom, left:right]
        if np.any(region == 0) and np.any(region == 1):
            if halo > 64 and np.count_nonzero(region == .5) > MAX_REFERENCE_UNKNOWN:
                raise ValueError('附近参照需要计算的透明区域过大，原选区保留；请分区域细化或补充参照')
            return box, halo > 64
    raise ValueError('附近缺少前景或背景参照，原选区保留；请补充不透明主体和背景范围')


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


def solve_alpha(image, trimap, *, tile_size=256, byte_output=False, linear=True):
    # Lazy import in the image worker only. Avoid a CPU-wide parallel pool.
    os.environ.setdefault("NUMBA_NUM_THREADS", "4")
    from pymatting import estimate_alpha_cf, ichol

    if byte_output:
        alpha = np.zeros(trimap.shape, dtype=np.uint8)
        alpha[trimap == 1] = 255
    else:
        alpha = trimap.copy()
    h, w = trimap.shape
    joint = _joint_references(trimap, tile_size)
    if joint:
        tile_size = max(h,w)
    started = perf_counter()
    tiles = 0
    # Solve overlapping-context ROIs at the working resolution.
    # A distant opaque reference must not be discarded solely because it falls
    # outside a fine strand's tile. Publish only the core, even after expansion.
    for y in range(0, h, tile_size):
        for x in range(0, w, tile_size):
            y1, x1 = min(h, y + tile_size), min(w, x + tile_size)
            unknown = trimap[y:y1, x:x1] == 0.5
            if not unknown.any():
                continue
            (left, top, right, bottom), expanded = _reference_box(trimap, (x, y, x1, y1))
            expanded = expanded or joint
            region = trimap[top:bottom, left:right].astype(np.float64)
            if perf_counter() - started > 180:
                raise ValueError("边缘细化超时，原选区保留；请减小范围再试")
            rgb = np.asarray(image.crop((left, top, right, bottom)).convert("RGB"), dtype=np.float64) / 255
            if linear:
                rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
            try:
                matte = estimate_alpha_cf(
                    rgb,
                    region.copy(),
                    preconditioner=lambda matrix: ichol(matrix, max_nnz=MAX_REFERENCE_FILL if expanded else 4_000_000),
                    laplacian_kwargs={"epsilon": 1e-6},
                    cg_kwargs={"maxiter": 500 if expanded else 250, "rtol": 1e-5},
                )
            except ValueError as exc:
                raise ValueError(
                    "这段边缘未能稳定求解，原选区保留；请减小范围再试"
                ) from exc
            if matte.shape != region.shape or not np.isfinite(matte).all():
                raise ValueError("透明度求解失败，原选区保留")
            core = matte[y - top : y1 - top, x - left : x1 - left]
            target = alpha[y:y1, x:x1]
            if byte_output:
                # Match the historical float32-to-byte rounding while storing
                # only one byte per output pixel for a full-resolution matte.
                values = np.asarray(core[unknown], dtype=np.float32)
                np.clip(values, 0, 1, out=values)
                np.multiply(values, 255, out=values)
                np.rint(values, out=values)
                target[unknown] = values.astype(np.uint8)
            else:
                target[unknown] = core[unknown]
            tiles += 1
    if not byte_output:
        np.clip(alpha, 0, 1, out=alpha)
    return alpha, tiles


def warm():
    """Compile the matting kernels on a tiny trimap so first use is fast."""
    from PIL import Image

    trimap = np.zeros((96, 96), dtype=np.float64)
    trimap[:32] = 1.0
    trimap[32:64] = 0.5
    image = Image.new("RGB", (96, 96), (128, 128, 128))
    solve_alpha(image, trimap, tile_size=64)
