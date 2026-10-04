"""One-shot original-resolution export outside the interactive edit worker."""

import json
from pathlib import Path
import sys

from .document import render_layers, validate_layers, validate_mask, raster_mask
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
        layers = validate_layers(request["layers"])
        def progress(phase):
            print(json.dumps({"id": request["id"], "type": "progress", "phase": phase}), flush=True)

        progress(1)
        source = load_source(Path(request["source_path"]))
        if source.digest != request["source_sha"]:
            raise ValueError("源照片已变化，导出已停止")
        progress(2)
        output = request.get('output','photo')
        if output not in ('photo','cutout','mask'):
            raise ValueError('未知选区导出格式')
        if output != 'photo' and Path(request['stage_path']).suffix.lower() != '.png':
            raise ValueError('透明选区和黑白蒙版请导出为 PNG')
        alpha = raster_mask(validate_mask(request['mask']),source.image.size) if output != 'photo' else None
        if output == 'mask':
            rendered = alpha
        else:
            rendered = render_layers(source.image, layers)
            if output == 'cutout':
                from .cutout import color_patch, compose_cutout
                patch = color_patch(source.image, layers, validate_mask(request['mask']), alpha)
                rendered = compose_cutout(rendered, alpha, patch)
        progress(3)
        if output == 'mask':
            from .storage import atomic_output
            with atomic_output(request['stage_path']) as file:
                rendered.save(file,format='PNG')
            staged = request['stage_path']
        else:
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
