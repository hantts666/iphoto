"""Measure full-size mask validation and project round-trip memory on Windows."""

import gc
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw

from iphoto.document import new_layer, read_project, write_project
from iphoto.engine import ENGINE_VERSION
from iphoto import masks
from iphoto.masks import decode_bitmap, encode_bitmap, validate_bitmap
from qa_zoom_detail import memory_mb


def main():
    size = (6000, 4000)
    layers = []
    before = memory_mb(os.getpid())
    print("before_private_working_mb", before, flush=True)
    for index in range(8):
        mask_image = Image.new("L", size)
        draw = ImageDraw.Draw(mask_image)
        draw.ellipse((index * 150, 300, 5000 - index * 100, 3600), fill=255)
        bitmap = encode_bitmap(mask_image, sampling="alpha", preserve_resolution=True)
        layer = new_layer(f"mask-{index}")
        layer["mask"]["bitmap"] = bitmap
        layers.append(layer)
        del draw, mask_image
        start = perf_counter()
        validate_bitmap(bitmap)
        gc.collect()
        print("validated", index + 1, "seconds", round(perf_counter()-start, 3),
              "private_working_mb", memory_mb(os.getpid()),
              "decoded_cache_mb", round(masks._DECODE_CACHE_BYTES/1024**2, 1), flush=True)
    with TemporaryDirectory(prefix="iphoto-mask-project-") as folder:
        target = Path(folder) / "masks.iphoto"
        payload = {
            "schema_version": "1.6", "engine_version": ENGINE_VERSION,
            "source": str(Path(folder) / "photo.png"), "source_sha256": "a" * 64,
            "layers": layers, "active_layer": layers[0]["id"],
            "conversation": [], "selection_draft": None, "region_draft": None,
            "scene_catalog": None,
        }
        start = perf_counter()
        write_project(target, payload)
        print("save_seconds", round(perf_counter()-start, 3), "file_mb", round(target.stat().st_size/1024**2, 3), flush=True)
        masks._VALIDATED.clear()  # Read as an external project in a fresh process.
        start = perf_counter()
        loaded = read_project(target)
        print("load_seconds", round(perf_counter()-start, 3), "layers", len(loaded["layers"]),
              "private_working_mb", memory_mb(os.getpid()), flush=True)
        for layer in loaded["layers"]:
            decode_bitmap(layer["mask"]["bitmap"], size)
        gc.collect()
        print("after_render_private_working_mb", memory_mb(os.getpid()),
              "decoded_cache_mb", round(masks._DECODE_CACHE_BYTES/1024**2, 1), flush=True)


if __name__ == "__main__":
    main()
