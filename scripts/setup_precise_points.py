"""Explicit SAM2.1 Small setup; application runtime remains ONNX-only.

Use --onnx-dir for verified exports, or --python for an isolated export runtime
with torch 2.10.0, onnx 1.23.1 and onnxruntime 1.24.4. Downloads occur only here.
"""
import argparse
from hashlib import sha1, sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from iphoto.segmentation import precise_sam
from iphoto.storage import atomic_output

SOURCE_NAME = "sam2.1_hiera_small.pt"
SOURCE_SIZE = 184416285
SOURCE_DIGEST = "6d1aa6f30de5c92224f8172114de081d104bbd23dd9dc5c58996f0cad5dc4d38"
SOURCE_URL = ("https://huggingface.co/facebook/sam2.1-hiera-small/resolve/"
              "ee5bba1d82bb8749febdf90f45e84b687142ba03/" + SOURCE_NAME)


def check(path, size, digest):
    hasher, count = sha256(), 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            count += len(chunk)
            if count > size:
                raise ValueError("Model size exceeded; not installed")
            hasher.update(chunk)
    if count != size or hasher.hexdigest() != digest:
        raise ValueError("Model size or SHA256 mismatch; not installed")


def install(directory):
    # Validate the entire pair and any existing destination before publishing.
    for kind, (name, size, digest) in precise_sam.FILES.items():
        check(directory / name, size, digest)
        if (precise_sam.MODEL_DIR / name).exists():
            precise_sam.verified_path(kind)
    precise_sam.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for kind, (name, size, digest) in precise_sam.FILES.items():
        target = precise_sam.MODEL_DIR / name
        if not target.exists():
            with (directory / name).open("rb") as source, atomic_output(target) as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
        precise_sam.verified_path(kind)
        print(f"Verified {name}", flush=True)


def source_manifest():
    return json.loads((ROOT / "scripts/precise_points_sources.json").read_text(encoding="utf-8"))


def verify_source(directory):
    for item in source_manifest()["files"]:
        data = (directory / item["path"]).read_bytes()
        digest = sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if len(data) != item["size"] or digest != item["sha"]:
            raise ValueError(f"Unverified SAM2 source: {item['path']}")


def download(url, target, size):
    with urlopen(Request(url, headers={"User-Agent": "iPhoto-model-setup"}), timeout=60) as response, target.open("xb") as output:
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > size:
                raise ValueError("Download size exceeded")
            output.write(chunk)
    if count != size:
        raise ValueError("Incomplete download")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--onnx-dir", type=Path)
    group.add_argument("--python", type=Path, help="Isolated export Python")
    parser.add_argument("--checkpoint", type=Path, help="Previously downloaded official checkpoint")
    parser.add_argument("--sam2-source", type=Path, help="Previously downloaded pinned official source")
    args = parser.parse_args()
    if args.onnx_dir:
        install(args.onnx_dir)
        return
    with tempfile.TemporaryDirectory(prefix="iphoto-precise-points-") as temporary:
        directory = Path(temporary)
        checkpoint = args.checkpoint or directory / SOURCE_NAME
        if args.checkpoint is None:
            print("Downloading official SAM2.1 Small checkpoint", flush=True)
            download(SOURCE_URL, checkpoint, SOURCE_SIZE)
        check(checkpoint, SOURCE_SIZE, SOURCE_DIGEST)
        source = args.sam2_source or directory / "sam2-source"
        if args.sam2_source is None:
            manifest = source_manifest()
            for item in manifest["files"]:
                path = source / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                download(f"https://raw.githubusercontent.com/{manifest['repository']}/{manifest['revision']}/{item['path']}", path, item["size"])
        verify_source(source)
        output = directory / "onnx"
        subprocess.run([str(args.python), str(ROOT / "scripts/export_precise_points.py"),
                        "--checkpoint", str(checkpoint), "--sam2-source", str(source),
                        "--output", str(output)], check=True)
        install(output)


if __name__ == "__main__":
    main()
