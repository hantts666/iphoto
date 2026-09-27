"""Measure real QML layer-list refresh work during editor notifications."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from iphoto.ai_settings import SettingsStore
from iphoto.controllers import layers
from iphoto.document import new_layer
from iphoto.workspace import Editor


class EmptyVault:
    available = False

    def get(self, target):
        return ""


def main():
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    with TemporaryDirectory(prefix="iphoto-layer-qa-") as temp:
        editor = Editor(ai_store=SettingsStore(Path(temp), EmptyVault()))
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
        engine.rootContext().setContextProperty("editor", editor)
        engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.resize(1080, 700)
        window.setProperty("chatOpen", False)
        editor.loadDemo()
        deadline = perf_counter() + 30
        while not (editor.hasImage and not editor.rendering and window.property("previewReady")):
            assert perf_counter() < deadline
            QTest.qWait(20)
        original = layers.layerMaskThumbnail
        calls = 0

        def counted(owner, lid):
            nonlocal calls
            calls += 1
            return original(owner, lid)

        layers.layerMaskThumbnail = counted
        try:
            for count in (1, 16, 32):
                if count > 1:
                    editor._layers = [new_layer(f"调整层 {i}", True) for i in range(count)]
                    editor._selected = editor._layers[-1]["id"]
                    editor._load_layer()
                    editor.changed.emit()
                editor.selection.pickLayer(editor.activeLayerId)
                QTest.qWait(100)
                calls = 0
                started = perf_counter()
                for _ in range(100):
                    editor.changed.emit()
                    app.processEvents()
                print(count, "layers:", round((perf_counter() - started) * 10, 2),
                      "ms per notification; thumbnail calls:", calls, flush=True)
                assert calls == 0, "Status-only updates refreshed mask thumbnails"
                calls = 0
                started = perf_counter()
                for index in range(100):
                    editor._layer()["opacity"] = 0.5 if index % 2 else 1.0
                    editor.changed.emit()
                    app.processEvents()
                print(count, "layers:", round((perf_counter() - started) * 10, 2),
                      "ms per opacity row update; thumbnail calls:", calls, flush=True)
                assert calls == 0, "Opacity-only updates refreshed mask thumbnails"
            assert not warnings, warnings
        finally:
            layers.layerMaskThumbnail = original
            editor.close()
            window.hide()


if __name__ == "__main__":
    main()
