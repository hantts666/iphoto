"""Explicit installation of verified face-parsing data. UI never downloads it."""

from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from iphoto.segmentation.face_models import MODEL_DIR, NAME, SIZE, DIGEST, URL, verified_path
from iphoto.storage import atomic_output


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / NAME
    if path.exists():
        verified_path()
        print(f"Verified existing {NAME}")
        return
    print(f"Downloading face-parsing {NAME} ({SIZE / 1024 / 1024:.1f} MiB)", flush=True)
    with urlopen(Request(URL, headers={"User-Agent": "iPhoto-model-setup"}), timeout=60) as response, atomic_output(path) as output:
        hasher, count = sha256(), 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > SIZE:
                raise ValueError("下载超出指定模型大小")
            hasher.update(chunk)
            output.write(chunk)
        if count != SIZE or hasher.hexdigest() != DIGEST:
            raise ValueError("模型大小或 SHA256 不符，未安装")
    print(f"Verified {NAME}", flush=True)


if __name__ == "__main__":
    main()
