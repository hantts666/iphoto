"""Image-to-mask pipeline. Call only in the image worker, never the UI thread."""

from time import perf_counter
import numpy as np
from PIL import Image, ImageOps

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from .efficient_sam import backend
from .edges import guided_edge, native_edge
from .corrections import correct_negatives
from .prompts import from_hint, from_points, validate_points


def choose_candidate(logits, scores, coords, labels, hint=None):
    masks = logits > 0
    candidates = []
    for i, mask in enumerate(masks):
        coverage = float(mask.mean())
        if coverage < 0.0001 or coverage > 0.995:
            continue
        checks = []
        for (x, y), label in zip(coords, labels):
            if label in (0, 1):
                checks.append(
                    bool(
                        mask[
                            min(mask.shape[0] - 1, round(float(y))),
                            min(mask.shape[1] - 1, round(float(x))),
                        ]
                    )
                    == bool(label)
                )
        adherence = float(np.mean(checks)) if checks else 1
        if adherence < 1:
            continue
        overlap = 0.0
        if hint is not None:
            reference = np.asarray(hint) > 127
            overlap = float((mask & reference).sum() / max(1, (mask | reference).sum()))
            if overlap < 0.10:
                continue
        rank = (
            float(np.clip(scores[i], 0, 1)) * 0.60 + adherence * 0.25 + overlap * 0.15
        )
        candidates.append((rank, i, adherence, overlap))
    if not candidates:
        raise ValueError(
            "模型没有找到可靠目标，原选区保留；请框住目标或补充保留/排除点"
        )
    rank, index, adherence, overlap = max(candidates)
    if adherence < 1:
        raise ValueError("结果没有满足提示点，原选区保留；请把点放在目标或背景的内部")
    return masks[index], {
        "predicted_iou": round(float(scores[index]), 3),
        "hint_overlap": round(overlap, 3),
        "candidate": index,
        "warnings": ["模型对边界信心较低，请补充提示点"]
        if scores[index] < 0.85
        else [],
    }


def warm(image):
    """Pre-encode the embedding cache so the first real click is fast."""
    proxy = image.convert("RGB")
    proxy.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    return backend().warm(proxy)


def segment_jobs(image, jobs, *, tolerant=False, source=None, progress=None, detail_progress=None):
    """Run bounded pixel jobs with the same validation in either worker."""
    if not isinstance(jobs, list) or len(jobs) > 16:
        raise ValueError("一次最多分割 16 个对象")
    if not jobs:
        warm(image)
        return {"items": []}
    items = []
    for index, job in enumerate(jobs, 1):
        try:
            if job.get("mask_target", "object") in ("face", "face_skin"):
                from .face_skin import segment as face_segment

                options = {"target":"face"} if job.get("mask_target") == "face" else {}
                if job.get("recover_face_anchor") is True:
                    options["recover_anchor"] = True
                if job.get('face_features') is not None:
                    options['features'] = job['face_features']
                if 'face_scope' in job:
                    options['scope'] = job['face_scope']
                if 'face_context' in job:
                    options['context_hint'] = job['face_context']
                if 'face_part' in job:
                    options['part'] = job['face_part']
                mask, quality = face_segment(source or image, job.get("hint"), job.get("points"), crop=job.get("skin_crop"), **options)
            elif job.get("mask_target") == "body_skin":
                from .body_skin import segment as body_segment

                mask, quality = body_segment(source or image, job.get("parts") or [job], progress=progress)
            elif job.get("mask_target", "object") == "object":
                # Background catalog previews stay cheap. Foreground requests
                # must use the native RGB for refinement, not an enlarged proxy.
                native = source is not None and not tolerant
                def report(phase, tile=None, tiles=None):
                    if detail_progress is not None:
                        if tile is None:
                            detail_progress(phase,index,len(jobs))
                        else:
                            detail_progress(phase,index,len(jobs),tile,tiles)
                mask, quality = segment(source if native else image, job.get("hint"), job.get("points"),
                                        progress=report, model_image=image, native_detail=native)
                quality["model"] = "EfficientSAM-S"
                quality["resolution"] = "source" if native else "preview"
            else:
                raise ValueError("未知分区目标类型，照片未改变")
        except ValueError:
            if not tolerant:
                raise
            continue
        items.append({"id": job["id"], "mask": mask, "quality": quality})
    return {"items": items}


