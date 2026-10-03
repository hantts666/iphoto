"""Build the pinned official model using an isolated converter environment.

Production inference needs ONNX Runtime only, not Torch/Transformers.
See models/README.md for exact converter versions and license attribution.
"""
import argparse
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from iphoto.matting.models import REPOSITORY, REVISION, SIZE, DIGEST


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--cache-dir",type=Path,required=True)
    args = parser.parse_args()
    for package,expected in {"torch":"2.10.0", "transformers":"5.18.0", "onnx":"1.23.1"}.items():
        if version(package).split("+")[0] != expected:
            raise ValueError(f"转换器需要 {package}=={expected}")
    import torch
    from transformers import VitMatteForImageMatting

    torch.set_num_threads(4)
    torch.manual_seed(0)
    model = VitMatteForImageMatting.from_pretrained(REPOSITORY,revision=REVISION,
                                                  cache_dir=str(args.cache_dir)).float().eval()
    class Wrapper(torch.nn.Module):
        def __init__(self,network):
            super().__init__()
            self.network = network
        def forward(self,pixels):
            return self.network(pixel_values=pixels).alphas
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary = args.output.with_name(args.output.name+".tmp")
    try:
        torch.onnx.export(Wrapper(model).eval(),torch.zeros(1,4,640,640,dtype=torch.float32),temporary,
                          input_names=["pixel_values"],output_names=["alphas"],opset_version=17,
                          dynamo=False,external_data=False)
        if temporary.stat().st_size != SIZE or sha256(temporary.read_bytes()).hexdigest() != DIGEST:
            raise ValueError("导出结果与验证版本不一致，未安装；请检查转换器版本")
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Verified export {args.output.name}",flush=True)


if __name__ == "__main__":
    main()
