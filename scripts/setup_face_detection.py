"""Explicit atomic install of pinned neural face localization models."""
from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import urlopen,Request
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from iphoto.segmentation import face_detection,retinaface
from iphoto.storage import atomic_output


def install(model):
    model.MODEL_DIR.mkdir(parents=True,exist_ok=True)
    path=model.MODEL_DIR/model.NAME
    if path.exists():
        model.verified_path();print(f"Verified existing {model.NAME}");return
    with urlopen(Request(model.URL,headers={"User-Agent":"iPhoto-model-setup"}),timeout=30) as response,atomic_output(path) as output:
        data=response.read(model.SIZE+1)
        if len(data)!=model.SIZE or sha256(data).hexdigest()!=model.DIGEST:
            raise ValueError("人脸模型大小或 SHA256 不符，未安装")
        output.write(data)
    print(f"Verified {model.NAME}")


def main():
    for model in (face_detection,retinaface):
        install(model)


if __name__ == "__main__":main()
