"""Sample working set by stage of a source-resolution matte on Windows."""

import argparse
import os
from threading import Event, Thread
from time import monotonic

from PIL import Image, ImageDraw

from qa_zoom_detail import memory_mb
from iphoto.document import empty_mask
from iphoto.matting import service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=10000)
    parser.add_argument("--height", type=int, default=6000)
    args = parser.parse_args()
    image = Image.new("RGB", (args.width, args.height), (34, 52, 75))
    ImageDraw.Draw(image).rectangle(
        (args.width//2, 0, args.width-1, args.height-1), fill=(220, 194, 160)
    )
    mask = empty_mask()
    mask["ops"].append({
        "kind": "rect", "mode": "add", "points": [[.25, .15], [.75, .85]],
    })
    stage = ["prepare"]
    samples = []
    done = Event()
    started = monotonic()

    def sample():
        while not done.is_set():
            value = memory_mb(os.getpid())
            if value:
                samples.append((stage[0], monotonic()-started, *value))
            done.wait(.04)

    original = {}
    for name in ("make_trimap", "solve_alpha", "encode_bitmap"):
        function = getattr(service, name)
        original[name] = function

        def timed(*items, _name=name, _function=function, **options):
            stage[0] = _name
            result = _function(*items, **options)
            stage[0] = "after_" + _name
            return result

        setattr(service, name, timed)

    sampler = Thread(target=sample, daemon=True)
    sampler.start()
    try:
        _, quality = service.refine_alpha(image, mask, 8)
    finally:
        done.set()
        sampler.join(timeout=2)
        for name, function in original.items():
            setattr(service, name, function)
    print(f"elapsed_s={monotonic()-started:.2f} tiles={quality['tiles']} "
          f"mask_size={quality['mask_size']}")
    for name in dict.fromkeys(item[0] for item in samples):
        values = [item for item in samples if item[0] == name]
        print(f"{name}: peak_private={max(v[2] for v in values):.1f}MB "
              f"peak_working={max(v[3] for v in values):.1f}MB "
              f"time={values[0][1]:.2f}-{values[-1][1]:.2f}s")


if __name__ == "__main__":
    main()
