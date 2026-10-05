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


def _prune_assets(assets, keep):
    """Retain active image batches as well as asynchronously displayed frames."""
    limit = max(8, len(keep))
    while len(assets) > limit:
        old = assets.pop(0)
        if old in keep:
            assets.append(old)
        else:
            try:
                old.unlink(missing_ok=True)
            except PermissionError:
                pass  # The session directory is also cleaned on exit.


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
    current_crop_assets = set()
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
        nonlocal current_crop_assets
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
        current_crop_assets = set()
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
                key if request.get('mask_view') in ('white','black') else None,
            )
            result["mask_cache_hit"] = (
                current_overlay is not None and mask_key == previous_mask_key
            )
            if not result["mask_cache_hit"]:
                current_overlay = cache / f"mask-{request['id']}.png"
                mode = request.get('mask_view', 'overlay')
                if mode in ('white','black'):
                    from .cutout import background_view
                    if cache_hit:
                        from PIL import Image
                        with Image.open(result['preview']) as cached:
                            output = cached.copy()
                    # Native colors and alpha share one context in preview,
                    # detail and export; the ordinary photograph stays intact.
                    background_view(source.image, layers or [], mask, mode, output).save(current_overlay)
                else:
                    overlay_mask(mask, proxy.size, mode).save(current_overlay)
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

    @register("mask_refinement_crop")
    def _mask_refinement_crop(request):
        nonlocal current_crop_assets
        from .ai_mask_refinement import prepare_crop

        if request["source_sha"] != source.digest:
            raise ValueError("照片已变化，过期范围修正图未准备")
        if request.get('verify'):
            picture, mask, overlay, box = prepare_crop(source.image, request['mask'], reference=request['reference_mask'],
                opacity=.5 if request.get('restore_verify') is True or request.get('reselect_verify') is True else .4,
                reselection_scope=request.get('reselect_scope') if request.get('reselect_verify') is True else None)
            regions = []
        elif request.get('review'):
            from .ai_mask_review import prepare_review
            picture, mask, overlay, box, regions = prepare_review(source.image, request['mask'], reference=request.get('reference_mask'))
        else:
            picture, mask, overlay, box = prepare_crop(source.image, request["mask"])
            regions = []
        paths = [cache / f"mask-refinement-{request['id']}-{kind}.png" for kind in ("photo", "mask", "overlay")]
        native_size = picture.size
        def display(item):
            if not request.get('verify') or item.mode == 'L':
                return item
            from PIL import Image
            scale = min(3, 1024/max(native_size))
            return item.resize(tuple(max(1, round(value*scale)) for value in native_size), Image.Resampling.LANCZOS)
        for item, path in zip((picture, mask, overlay), paths):
            display(item).save(path, **({"icc_profile": SRGB_PROFILE} if item.mode == 'RGB' else {}))
            assets.append(path)
        extra = {}
        crop_assets = set(paths)
        if (request.get('review') or request.get('verify')) and request.get('reference_mask') is not None:
            from .ai_mask_review import comparison_images, reselection_images

            if request.get('reselect_verify') is True:
                kinds=('reference','changes','additions')
                comparisons=reselection_images(source.image,request['mask'],request['reference_mask'],box,request['reselect_scope'])
            else:
                kinds=('reference','changes')
                comparisons=comparison_images(source.image,request['mask'],request['reference_mask'],box,restore=request.get('restore_verify') is True)
            for kind,item in zip(kinds,comparisons):
                path = cache / f"mask-refinement-{request['id']}-{kind}.png"
                display(item).save(path, icc_profile=SRGB_PROFILE); assets.append(path)
                crop_assets.add(path)
                extra[kind+'_path'] = str(path)
        if request.get('context_crop') is not None:
            from .ai_mask_review import context_image

            path = cache / f"mask-refinement-{request['id']}-context.png"
            context_image(source.image, request['context_crop'], box).save(path, icc_profile=SRGB_PROFILE)
            assets.append(path); extra['context_path'] = str(path)
            crop_assets.add(path)
        current_crop_assets = crop_assets
        return {"path": str(paths[0]), "mask_path": str(paths[1]), "overlay_path": str(paths[2]),
                "crop_box": box, "crop_size": list(native_size), "source_size": list(source.image.size), 'regions': regions, **extra}

    @register('mask_refinement_apply')
    def _mask_refinement_apply(request):
        from .ai_mask_review import remove_regions

        if request['source_sha'] != source.digest:
            raise ValueError('照片已变化，过期复查排除未应用')
        mask = remove_regions(source.image, request['mask'], request['exclude_regions'], request['regions'], reference=request.get('reference_mask'))
        return {'mask': mask}

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

    @register("channel_preview")
    def _channel_preview(request):
        nonlocal current_crop_assets
        from .matting.channels import suggest, whole_options, preview as channel_preview
        if request.get('expected_sha256') != source.digest:
            raise ValueError('照片已变化，通道预览未应用')
        options = dict(request['options'])
        radius = max(2, round(options['radius']*min(proxy.size)/min(source.image.size)))
        if request.get('initial'):
            from .document import raster_mask
            whole=not request['mask'].get('semantic_target') and raster_mask(request['mask'],source.image.size).getextrema()==(255,255)
            if whole:
                options=whole_options()
            else:
                options, _ = suggest(proxy, request['mask'], radius)
                options['radius'] = request['options']['radius']
                options.update({k:request['options'][k] for k in ('interior','detail','color') if k in request['options']})
        alpha, channel, score = channel_preview(proxy, request['mask'], options, radius)
        path = cache / f"channel-preview-{request['id']}.png"
        alpha.save(path, compress_level=3)
        result={'path':str(path),'channel':channel,'score':score,'options':options}
        current_crop_assets = {path}
        if request.get('display_preview') is True:
            from .matting.channel_view import previews
            pictures,focused=previews(proxy,alpha,request['mask'],radius,whole=bool(options.get('whole')))
            views={}
            for name,picture in pictures.items():
                destination=cache / f"channel-view-{request['id']}-{name}.png"
                picture.save(destination,compress_level=3)
                views[name]=str(destination)
                current_crop_assets.add(destination)
            result.update(views=views,focused=focused)
        assets.extend(current_crop_assets)
        return result

    @register('matte_candidate')
    def _matte_candidate(request):
        nonlocal current_crop_assets
        from .matte_review import render_review
        if request.get('expected_sha256') != source.digest:
            raise ValueError('照片已变化，抠图检查未应用')
        result=render_review(source.image,request['layers'],request['mask'],cache,request['id'],request.get('review_boxes'))
        current_crop_assets={Path(item['path']) for item in result['images']}
        assets.extend(current_crop_assets)
        return result

    @register("generative_crop")
    def _generative_crop(request):
        nonlocal current_crop_assets
        from .photo_strategy import soften_effect_mask
        from .document import raster_mask
        if request.get('expected_sha256') != source.digest:
            raise ValueError('照片已变化，选区图像编辑未应用')
        proposed = validate_layers(request['proposed'])
        if len(proposed) != 1:
            raise ValueError('选区图像编辑需要一个目标')
        mask = proposed[0]['mask']
        if request.get('soften'):
            mask = soften_effect_mask(mask, source.image.size)
            proposed[0]['mask'] = mask
        bounds = raster_mask(mask, source.image.size).getbbox()
        if not bounds:
            raise ValueError('选区为空，图像编辑未执行')
        x0,y0,x1,y1 = bounds
        margin = max(32, round(max(x1-x0,y1-y0)*.2))
        box = [max(0,x0-margin),max(0,y0-margin),min(source.image.width,x1+margin),min(source.image.height,y1+margin)]
        current = render_layers(source.image, validate_layers(request['before']))
        crop = current.crop(tuple(box))
        scale = min(1536/max(crop.size), max(1., (512*512/(crop.width*crop.height))**.5))
        output_size = [max(1,round(side*scale/8)*8) for side in crop.size]
        if min(output_size) < 192 or max(output_size)/min(output_size)>8:
            raise ValueError('选区过窄，无法稳定生成精修；请扩大上下文范围')
        path = cache / f"generated-input-{request['id']}.png"
        preview(crop,1280).save(path,compress_level=3)
        current_crop_assets = {path}
        assets.append(path)
        return {'path':str(path),'box':box,'canvas_size':list(source.image.size),
                'output_size':output_size,'proposed':proposed}

    @register("photo_candidate")
    def _photo_candidate(request):
        nonlocal current_crop_assets
        from .photo_strategy import render_candidate
        if request.get('expected_sha256') != source.digest:
            raise ValueError('照片已变化，成片未应用')
        result = render_candidate(source.image, request['before'], request['proposed'],
                                  request.get('faces', []), cache, request['id'], request.get('soften', False))
        current_crop_assets = {Path(item['path']) for item in result['images']}
        assets.extend(current_crop_assets)
        return result

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
            keep = {current_original, current_overlay} - {None}
            if previous_result:
                keep.add(Path(previous_result['preview']))
            _prune_assets(assets, keep | current_crop_assets)
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
