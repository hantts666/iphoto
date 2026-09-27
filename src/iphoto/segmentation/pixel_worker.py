"""Persistent pixel worker, isolated so cancellation cannot stall photo I/O."""

import json
import sys
from time import perf_counter

from PIL import Image

from .service import segment_jobs


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
            path = request["proxy_path"]
            if path != image_path:
                with Image.open(path) as opened:
                    loaded = opened.convert("RGB")
                if max(loaded.size) > 1600:
                    raise ValueError("像素代理图超过尺寸上限")
                image, image_path = loaded, path
            started = perf_counter()
            result = segment_jobs(
                image, request.get("jobs"), tolerant=request.get("priority") == "low"
            )
            result["elapsed_ms"] = round((perf_counter() - started) * 1000)
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
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
