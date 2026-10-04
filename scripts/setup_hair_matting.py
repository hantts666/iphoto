"""Explicit installation of the pinned Apache-2.0 portrait ONNX data."""
import argparse
from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from iphoto.matting.portrait_models import MODEL_DIR,NAME,SIZE,DIGEST,URL,verified_path
from iphoto.storage import atomic_output


def install(source=None):
    MODEL_DIR.mkdir(parents=True,exist_ok=True)
    target = MODEL_DIR/NAME
    if source is not None and Path(source).resolve()==target.resolve(): return verified_path()
    with (Path(source).open('rb') if source is not None else urlopen(URL,timeout=30)) as stream:
        with atomic_output(target,overwrite=True) as output:
            digest,count = sha256(),0
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                count += len(chunk)
                if count>SIZE: raise ValueError('人物发丝模型大小不符，未安装')
                digest.update(chunk);output.write(chunk)
            if count!=SIZE or digest.hexdigest()!=DIGEST:
                raise ValueError('人物发丝模型 SHA256 不符，未安装')
    return verified_path()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-onnx',type=Path)
    args=parser.parse_args()
    print('Verified '+str(install(args.from_onnx)),flush=True)
