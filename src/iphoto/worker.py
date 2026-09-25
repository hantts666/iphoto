"""A persistent, CPU-only image worker using a small NDJSON protocol."""

import json
from pathlib import Path
import sys
import time

from .engine import (
    Recipe,
    SRGB_PROFILE,
    export_image,
    histogram,
    interpret_local,
    load_source,
    preview,
    render,
    validate_export_target,
)
from .document import validate_layers, render_layers, overlay_mask, validate_mask
from .preview_cache import LayerPreviewCache, composition_key
from .plugins import run_selection


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    source = proxy = None
    cache = Path(sys.argv[2])
    cache.mkdir(parents=True, exist_ok=True)
    assets = []
    previous_key = previous_result = None
    current_original = None
    current_overlay = previous_mask_key = None
    composition_cache = LayerPreviewCache()
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 192 * 1024 * 1024:
                raise ValueError("控制消息过大")
            request = json.loads(line)
            op = request["op"]
            if op == "shutdown":
                break
            started = time.perf_counter()
            result = {}
            if op == "open":
                next_source = load_source(request["path"])
                if (
                    request.get("expected_sha256")
                    and next_source.digest != request["expected_sha256"]
                ):
                    raise ValueError("源图片已变化，未打开该项目；当前编辑保持不变")
                next_proxy = next_source.image
                original = cache / f"original-{request['id']}.png"
                next_proxy.save(original, icc_profile=SRGB_PROFILE)
                result = {
                    "original": str(original),
                    "path": str(next_source.path),
                    "name": next_source.path.name,
                    "width": next_source.image.width,
                    "height": next_source.image.height,
                    "sha256": next_source.digest,
                    "warning": next_source.warning,
                    "histogram": histogram(next_proxy),
                }
                # Commit only after decoding, project verification and preview writing succeed.
                source, proxy = next_source, next_proxy
                composition_cache.clear()
                previous_key = previous_result = None
                current_overlay = previous_mask_key = None
                current_original = original
                assets.append(original)
            elif source is None:
                raise ValueError("请先导入照片")
            elif op == "render":
                layers = (
                    validate_layers(request["layers"]) if "layers" in request else None
                )
                key = (
                    composition_key(layers) if layers is not None else request["recipe"]
                )
                cache_hit = previous_result is not None and key == previous_key
                if cache_hit:
                    result = dict(previous_result)
                else:
                    output = (
                        composition_cache.render(proxy, layers)
                        if layers is not None
                        else render(proxy, Recipe.from_dict(request["recipe"]))
                    )
                    target = cache / f"preview-{request['id']}.png"
                    output.save(target, icc_profile=SRGB_PROFILE)
                    assets.append(target)
                    result = {"preview": str(target), "histogram": histogram(output)}
                    previous_key, previous_result = key, dict(result)
                result["cache_hit"] = cache_hit
                result["reused_layers"] = composition_cache.reused
                if "mask" in request:
                    mask = validate_mask(request["mask"])
                    mask_key = (
                        {k: v for k, v in mask.items() if k != "label"},
                        request.get("mask_view", "overlay"),
                    )
                    result["mask_cache_hit"] = (
                        current_overlay is not None and mask_key == previous_mask_key
                    )
                    if not result["mask_cache_hit"]:
                        current_overlay = cache / f"mask-{request['id']}.png"
                        overlay_mask(
                            mask, proxy.size, request.get("mask_view", "overlay")
                        ).save(current_overlay)
                        assets.append(current_overlay)
                        previous_mask_key = mask_key
                    result["mask"] = str(current_overlay)
            elif op == "selection":
                mask, quality = run_selection(
                    request["plugin"],
                    proxy,
                    request["mask"],
                    **request.get("options", {}),
                )
                result = {"mask": mask, "quality": quality}
            elif op == "segment":
                from .segmentation.service import segment

                jobs = request.get("jobs")
                if not isinstance(jobs, list) or not 1 <= len(jobs) <= 16:
                    raise ValueError("一次最多分割 16 个对象")
                items = []
                for job in jobs:
                    mask, quality = segment(source.image, job.get("hint"), job.get("points"))
                    items.append({"id": job["id"], "mask": mask, "quality": quality})
                result = {"items": items}
            elif op == "matte":
                from .matting.service import refine_alpha

                mask, quality = refine_alpha(
                    source.image, validate_mask(request["mask"]), request.get("radius", 8)
                )
                result = {"mask": mask, "quality": quality}
            elif op == "interpret":
                recipe, summary = interpret_local(
                    request["text"],
                    Recipe.from_dict(request["recipe"]),
                    request.get("locked", []),
                )
                result = {"recipe": recipe.to_dict(), "summary": summary}
            elif op == "export":
                validate_export_target(source, request["path"])
                output = (
                    render_layers(source.image, validate_layers(request["layers"]))
                    if "layers" in request
                    else None
                )
                target = export_image(
                    source,
                    Recipe.from_dict(request.get("recipe", {})),
                    request["path"],
                    output,
                    jpeg_quality=request.get("jpeg_quality", 100),
                )
                result = {"path": str(target), "width": source.image.width, "height": source.image.height, "format": target.suffix.lower()}
            else:
                raise ValueError("未知操作")
            # Retain a few generations for async QML image loading; never touch user files.
            while len(assets) > 8:
                old = assets.pop(0)
                if old in (current_original, current_overlay) or (
                    previous_result and str(old) == previous_result["preview"]
                ):
                    assets.append(old)
                else:
                    try:
                        old.unlink(missing_ok=True)
                    except PermissionError:
                        pass  # QML may still be loading it; clean the session directory on exit.
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            response = {
                "id": request["id"],
                "op": op,
                "generation": request.get("generation", 0),
                "ok": True,
                "result": result,
            }
        except Exception as exc:
            response = {
                "id": request.get("id", -1),
                "op": request.get("op", "unknown"),
                "generation": request.get("generation", 0),
                "ok": False,
                "error": str(exc),
            }
        output = None  # Do not retain a full-resolution export buffer while idle.
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
