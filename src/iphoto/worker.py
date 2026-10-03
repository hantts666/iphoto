"""A persistent, CPU-only image worker using a small NDJSON protocol."""

import json
from pathlib import Path
import sys
import threading
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
    stats,
    validate_export_target,
)
from .document import (
    RASTER_CACHE,
    validate_layers,
    render_layers,
    overlay_mask,
    validate_mask,
)
from .preview_cache import LayerPreviewCache, composition_key
from .masks import clear_decode_cache
from .plugins import run_selection

PREVIEW_PNG_COMPRESSION = 3


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
    handlers = {}

    def register(name):
        def wrap(fn):
            handlers[name] = fn
            return fn

        return wrap

    def _warm_matting():
        try:
            from .matting.models import available as detail_available
            if detail_available():
                # Neural native selection does not use Numba. Avoid racing a
                # large JIT compilation against image decoding and first click.
                return
            from .matting.solver import warm

            warm()
        except Exception:
            pass  # Warm-up is best effort; first refine still works without it.

    @register("open")
    def _open(request):
        nonlocal source, proxy, previous_key, previous_result
        nonlocal current_overlay, previous_mask_key, current_original
        next_source = load_source(request["path"])
        if (
            request.get("expected_sha256")
            and next_source.digest != request["expected_sha256"]
        ):
            raise ValueError("源图片已变化，未打开该项目；当前编辑保持不变")
        # Previews, overlays and classical selection run on a 1600px proxy so
        # 24MP+ photos stay interactive; export and alpha matting keep full res.
        next_proxy = preview(next_source.image, 1600)
        original = cache / f"original-{request['id']}.png"
        next_proxy.save(original, icc_profile=SRGB_PROFILE, compress_level=PREVIEW_PNG_COMPRESSION)
        result = {
            "original": str(original),
            "path": str(next_source.path),
            "name": next_source.path.name,
            "width": next_source.image.width,
            "height": next_source.image.height,
            "sha256": next_source.digest,
            "warning": next_source.warning,
            "histogram": histogram(next_proxy),
            "stats": stats(next_proxy),
        }
        from .segmentation.face_detection import available as faces_available, detect
        result["faces"] = []
        if faces_available():
            try:
                detection_warnings = []
                result["faces"] = detect(next_proxy,warnings=detection_warnings)
                if detection_warnings:
                    result["face_detection_warning"] = detection_warnings[0]
            except Exception:
                # Optional localization cannot prevent opening a photograph.
                result["face_detection_warning"] = "本地人脸检测未完成，可使用文字定位或框选人脸"
        # Commit only after decoding, project verification and preview writing succeed.
        source, proxy = next_source, next_proxy
        composition_cache.clear()
        RASTER_CACHE.clear()
        clear_decode_cache()
        previous_key = previous_result = None
        current_overlay = previous_mask_key = None
        current_original = original
        assets.append(original)
        return result

    @register("render")
    def _render(request):
        nonlocal previous_key, previous_result, current_overlay, previous_mask_key
        started = time.perf_counter()
        stage_ms = dict.fromkeys(("prepare", "compose", "png", "histogram", "stats", "mask"), 0.0)
        layers = validate_layers(request["layers"]) if "layers" in request else None
        key = composition_key(layers) if layers is not None else request["recipe"]
        cache_hit = previous_result is not None and key == previous_key
        stage_ms["prepare"] = round((time.perf_counter() - started) * 1000, 3)
        if cache_hit:
            result = dict(previous_result)
        else:
            started = time.perf_counter()
            output = (
                composition_cache.render(proxy, layers)
                if layers is not None
                else render(proxy, Recipe.from_dict(request["recipe"]))
            )
            stage_ms["compose"] = round((time.perf_counter() - started) * 1000, 3)
            target = cache / f"preview-{request['id']}.png"
            started = time.perf_counter()
            output.save(target, icc_profile=SRGB_PROFILE, compress_level=PREVIEW_PNG_COMPRESSION)
            stage_ms["png"] = round((time.perf_counter() - started) * 1000, 3)
            assets.append(target)
            started = time.perf_counter()
            bins = histogram(output)
            stage_ms["histogram"] = round((time.perf_counter() - started) * 1000, 3)
            started = time.perf_counter()
            measurements = stats(output)
            stage_ms["stats"] = round((time.perf_counter() - started) * 1000, 3)
            result = {
                "preview": str(target),
                "histogram": bins,
                "stats": measurements,
            }
            previous_key, previous_result = key, dict(result)
        result["cache_hit"] = cache_hit
        result["reused_layers"] = composition_cache.reused
        started = time.perf_counter()
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
        stage_ms["mask"] = round((time.perf_counter() - started) * 1000, 3)
        result["stage_ms"] = stage_ms
        return result

    @register("repair_crop")
    def _repair_crop(request):
        from .ai_grounding import crop_pixels

        if request["source_sha"] != source.digest:
            raise ValueError("照片已变化，过期修复图未准备")
        box = crop_pixels(request["crop"], source.image.size)
        picture = source.image.crop(box)
        target = cache / f"repair-crop-{request['id']}.png"
        picture.save(target, icc_profile=SRGB_PROFILE)
        assets.append(target)
        return {"path": str(target), "crop_size": list(picture.size)}

    @register("object_crop")
    def _object_crop(request):
        from PIL import Image
        from .ai_grounding import crop_pixels

        if request["source_sha"] != source.digest:
            raise ValueError("照片已变化，过期目标细节未准备")
        box = crop_pixels(request["crop"], source.image.size)
        picture = source.image.crop(box)
        crop_size = [picture.width, picture.height]
        picture.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        target = cache / f"object-crop-{request['id']}.png"
        picture.save(target, icc_profile=SRGB_PROFILE)
        assets.append(target)
        return {"path": str(target), "crop_size": crop_size, "image_size": list(picture.size)}

    @register("selection")
    def _selection(request):
        mask, quality = run_selection(
            request["plugin"],
            proxy,
            request["mask"],
            **request.get("options", {}),
        )
        return {"mask": mask, "quality": quality}

    @register("segment")
    def _segment(request):
        from .segmentation.service import segment_jobs

        return segment_jobs(
            proxy,
            request.get("jobs"),
            tolerant=request.get("context", {}).get("purpose") == "precache",
            source=source.image,
        )

    @register("matte")
    def _matte(request):
        from .matting.service import refine_alpha

        mask, quality = refine_alpha(
            source.image, validate_mask(request["mask"]), request.get("radius", 8)
        )
        return {"mask": mask, "quality": quality}

    @register("interpret")
    def _interpret(request):
        recipe, summary = interpret_local(
            request["text"],
            Recipe.from_dict(request["recipe"]),
            request.get("locked", []),
        )
        return {"recipe": recipe.to_dict(), "summary": summary}

    @register("export")
    def _export(request):
        validate_export_target(source, request["path"])
        rendered = (
            render_layers(source.image, validate_layers(request["layers"]))
            if "layers" in request
            else None
        )
        target = export_image(
            source,
            Recipe.from_dict(request.get("recipe", {})),
            request["path"],
            rendered,
            jpeg_quality=request.get("jpeg_quality", 100),
        )
        return {
            "path": str(target),
            "width": source.image.width,
            "height": source.image.height,
            "format": target.suffix.lower(),
        }

    threading.Thread(target=_warm_matting, daemon=True).start()
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 192 * 1024 * 1024:
                raise ValueError("控制消息过大")
            request = json.loads(line)
            op = request["op"]
            if op == "shutdown":
                break
            handler = handlers.get(op)
            if handler is None:
                raise ValueError("未知操作")
            if op != "open" and source is None:
                raise ValueError("请先导入照片")
            started = time.perf_counter()
            result = handler(request)
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
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
