"""Offline canvas stress run; frame timings include synchronous screenshot readback.

This is a reproducible software-renderer workload, not a display refresh-rate test.
No cloud requests, private images or saved AI credentials are used.
"""

import json
import os
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from iphoto.ai_settings import SettingsStore
from iphoto.workspace import Editor


class EmptyVault:
    available = False

    def get(self, target):
        return ""


def main():
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    with TemporaryDirectory(prefix="iphoto-canvas-qa-") as temp:
        editor = Editor(ai_store=SettingsStore(Path(temp), EmptyVault()))
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(
            lambda items: warnings.extend(i.toString() for i in items)
        )
        engine.rootContext().setContextProperty("editor", editor)
        engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.resize(1440, 930)
        window.setProperty("chatOpen", False)
        editor.loadDemo()
        deadline = perf_counter() + 30
        while not (
            editor.hasImage
            and not editor.rendering
            and editor._active is None
            and editor._pending_render is None
            and not editor._timer.isActive()
            and window.property("previewReady")
        ):
            assert perf_counter() < deadline, "Preview did not become ready"
            QTest.qWait(20)
        QTest.qWait(100)
        before = editor._serial
        nav = editor.viewport
        results = []
        try:
            for zoom in (0.5, 1, 8, 32):
                nav.setZoom(zoom)
                nav.centerOn(0.5, 0.5)
                times = []
                for frame in range(80):
                    start = perf_counter()
                    nav.pan(4 if frame % 20 < 10 else -4, 2 if frame % 16 < 8 else -2)
                    app.processEvents()
                    image = window.grabWindow()
                    assert not image.isNull()
                    elapsed = (perf_counter() - start) * 1000
                    if frame >= 10:
                        times.append(elapsed)
                overlay = window.findChild(QObject, "canvasOverlays")
                results.append(
                    {
                        "zoom_percent": zoom * 100,
                        "frame_and_readback_median_ms": round(median(times), 3),
                        "frame_and_readback_p95_ms": round(
                            sorted(times)[int(len(times) * 0.95)], 3
                        ),
                        "overlay_dip_size": [overlay.width(), overlay.height()],
                    }
                )
            assert editor._serial == before, "Canvas navigation submitted worker jobs"
            assert not warnings, warnings
            report = {
                "backend": "Qt Quick software / offscreen; synchronous frame + readback",
                "window_dip_size": [1440, 930],
                "device_pixel_ratio": window.devicePixelRatio(),
                "source_size": [editor._width, editor._height],
                "measured_frames_per_zoom": 70,
                "extra_worker_requests": editor._serial - before,
                "qml_warnings": warnings,
                "results": results,
            }
            output = ROOT / "artifacts/canvas-v141-benchmark.json"
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            editor.close()
            window.hide()


if __name__ == "__main__":
    main()
