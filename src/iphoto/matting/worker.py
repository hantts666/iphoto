"""One-shot source-resolution alpha matting outside the main edit worker."""

import json
from pathlib import Path
import sys

from ..document import validate_mask
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
                    mask,quality=refine_edges(source.image,mask,progress=channel_progress)
            elif method=='correction':
                from .correction import correct
                mask,quality=correct(source.image,mask,request['corrections'],request['review_boxes'],progress=channel_progress,hair=request.get('hair',False),context_points=request.get('context_points',False))
            else:
                mask, quality = estimate(source.image, mask, request['channel_options'], progress=channel_progress)
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
        response = {
            "id": request["id"], "op": "matte",
            "generation": request["generation"], "ok": True,
            "result": {"mask": mask, "quality": quality},
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
