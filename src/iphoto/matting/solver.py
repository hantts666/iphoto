"""Bounded CPU adapter for PyMatting's published closed-form implementation."""

from time import perf_counter
import os
from pathlib import Path

import numpy as np

MAX_REFERENCE_AREA = 1_000_000
MAX_REFERENCE_UNKNOWN = 160_000
MAX_REFERENCE_FILL = 16_000_000
MAX_REFERENCE_SIDE = 1280


def _joint_references(trimap, tile_size):
    """Share anchors across a bounded, connected transparent surface.

    Separate local solutions can disagree even when each window has both
    classes. A broad connected unknown region must use the same color anchors.
    Small working boundaries retain their existing local context and budget.
    """
    h, w = trimap.shape
    if (h*w > MAX_REFERENCE_AREA or max(h,w) > MAX_REFERENCE_SIDE
            or np.count_nonzero(trimap == .5) > MAX_REFERENCE_UNKNOWN
            or not np.any(trimap == 0) or not np.any(trimap == 1)):
        return False
    # Check connectivity only after the independent area/unknown bounds. This
    # must never allocate component labels for a whole high-resolution photo.
    unknown = trimap == .5
    broad_area = min(tile_size,256) ** 2
    if np.count_nonzero(unknown) > broad_area:
        import cv2
        _, _, stats, _ = cv2.connectedComponentsWithStats(
            unknown.astype(np.uint8), connectivity=8
        )
        if np.any(stats[1:, cv2.CC_STAT_AREA] > broad_area):
            return True
    for y in range(0,h,tile_size):
        for x in range(0,w,tile_size):
            if not np.any(trimap[y:y+tile_size,x:x+tile_size] == .5):
                continue
            local = trimap[max(0,y-64):min(h,y+tile_size+64),max(0,x-64):min(w,x+tile_size+64)]
            if not np.any(local == 0) or not np.any(local == 1):
                return True
    return False


def _joint_window(trimap, tile_size):
    """Find a small native solve inside a large canvas without full-size labels."""
    h, w = trimap.shape
    if h*w <= MAX_REFERENCE_AREA and max(h,w) <= MAX_REFERENCE_SIDE:
        return (0,0,w,h) if _joint_references(trimap,tile_size) else None
    left, top, right, bottom, count = w, h, 0, 0, 0
    # Scan fixed-height strips; even on 60MP input there is no additional
    # whole-photo boolean/label array. Stop when a joint solve cannot fit.
    for y in range(0,h,256):
        active = trimap[y:y+256] == .5
        count += int(np.count_nonzero(active))
        if count > MAX_REFERENCE_UNKNOWN:
            return None
        rows = np.flatnonzero(np.any(active,axis=1))
        if not rows.size:
            continue
        columns = np.flatnonzero(np.any(active,axis=0))
        left, right = min(left,int(columns[0])), max(right,int(columns[-1])+1)
        top, bottom = min(top,y+int(rows[0])), max(bottom,y+int(rows[-1])+1)
        if (right-left)*(bottom-top) > MAX_REFERENCE_AREA or max(right-left,bottom-top) > MAX_REFERENCE_SIDE:
            return None
    if not count:
        return None
    box = (max(0,left-64),max(0,top-64),min(w,right+64),min(h,bottom+64))
    x0,y0,x1,y1 = box
    if (x1-x0)*(y1-y0) > MAX_REFERENCE_AREA or max(x1-x0,y1-y0) > MAX_REFERENCE_SIDE:
        return None
    return box if _joint_references(trimap[y0:y1,x0:x1],tile_size) else None


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
    joint_box = _joint_window(trimap,tile_size)
    started = perf_counter()
    tiles = 0
    # Solve overlapping-context ROIs at the working resolution.
    # A distant opaque reference must not be discarded solely because it falls
    # outside a fine strand's tile. Publish only the core, even after expansion.
    cores = ([joint_box] if joint_box else
             ((x,y,min(w,x+tile_size),min(h,y+tile_size))
              for y in range(0,h,tile_size) for x in range(0,w,tile_size)))
    for x,y,x1,y1 in cores:
        unknown = trimap[y:y1, x:x1] == 0.5
        if not unknown.any():
            continue
        if joint_box:
            (left,top,right,bottom), expanded = joint_box, True
        else:
            (left,top,right,bottom), expanded = _reference_box(trimap, (x,y,x1,y1))
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
