"""Shared repository and local-file URL resolution."""

from pathlib import Path
from PySide6.QtCore import QUrl

ROOT = Path(__file__).resolve().parents[2]


def path_from_url(url):
    value = QUrl(url)
    return Path(value.toLocalFile()) if value.isLocalFile() else Path(url)
