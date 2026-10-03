"""Explicitly install the verified ONNX export; never called by the UI."""
import argparse
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from iphoto.matting.models import MODEL_DIR, NAME, SIZE, DIGEST, verified_path
from iphoto.storage import atomic_output


def install(source):
    source = Path(source)
    MODEL_DIR.mkdir(parents=True,exist_ok=True)
    target = MODEL_DIR/NAME
    if source.resolve() == target.resolve():
        return verified_path()
    if source.stat().st_size != SIZE:
        raise ValueError("模型大小不符，未安装")
    with source.open("rb") as file, atomic_output(target,overwrite=True) as output:
        hasher,count = sha256(),0
        for chunk in iter(lambda:file.read(1024*1024),b""):
            count += len(chunk)
            if count>SIZE:
                raise ValueError("模型超出指定大小，未安装")
            hasher.update(chunk)
            output.write(chunk)
        if count != SIZE or hasher.hexdigest() != DIGEST:
            raise ValueError("模型 SHA256 不符，未安装")
    return verified_path()


def main():
    parser = argparse.ArgumentParser(description="安装固定版本的 ViTMatte ONNX；生成方式见 models/README.md")
    parser.add_argument("--from-onnx",type=Path,help="scripts/export_matting.py 生成的文件")
    args = parser.parse_args()
    path = install(args.from_onnx) if args.from_onnx else verified_path()
    print(f"Verified {path.name}",flush=True)


if __name__ == "__main__":
    main()
