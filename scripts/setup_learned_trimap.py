"""Install two audited local ONNX exports; never load executable model plugins."""
import argparse
from hashlib import sha256
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from iphoto.matting.learned_models import FILES, MODEL_DIR, verified_path
from iphoto.storage import atomic_output


def install(directory):
    paths = {kind: verified_path(directory, kind) for kind in FILES}
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for kind, source in paths.items():
        name, size, expected = FILES[kind]
        target = MODEL_DIR / name
        if source.resolve() == target.resolve(): continue
        with source.open('rb') as stream, atomic_output(target, overwrite=True) as output:
            digest, count = sha256(), 0
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                count += len(block)
                if count > size: raise ValueError('区域模型大小不符，未安装')
                digest.update(block); output.write(block)
            if count != size or digest.hexdigest() != expected:
                raise ValueError('区域模型在复制期间变化，未安装')
    return [verified_path(MODEL_DIR, kind) for kind in FILES]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-directory', type=Path, required=True)
    print('Verified ' + ', '.join(str(p) for p in install(parser.parse_args().from_directory)))
