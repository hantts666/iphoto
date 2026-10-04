"""Reproduce verified exports with Meta source and Microsoft's MIT wrappers."""
import argparse
from hashlib import sha256
from pathlib import Path
import sys
import warnings

from setup_precise_points import check, verify_source, source_manifest, SOURCE_SIZE, SOURCE_DIGEST


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--sam2-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.checkpoint, SOURCE_SIZE, SOURCE_DIGEST)
    verify_source(args.sam2_source)
    import torch
    import onnx
    import onnxruntime
    if (torch.__version__.split("+")[0], onnx.__version__, onnxruntime.__version__) != ("2.10.0", "1.23.1", "1.24.4"):
        raise ValueError("Export requires torch 2.10.0, onnx 1.23.1, onnxruntime 1.24.4")
    wrappers = Path(onnxruntime.__file__).parent / "transformers/models/sam2"
    for name, digest in source_manifest()["wrappers"].items():
        if sha256((wrappers / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Unverified Microsoft wrapper: {name}")
    sys.path.insert(0, str(args.sam2_source.resolve()))
    sys.path.insert(0, str(wrappers))
    from sam2.build_sam import build_sam2
    from image_encoder import SAM2ImageEncoder
    from image_decoder import SAM2ImageDecoder
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    model = build_sam2("configs/sam2.1/sam2.1_hiera_s.yaml", str(args.checkpoint.resolve()),
                       device="cpu", apply_postprocessing=False)
    encoder = SAM2ImageEncoder(model).eval()
    decoder = SAM2ImageDecoder(model, False, False, return_logits=True).eval()
    example = torch.zeros(1, 3, 1024, 1024)
    with torch.inference_mode():
        features = encoder(example)
    args.output.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        print("Export encoder", flush=True)
        torch.onnx.export(encoder, (example,), str(args.output / "sam21-small-encoder.onnx"),
                          opset_version=17, dynamo=False, input_names=["image"],
                          output_names=["features0", "features1", "embedding"], do_constant_folding=True)
        print("Export decoder", flush=True)
        inputs = (*features, torch.zeros(1, 4, 2), torch.tensor([[2, 3, 1, 0]], dtype=torch.int32),
                  torch.zeros(1, 1, 256, 256), torch.ones(1), torch.tensor([222, 279], dtype=torch.int32))
        torch.onnx.export(decoder, inputs, str(args.output / "sam21-small-decoder.onnx"),
                          opset_version=17, dynamo=False,
                          input_names=["features0", "features1", "embedding", "point_coords", "point_labels",
                                       "input_masks", "has_input_masks", "original_image_size"],
                          output_names=["masks", "scores", "low_res_masks"],
                          dynamic_axes={"point_coords": {1: "points"}, "point_labels": {1: "points"},
                                        "masks": {2: "height", 3: "width"}}, do_constant_folding=True)
    from iphoto.segmentation.precise_sam import FILES
    for name, size, digest in FILES.values():
        path = args.output / name
        onnx.checker.check_model(str(path))
        check(path, size, digest)
        print(f"Verified export {name}", flush=True)


if __name__ == "__main__":
    main()
