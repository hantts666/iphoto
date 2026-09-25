"""Explicit optional model setup. App inference never downloads weights."""
from hashlib import md5, sha256
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from iphoto.storage import atomic_output

URL="https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
# Checksum published by rembg/sessions/u2netp.py; HTTPS verifies the source.
EXPECTED_MD5="8e83ca70e441ab06c318d82300c84806"


def main():
    target=ROOT/"models/u2netp.onnx"
    if target.is_file():
        if md5(target.read_bytes()).hexdigest()!=EXPECTED_MD5:
            raise ValueError("已有同名模型与指定版本不同，未覆盖；请先手动整理 models 目录")
        print("Model already verified.",flush=True)
        return
    print("Downloading optional U2Net small model from rembg release...",flush=True)
    with urlopen(Request(URL,headers={"User-Agent":"iPhoto-model-setup/1.3"}),timeout=40) as response:
        data=response.read(10*1024*1024+1)
    if len(data)>10*1024*1024 or md5(data).hexdigest()!=EXPECTED_MD5:
        raise ValueError("模型大小或校验值不符合指定版本，未安装")
    target.parent.mkdir(parents=True,exist_ok=True)
    with atomic_output(target) as output: output.write(data)
    print(f"Verified {len(data)} bytes; SHA256 {sha256(data).hexdigest()}",flush=True)


if __name__=="__main__": main()
