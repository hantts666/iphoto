"""Measure source-resolution viewport render cost without the Qt UI."""

import multiprocessing
import os
import sys
import tempfile
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iphoto.document import new_layer, render_detail_tile  # noqa: E402
from iphoto.engine import Recipe, load_source  # noqa: E402
from qa_zoom_detail import make_photo, memory_mb  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="iphoto-detail-bench-") as temporary:
        photo = Path(temporary) / "detail.png"
        maker = multiprocessing.Process(target=make_photo, args=(photo,))
        maker.start()
        maker.join()
        assert maker.exitcode == 0
        source = load_source(photo)
        global_layer = new_layer("global", True)
        global_layer["recipe"] = Recipe(exposure=.3, warmth=8).to_dict()
        local_layer = new_layer("local")
        local_layer["recipe"] = Recipe(sharpness=35, vibrance=18).to_dict()
        local_layer["mask"].update(
            feather=.01,
            ops=[{"kind": "ellipse", "mode": "add", "points": [[.35, .2], [.7, .8]]}],
        )
        box = (2450, 1650, 3550, 2350)
        before = memory_mb(os.getpid())
        started = perf_counter()
        tile = render_detail_tile(source.image, [global_layer, local_layer], box)
        elapsed = perf_counter() - started
        after = memory_mb(os.getpid())
        print(f"source={source.image.size} tile={tile.size} render_s={elapsed:.2f} "
              f"private_working_mb_before={before} after={after}", flush=True)


if __name__ == "__main__":
    main()
