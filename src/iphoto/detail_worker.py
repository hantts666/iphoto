"""Source-resolution viewport tiles in a process separate from photo editing."""

import json
from pathlib import Path
import sys
from time import perf_counter

from .document import overlay_mask_tile, render_detail_tile, validate_layers, validate_mask
from .engine import SRGB_PROFILE, load_source


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    directory = Path(sys.argv[2])
    directory.mkdir(parents=True, exist_ok=True)
    source = None
    assets = []
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 192 * 1024 * 1024:
                raise ValueError("细节请求过大")
            request = json.loads(line)
            if request.get("op") != "detail":
                raise ValueError("未知细节操作")
            path = Path(request["source_path"])
            if source is None or source.path != path:
                next_source = load_source(path)
                if next_source.digest != request["source_sha"]:
                    raise ValueError("源照片已变化，细节视图未更新")
                source = next_source
            started = perf_counter()
            box = request["box"]
            tile = render_detail_tile(
                source.image, validate_layers(request["layers"]), box
            )
            target = directory / f"detail-{request['id']}.png"
            tile.save(target, icc_profile=SRGB_PROFILE)
            assets.append(target)
            mask_target = None
            if "mask" in request:
                mode = request.get("mask_view", "overlay")
                if mode not in ("overlay", "grayscale"):
                    raise ValueError("蒙版视图无效")
                mask_target = directory / f"detail-mask-{request['id']}.png"
                overlay_mask_tile(
                    validate_mask(request["mask"]), source.image.size, box, mode
                ).save(mask_target)
                assets.append(mask_target)
            while len(assets) > 8:
                assets.pop(0).unlink(missing_ok=True)
            response = {
                "id": request["id"], "op": "detail",
                "generation": request["generation"], "ok": True,
                "result": {"path": str(target), "box": box,
                           "mask": str(mask_target) if mask_target else "",
                           "elapsed_ms": round((perf_counter() - started) * 1000)},
            }
        except Exception as exc:
            response = {
                "id": request.get("id", -1), "op": "detail",
                "generation": request.get("generation", 0),
                "ok": False, "error": str(exc)[:1000],
            }
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
