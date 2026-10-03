"""Run with .venv/Scripts/python.exe run.py (or start-iphoto.cmd)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    from iphoto.process_runtime import load_main

    load_main(sys.argv)()
