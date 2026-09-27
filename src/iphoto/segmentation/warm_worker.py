"""One-shot encoder process; optional preheat must not block photo operations."""

import sys
from PIL import Image

from .service import warm


def main():
    if len(sys.argv) != 3:
        raise SystemExit("预热参数无效")
    with Image.open(sys.argv[2]) as image:
        warm(image)


if __name__ == "__main__":
    main()
