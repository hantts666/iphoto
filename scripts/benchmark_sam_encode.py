"""Measure first EfficientSAM encoder run on the bundled photograph."""

import argparse
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iphoto.segmentation.models import verified_path  # noqa: E402
from iphoto.segmentation.runtime import prepare_runtime  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("s", "ti"), default="s")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--edge", type=int, default=1600)
    args = parser.parse_args()
    if not 1 <= args.threads <= 32 or not 512 <= args.edge <= 1600:
        parser.error("threads must be 1-32 and edge must be 512-1600")
    prepare_runtime()
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.log_severity_level = 3
    with Image.open(ROOT / "assets/lake.jpg") as opened:
        image = opened.convert("RGB")
    image.thumbnail((args.edge, args.edge), Image.Resampling.LANCZOS)
    started = perf_counter()
    array = np.asarray(image, dtype=np.uint8)
    batched = (
        np.ascontiguousarray(array.transpose(2, 0, 1)[None], dtype=np.float32) / 255
    )
    prepare_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    session = ort.InferenceSession(
        str(verified_path(f"{args.variant}_encoder")),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    init_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    (embedding,) = session.run(None, {"batched_images": batched})
    encode_ms = (perf_counter() - started) * 1000
    print(
        f"variant={args.variant} threads={args.threads} edge={max(image.size)} "
        f"prepare_ms={prepare_ms:.0f} init_ms={init_ms:.0f} "
        f"encode_ms={encode_ms:.0f} embedding_mib={embedding.nbytes / 2**20:.1f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