def segment(image, hint=None, points=None, *, engine=None, soften=True, progress=None, model_image=None, native_detail=True):
    started = perf_counter()
    points = validate_points(points or [])
    # Resize before converting/copying so a 60 MP source does not allocate
    # another full-size RGB image just to encode a 1600 px embedding.
    encoding_image = model_image if model_image is not None else image
    proxy = (ImageOps.contain(encoding_image, (1600, 1600), Image.Resampling.LANCZOS)
             if max(encoding_image.size) > 1600 else encoding_image)
    if proxy.mode != "RGB":
        proxy = proxy.convert("RGB")
    guide = raster_mask(validate_mask(hint), proxy.size) if hint is not None else None
    coords, labels = (
        from_hint(guide, points)
        if guide is not None
        else from_points(points, proxy.size)
    )
    if progress is not None:
        progress("segment")
    logits, scores, timing = (engine or backend()).predict(proxy, coords, labels)
    semantic_logits = logits
    corrected = {}
    if 0 in labels:
        logits = logits.copy()
        for i in range(len(logits)):
            hard, count = correct_negatives(proxy, logits[i] > 0, coords, labels)
            if count:
                logits[i][~hard] = np.minimum(logits[i][~hard], -1)
                corrected[i] = count
    hard, quality = choose_candidate(logits, scores, coords, labels, guide)
    if quality["candidate"] in corrected:
        quality["local_negative_pixels"] = corrected[quality["candidate"]]
        quality["warnings"].append("已按排除点局部修正，请检查颜色相近的边缘")
    alpha = (
        guided_edge(proxy, hard, radius=max(2, round(min(proxy.size) * 0.004)))
        if soften
        else Image.fromarray(hard.astype(np.uint8) * 255)
    )
    result = empty_mask()
    result.update(bitmap=encode_bitmap(alpha), label="所选区域")
    if hint:
        result["label"] = hint["label"]
    from ..matting.models import available as detail_available
    use_details = native_detail and detail_available()
    detail_done = False
    if use_details:
        if progress is not None:
            progress("details")
        try:
            from .detail import recover
            from .confidence import recover as confidence_recover
            from ..matting.neural import refine as neural_refine

            def tile_progress(tile,tiles):
                if progress is not None:
                    progress("details",tile,tiles)
            # Hint-only AI jobs already have an internal semantic anchor. Use
            # it for validation without adding visible points to user history.
            anchors = points or [[float(x)/max(1, proxy.width-1), float(y)/max(1, proxy.height-1), int(label)]
                                 for (x, y), label in zip(coords, labels) if label in (0, 1)]
            recovered = confidence_recover(image, result, semantic_logits[quality["candidate"]],
                                           anchors, progress=tile_progress)
            if recovered is None:
                recovered = recover(image, result, points,progress=tile_progress)
            if recovered is not None:
                result, matte_quality = recovered
                quality["detail_recovery"] = matte_quality
            else:
                result, matte_quality = neural_refine(image, result,
                                                     radius=min(64,max(8,round(8*max(image.size)/1600))),progress=tile_progress)
            quality["original_matting"] = matte_quality
            quality["warnings"].extend(matte_quality.get("warnings", []))
            detail_done = True
        except (ValueError, ImportError) as exc:
            quality["detail_fallback"] = str(exc)[:500]
            quality["warnings"].append("细节模型未能稳定细化，保留轮廓并使用原图边缘；请检查细枝、发丝和孔洞")
    if image.size != proxy.size and not detail_done:
        from ..matting.service import refine_alpha

        if progress is not None:
            progress("edges")
        radius = min(64, max(8, round(8 * max(image.size) / 1600)))
        try:
            result, matte_quality = refine_alpha(image, result, radius=radius)
            quality["original_matting"] = matte_quality
        except (ValueError, ImportError) as exc:
            # Automatic object selection remains usable on dense boundaries.
            # Explicit user-requested matting retains its strict failure path.
            if progress is not None:
                progress("local_edges")
            result = native_edge(image, result, min(12, radius))
            quality["original_edges"] = {"backend": "RGB-guided", "reason": str(exc)[:500]}
            quality["warnings"].append("透明边缘未能稳定估计，已使用原图颜色贴边；请检查细枝、发丝和孔洞")
    quality.update(
        timing,
        coverage=quality.get("original_matting",{}).get("coverage",round(float((np.asarray(alpha) > 0).mean()) * 100, 1)),
        elapsed_ms=round((perf_counter() - started) * 1000, 1),
        mask_size=[result["bitmap"]["width"], result["bitmap"]["height"]],
    )
    return result, quality
