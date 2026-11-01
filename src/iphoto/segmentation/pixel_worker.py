"""Persistent pixel worker, isolated so cancellation cannot stall photo I/O."""

import json
import sys
from time import perf_counter
from hashlib import sha256
from pathlib import Path

from PIL import Image


def load_native(request):
    from ..engine import load_source

    loaded = load_source(request["source_path"])
    if loaded.digest != request.get("source_sha"):
        raise ValueError("照片源文件已变化，本次分区未应用")
    from ..analysis_content import resolve
    return resolve(loaded,request.get('content_reference'))


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    image_path = ""
    image = None
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 192 * 1024 * 1024:
                raise ValueError("控制消息过大")
            request = json.loads(line)
            if request.get("op") != "segment":
                raise ValueError("未知像素操作")
            def detail_progress(phase, part, total, tile=None, tiles=None):
                details = {"tile": tile,"tiles": tiles} if tile is not None else {}
                kind = "face" if phase.startswith("face_") else "object"
                print(json.dumps({"id": request["id"], "op": "segment",
                                  "generation": request.get("generation", 0),
                                  "progress": {"kind": kind, "phase": phase, "part": part, "total": total,**details}}), flush=True)

            path = request["proxy_path"]
            proxy_sha=request.get('proxy_sha256')
            if request.get('content_reference') is not None:
                if not isinstance(proxy_sha,str) or sha256(Path(path).read_bytes()).hexdigest()!=proxy_sha:
                    raise ValueError('分析预览已变化，本次分区未应用')
                from ..analysis_content import read_layers
                if sha256(Path(request['source_path']).read_bytes()).hexdigest()!=request.get('source_sha'):
                    raise ValueError('照片源文件已变化，本次分区未应用')
                read_layers(request['content_reference'],digest=request['source_sha'],size=request.get('source_size'))
            image_key=(path,proxy_sha)
            if image_key != image_path:
                with Image.open(path) as opened:
                    loaded = opened.convert("RGB")
                if max(loaded.size) > 1600:
                    raise ValueError("像素代理图超过尺寸上限")
                image, image_path = loaded, image_key
            started = perf_counter()
            composition = request.get("composition")
            source = None
            jobs = request.get("jobs", [])
            if (any(job.get("mask_target") in ("face", "face_skin", "body_skin") for job in jobs)
                    or jobs and request.get("priority") != "low"):
                first_face = next((index for index,job in enumerate(jobs,1)
                                   if job.get("mask_target") in ("face","face_skin") or 'part_restore' in job or 'part_reselect' in job),None)
                if first_face is not None:
                    detail_progress("face_source",first_face,len(jobs))
                source = load_native(request)
            if not jobs and (composition is not None or request.get('skin_effects')):
                # Combining cached masks needs no model import or embedding.
                result = {"items": []}
            else:
                from .service import segment_jobs

                def progress(part, total):
                    print(json.dumps({"id": request["id"], "op": "segment",
                                      "generation": request.get("generation", 0),
                                      "progress": {"kind": "body", "part": part, "total": total}}), flush=True)

                result = segment_jobs(
                    image, jobs, tolerant=request.get("priority") == "low", source=source,
                    progress=progress, detail_progress=detail_progress
                )
            if request.get('skin_effects') is not None:
                from ..skin_effects import prepare_items
                result['items']=prepare_items(result['items'],request['skin_effects'])
            if composition is not None:
                from .object_composition import compose

                result["mask"] = compose(composition, result["items"])
            result["elapsed_ms"] = round((perf_counter() - started) * 1000)
            if request.get('content_reference') is not None:
                result['content_sha256']=request['content_reference']['sha256']
            source = None
            response = {
                "id": request["id"],
                "op": "segment",
                "generation": request.get("generation", 0),
                "ok": True,
                "result": result,
            }
        except Exception as exc:
            response = {
                "id": request.get("id", -1),
                "op": "segment",
                "generation": request.get("generation", 0),
                "ok": False,
                "error": str(exc)[:1000],
            }
        source = None
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
