"""Explicit atomic install of the pinned official YuNet model."""
from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import urlopen,Request
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from iphoto.segmentation.face_detection import MODEL_DIR,NAME,SIZE,DIGEST,URL,verified_path
from iphoto.storage import atomic_output


def main():
    MODEL_DIR.mkdir(parents=True,exist_ok=True)
    path=MODEL_DIR/NAME
    if path.exists():
        verified_path();print(f"Verified existing {NAME}");return
    with urlopen(Request(URL,headers={"User-Agent":"iPhoto-model-setup"}),timeout=30) as response,atomic_output(path) as output:
        data=response.read(SIZE+1)
        if len(data)!=SIZE or sha256(data).hexdigest()!=DIGEST:
            raise ValueError("人脸模型大小或 SHA256 不符，未安装")
        output.write(data)
    print(f"Verified {NAME}")


if __name__ == "__main__":main()
