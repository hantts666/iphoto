"""Prepare the optional colour kernel cache explicitly, outside the application."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from iphoto.color_accel import warm

if __name__ == "__main__":
    warm()
    print("Colour cache ready; no photos or settings changed.")
