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


def publish_staged_file(staged, destination):
    """Publish an already complete file on the same volume without overwriting."""
    staged, destination = Path(staged), Path(destination)
    if not staged.is_file():
        raise ValueError("导出结果不存在，请重试")
    try:
        if os.name == "nt":
            os.rename(staged, destination)
        else:
            os.link(staged, destination)
            staged.unlink()
    except FileExistsError as exc:
        raise ValueError("目标文件已存在，请换一个文件名") from exc
