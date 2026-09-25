"""Explicit installation of verified model data; never run automatically by UI."""

import argparse
from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from iphoto.segmentation.models import BASE_URL, FILES, MODEL_DIR, verified_path
from iphoto.storage import atomic_output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("s", "ti", "all"), default="s")
    args = parser.parse_args()
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for variant in ("s", "ti") if args.variant == "all" else (args.variant,):
        for part in ("encoder", "decoder"):
            key = f"{variant}_{part}"
            name, size, digest = FILES[key]
            path = MODEL_DIR / name
            if path.exists():
                verified_path(key)
                print(f"Verified existing {name}", flush=True)
                continue
            print(f"Downloading {name} ({size / 1024 / 1024:.1f} MiB)", flush=True)
            request = Request(
                f"{BASE_URL}/{name}", headers={"User-Agent": "iPhoto-model-setup/1.5"}
            )
            with (
                urlopen(request, timeout=60) as response,
                atomic_output(path) as output,
            ):
                hasher = sha256()
                count = 0
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > size:
                        raise ValueError("下载超出指定模型大小")
                    hasher.update(chunk)
                    output.write(chunk)
                if count != size or hasher.hexdigest() != digest:
                    raise ValueError("模型大小或 SHA256 不符，未安装")
            print(f"Verified {name}", flush=True)


if __name__ == "__main__":
    main()
