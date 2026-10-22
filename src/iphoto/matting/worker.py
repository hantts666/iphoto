"""Isolated alpha matting, with bounded reuse for one manual channel dialog."""

import json
from pathlib import Path
import sys

from ..document import validate_mask, raster_mask
from ..engine import load_source
from .service import refine_alpha


def channel_session(request):
    token = request.get('channel_session')
    if token is None:
        return None
    if (not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef' for c in token)
            or request.get('method')!='channel' or type(request.get('preview_only')) is not bool
            or token!=request.get('channel_token')):
        raise ValueError('通道计算会话无效，原范围保留')
    reference=request.get('content_reference')
    content=reference.get('sha256') if isinstance(reference,dict) else None
    return token, request.get('source_path'), request.get('source_sha'),content


def execute(request, *, session=None, solver=None):
    try:
        if request.get("op") != "matte":
            raise ValueError("未知边缘细化操作")
        key = channel_session(request)
        reference=request.get('content_reference')
        content_id=reference.get('sha256') if isinstance(reference,dict) else None
        if session is not None and (request.get('method')!='channel'
                or (request.get('channel_token'),request.get('source_path'),request.get('source_sha'),content_id)!=session
                or key is not None and key!=session):
            raise ValueError('通道计算会话已变化，原范围保留')
        method = request.get("method", "classic")
        if method not in ("classic", "neural", "channel", "correction", "hair"):
            raise ValueError("未知边缘细化方法")
        if "stroke" in request and method not in ('neural','hair'):
            raise ValueError("局部透明细化需要 AI 方法")
        source = load_source(Path(request["source_path"]))
        if source.digest != request["source_sha"]:
            raise ValueError("源照片已变化，原选区保持不变")
        from ..analysis_content import resolve
        content_reference=request.get('content_reference')
        content=resolve(source,content_reference)
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
                    mask,quality=refine_local(content,mask,request['stroke'],points=request.get('points',[]),progress=channel_progress)
                else:
                    mask,quality=refine_edges(content,mask,boxes=request.get('review_boxes'),progress=channel_progress)
            elif method=='correction':
                from .correction import correct
                learned = None
                if request.get('hair',False):
                    from .learned_models import available as learned_available
                    if learned_available():
                        from .learned import LearnedTrimap
                        learned = LearnedTrimap()
                mask,quality=correct(content,mask,request['corrections'],request['review_boxes'],progress=channel_progress,
                                     hair=request.get('hair',False),context_points=request.get('context_points',False),
                                     source_point_proposal=request.get('source_point_proposal'),learned=learned)
            else:
                mask, quality = estimate(content, mask, request['channel_options'], progress=channel_progress,
                                         native_candidate=native_candidate,neural=solver)
                if solver is not None and request['channel_options'].get('ai'):
                    quality={**quality,'reused_alpha':solver.reused,'computed_tiles':solver.computed_tiles}
        elif method == "neural":
            from .neural import refine

            def progress(tile=None, tiles=None):
                phase = {"phase": "prepare"} if tile is None else {"phase": "details", "tile": tile, "tiles": tiles}
                print(json.dumps({"id": request["id"], "op": "matte", "generation": request["generation"],
                                  "progress": phase}, ensure_ascii=False), flush=True)

            progress()
            if "stroke" in request:
                from .local import refine as refine_local

                mask, quality = refine_local(content, mask, request["stroke"],
                                             points=request.get("points", []), progress=progress)
            else:
                mask, quality = refine(content, mask, request["radius"],
                                       points=request.get("points", []), progress=progress)
        else:
            mask, quality = refine_alpha(content, mask, request["radius"])
        if content_reference is not None:
            quality={**quality,'content_sha256':content_reference['sha256']}
            if native_candidate:
                native_candidate['quality']['content_sha256']=content_reference['sha256']
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
    return response


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    session, solver = None, None
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 24 * 1024 * 1024:
                raise ValueError('边缘细化请求过大')
            parsed = json.loads(line)
            if not isinstance(parsed,dict):
                raise ValueError('边缘细化请求无效')
            request = parsed
            key = channel_session(request)
            if key is not None and solver is None:
                from .session import ChannelSolver
                solver = ChannelSolver()
            response = execute(request,session=session,solver=solver)
        except Exception as exc:
            response={'id':request.get('id',-1),'op':'matte','generation':request.get('generation',0),
                      'ok':False,'error':str(exc)[:1000]}
            key = None
        keep = bool(response.get('ok') and key is not None)
        if keep:
            response['channel_session'] = key[0]
            session = key
        print(json.dumps(response,ensure_ascii=False),flush=True)
        if not keep:
            return
        # Keep only the model and one bounded alpha. Photo/guide/request
        # buffers must not remain live while waiting for another user action.
        from ..document import RASTER_CACHE
        from ..masks import clear_decode_cache
        RASTER_CACHE.clear()
        clear_decode_cache()
        del request,parsed,response,line


if __name__ == "__main__":
    main()
