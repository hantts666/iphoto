"""Publish complete local files atomically without overwriting unknown targets."""

from contextlib import contextmanager
import os
from pathlib import Path
from uuid import uuid4


@contextmanager
def atomic_output(path, overwrite=False):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as file:
            yield file
            file.flush()
            os.fsync(file.fileno())
        if overwrite:
            os.replace(temporary, path)
        elif os.name == "nt":
            os.rename(temporary, path)  # Windows rename fails if destination exists.
        else:
            os.link(temporary, path)  # Atomic and exclusive on POSIX.
    finally:
        temporary.unlink(missing_ok=True)
