"""Run with .venv/Scripts/python.exe run.py (or start-iphoto.cmd)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    if "--worker" in sys.argv:
        from iphoto.worker import main
    elif "--warm" in sys.argv:
        from iphoto.segmentation.warm_worker import main
    elif "--pixel-worker" in sys.argv:
        from iphoto.segmentation.pixel_worker import main

    elif "--detail-worker" in sys.argv:
        from iphoto.detail_worker import main
    elif "--matte-worker" in sys.argv:
        from iphoto.matting.worker import main
    elif "--export-worker" in sys.argv:
        from iphoto.export_worker import main
    else:
        from iphoto.app import main
    main()
