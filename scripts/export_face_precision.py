"""Export the verified author release in an isolated Torch 2.6 CPU runtime."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import torch

from face_precision_network import convert

SOURCE_DIGEST = 'f5a874906795ef89fadd7cf3b5b218ed8550fa9dbb383b7c0f95726c3a352914'


def digest(path):
    hasher = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if torch.__version__ != '2.6.0+cpu':
        raise ValueError('Export requires the isolated torch==2.6.0+cpu runtime')
    if digest(args.source) != SOURCE_DIGEST:
        raise ValueError('Author release SHA256 mismatch; not loaded')
    torch.set_num_threads(4);torch.set_num_interop_threads(1)
    original = torch.jit.load(str(args.source),map_location='cpu').eval()
    eager = convert(original)
    torch.manual_seed(104)
    sample = torch.rand(1,3,448,448)
    with torch.inference_mode():
        reference = original(sample)[0]
        actual = eager(sample)
        error = (reference-actual).abs().max().item()
        changes = (reference.argmax(1)!=actual.argmax(1)).sum().item()
    if error >= .0002 or changes:
        raise ValueError(f'Export translation mismatch: max error={error}, labels={changes}')
    torch.onnx.export(eager,sample,str(args.output),opset_version=17,input_names=['image'],output_names=['logits'],dynamo=False,do_constant_folding=True)
    print(json.dumps({'bytes':args.output.stat().st_size,'sha256':digest(args.output),'max_error':error,'changed_labels':changes}),flush=True)


if __name__ == '__main__':
    main()
