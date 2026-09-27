"""Open a large masked photo in real QML, then switch to another photo."""

import gc
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from iphoto.ai_settings import SettingsStore
from iphoto import masks
from iphoto.workspace import Editor
from qa_zoom_detail import child_memory_mb, memory_mb


def wait(condition, seconds=20):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        QGuiApplication.processEvents()
        if condition():
            return
        QTest.qWait(15)
    raise AssertionError("Qt operation timed out")


def settled(editor):
    return not editor._active and not editor._queue and not editor._pending_render and not editor._timer.isActive()


def main():
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    with TemporaryDirectory(prefix="iphoto-mask-session-") as folder_name:
        folder = Path(folder_name)
        first, second = folder / "large.png", folder / "small.png"
        Image.new("RGB", (6000, 4000), "#6287a5").save(first)
        Image.new("RGB", (300, 200), "#b9695a").save(second)
        editor = Editor(ai_store=SettingsStore(folder / "settings"))
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
        engine.rootContext().setContextProperty("editor", editor)
        engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        try:
            editor.openImage(str(first))
            wait(lambda: editor._path == str(first) and settled(editor))
            bitmap = masks.encode_bitmap(Image.new("L", (6000, 4000), 128),
                                         sampling="alpha", preserve_resolution=True)
            gc.collect()
            editor._layer()["mask"]["bitmap"] = bitmap
            assert editor.layerMaskThumbnail(editor.activeLayerId)
            editor._schedule_render()
            wait(lambda: settled(editor))
            print("before_switch ui_private_working_mb", memory_mb(os.getpid()),
                  "decoded_cache_mb", round(masks._DECODE_CACHE_BYTES / 1024**2, 1),
                  "worker", child_memory_mb(editor.process.processId()), flush=True)
            editor.openImage(str(second))
            wait(lambda: editor._path == str(second) and settled(editor))
            QTest.qWait(100)
            gc.collect()
            print("after_switch ui_private_working_mb", memory_mb(os.getpid()),
                  "decoded_cache_mb", round(masks._DECODE_CACHE_BYTES / 1024**2, 1),
                  "worker", child_memory_mb(editor.process.processId()), flush=True)
            assert masks._DECODE_CACHE_BYTES == 0
            assert not warnings, warnings
        finally:
            window.close()
            editor.close()


if __name__ == "__main__":
    main()
