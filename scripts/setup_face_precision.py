"""Explicit verified installation; no model download from the application UI.

Supply a verified ONNX or an isolated Python with torch==2.6.0+cpu and
onnx==1.17.0 to convert the official MIT release. Runtime needs no Torch.
"""
import argparse
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from iphoto.segmentation.face_precision import MODEL_DIR,NAME,SIZE,DIGEST,verified_path
from iphoto.storage import atomic_output

SOURCE_NAME = 'face_parsing.farl.lapa.main_ema_136500_jit191.pt'
SOURCE_SIZE = 646604126
SOURCE_DIGEST = 'f5a874906795ef89fadd7cf3b5b218ed8550fa9dbb383b7c0f95726c3a352914'
URL = 'https://github.com/FacePerceiver/facer/releases/download/models-v1/' + SOURCE_NAME


def check(path,size,digest):
    hasher=sha256();count=0
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            count+=len(chunk)
            if count>size:raise ValueError('Model size exceeded; not installed')
            hasher.update(chunk)
    if count!=size or hasher.hexdigest()!=digest:
        raise ValueError('Model size or SHA256 mismatch; not installed')


def install(path):
    check(path,SIZE,DIGEST)
    MODEL_DIR.mkdir(parents=True,exist_ok=True)
    with path.open('rb') as source,atomic_output(MODEL_DIR/NAME) as output:
        shutil.copyfileobj(source,output,1024*1024)
    verified_path()
    print(f'Verified {NAME}',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--onnx',type=Path,help='Already exported verified ONNX')
    group.add_argument('--python',type=Path,help='Isolated Torch export Python')
    parser.add_argument('--source',type=Path,help='Previously downloaded official JIT; avoids another download')
    args=parser.parse_args()
    if (MODEL_DIR/NAME).exists():
        verified_path();print(f'Verified existing {NAME}');return
    if args.onnx:
        install(args.onnx);return
    with tempfile.TemporaryDirectory(prefix='iphoto-face-precision-') as temporary:
        directory=Path(temporary);source=args.source or directory/SOURCE_NAME
        if args.source is None:
            print(f'Downloading official LaPa ({SOURCE_SIZE/1024/1024:.1f} MiB)',flush=True)
            with urlopen(Request(URL,headers={'User-Agent':'iPhoto-model-setup'}),timeout=60) as response,source.open('wb') as output:
                count=0
                while chunk:=response.read(1024*1024):
                    count+=len(chunk)
                    if count>SOURCE_SIZE:raise ValueError('Source model size exceeded')
                    output.write(chunk)
        check(source,SOURCE_SIZE,SOURCE_DIGEST)
        output=directory/NAME
        subprocess.run([str(args.python),str(ROOT/'scripts/export_face_precision.py'),'--source',str(source),'--output',str(output)],check=True)
        install(output)


if __name__=='__main__':
    main()
