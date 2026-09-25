import os
import sys
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(3)  # Do not leave crash-report dialogs during test runs.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from iphoto.ai_settings import SettingsStore


class MemoryVault:
    available = True

    def __init__(self): self.values = {}
    def get(self, target): return self.values.get(target, "")
    def set(self, target, key): self.values[target] = key
    def delete(self, target): self.values.pop(target, None)


@pytest.fixture(scope="session")
def qt_app():
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication.instance() or QGuiApplication([])
    return app


@pytest.fixture
def ai_store(tmp_path):
    return SettingsStore(tmp_path / "settings", MemoryVault())


@pytest.fixture
def pixel_protocol_stub(monkeypatch):
    """Explicit bitmap worker stand-in for protocol/transaction tests only.

    Their historical fixtures contain arbitrary polygons on uniform images.
    Real segmentation is covered separately, never by this fixture.
    """
    from iphoto.workspace import Editor
    from iphoto.controllers import pixel_selections
    from iphoto.document import raster_mask
    from iphoto.segmentation.classical import bitmap_mask

    original = Editor._request
    monkeypatch.setattr(pixel_selections, "available", lambda: True)

    def request(self, op, **data):
        if op != "segment":
            return original(self, op, **data)
        items = [{"id": job["id"], "mask": bitmap_mask(raster_mask(job["hint"], (600, 420)), "protocol fixture"), "quality": {}} for job in data["jobs"]]
        pixel_selections.complete(self, {"items": items}, data["context"])

    monkeypatch.setattr(Editor, "_request", request)
