"""One-shot original-resolution export outside the interactive edit worker."""

import json
from pathlib import Path
import sys

from .document import render_layers, validate_layers
from .engine import Recipe, export_image, load_source


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    line = sys.stdin.readline()
    if not line:
        return
    request = {}
    try:
        if len(line) > 192 * 1024 * 1024:
            raise ValueError("导出请求过大")
        request = json.loads(line)
        if request.get("op") != "export":
            raise ValueError("未知导出操作")
        source = load_source(Path(request["source_path"]))
        if source.digest != request["source_sha"]:
            raise ValueError("源照片已变化，导出已停止")
        rendered = render_layers(source.image, validate_layers(request["layers"]))
        staged = export_image(
            source,
            Recipe(),
            request["stage_path"],
            rendered,
            jpeg_quality=request["jpeg_quality"],
        )
        response = {
            "id": request["id"], "ok": True,
            "result": {"stage_path": str(staged), "width": source.image.width,
                       "height": source.image.height},
        }
    except Exception as exc:
        response = {
            "id": request.get("id", -1), "ok": False,
            "error": str(exc)[:1000],
        }
    print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
