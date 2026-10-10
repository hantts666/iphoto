"""One-shot source-resolution alpha matting outside the main edit worker."""

import json
from pathlib import Path
import sys

from ..document import validate_mask, raster_mask
from ..engine import load_source
from .service import refine_alpha


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    line = sys.stdin.readline()
    if not line:
        return
    request = {}
    try:
        if len(line) > 24 * 1024 * 1024:
            raise ValueError("边缘细化请求过大")
        request = json.loads(line)
        if request.get("op") != "matte":
            raise ValueError("未知边缘细化操作")
        method = request.get("method", "classic")
        if method not in ("classic", "neural", "channel", "correction", "hair"):
            raise ValueError("未知边缘细化方法")
        if "stroke" in request and method not in ('neural','hair'):
            raise ValueError("局部透明细化需要 AI 方法")
        source = load_source(Path(request["source_path"]))
        if source.digest != request["source_sha"]:
            raise ValueError("源照片已变化，原选区保持不变")
        mask = validate_mask(request["mask"])
        native_candidate = {} if request.get('compare_native') is True and method=='channel' else None
        if method in ('channel','correction','hair'):
            from .channels import estimate
            def channel_progress(tile=None, tiles=None, phase='details', **fields):
                print(json.dumps({'id':request['id'], 'op':'matte', 'generation':request['generation'],
                                  'progress':{**({'phase':phase} if tile is None else {'phase':phase,'tile':tile,'tiles':tiles}),**fields}}, ensure_ascii=False), flush=True)
            if method=='hair':
                from .hair import refine_local, refine_edges
                if 'stroke' in request:
                    mask,quality=refine_local(source.image,mask,request['stroke'],points=request.get('points',[]),progress=channel_progress)
                else:
                    mask,quality=refine_edges(source.image,mask,boxes=request.get('review_boxes'),progress=channel_progress)
            elif method=='correction':
                from .correction import correct
                learned = None
                if request.get('hair',False):
                    from .learned_models import available as learned_available
                    if learned_available():
                        from .learned import LearnedTrimap
                        learned = LearnedTrimap()
                mask,quality=correct(source.image,mask,request['corrections'],request['review_boxes'],progress=channel_progress,
                                     hair=request.get('hair',False),context_points=request.get('context_points',False),
                                     source_point_proposal=request.get('source_point_proposal'),learned=learned)
            else:
                mask, quality = estimate(source.image, mask, request['channel_options'], progress=channel_progress,
                                         native_candidate=native_candidate)
        elif method == "neural":
            from .neural import refine

            def progress(tile=None, tiles=None):
                phase = {"phase": "prepare"} if tile is None else {"phase": "details", "tile": tile, "tiles": tiles}
                print(json.dumps({"id": request["id"], "op": "matte", "generation": request["generation"],
                                  "progress": phase}, ensure_ascii=False), flush=True)

            progress()
            if "stroke" in request:
                from .local import refine as refine_local

                mask, quality = refine_local(source.image, mask, request["stroke"],
                                             points=request.get("points", []), progress=progress)
            else:
                mask, quality = refine(source.image, mask, request["radius"],
                                       points=request.get("points", []), progress=progress)
        else:
            mask, quality = refine_alpha(source.image, mask, request["radius"])
        if 'scope_limit' in request:
            from .scope import restrict
            mask=restrict(mask,request['scope_limit'],source.image.size)
            quality={**quality,'scope_limited':True,
                     'partial_pixels':sum(raster_mask(mask,source.image.size).histogram()[1:255])}
            if native_candidate:
                native_candidate['mask']=restrict(native_candidate['mask'],request['scope_limit'],source.image.size)
                native_candidate['quality'].update(scope_limited=True,
                    partial_pixels=sum(raster_mask(native_candidate['mask'],source.image.size).histogram()[1:255]))
        review_boxes = None
        if native_candidate:
            from ..matte_compare import comparison_boxes
            review_boxes=comparison_boxes({'native':native_candidate['mask'],'channel':mask},source.image.size)
        response = {
            "id": request["id"], "op": "matte",
            "generation": request["generation"], "ok": True,
            "result": {"mask": mask, "quality": quality,
                       **({'native_candidate':native_candidate,'review_boxes':review_boxes} if native_candidate else {})},
        }
    except Exception as exc:
        response = {
            "id": request.get("id", -1), "op": "matte",
            "generation": request.get("generation", 0),
            "ok": False, "error": str(exc)[:1000],
        }
    print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
