"""Measure UI event gaps while autosaving a legal multi-mask project."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PIL import Image
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from iphoto.ai_settings import SettingsStore
from iphoto.document import new_layer, read_project, write_project
from iphoto.masks import encode_bitmap
from iphoto.workspace import Editor
from qa_zoom_detail import memory_mb


def wait(condition, seconds=30):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        QGuiApplication.processEvents()
        if condition():
            return
        QTest.qWait(10)
    raise AssertionError("UI operation timed out")


def settled(editor):
    return not editor._active and not editor._queue and not editor._pending_render and not editor._timer.isActive()


def main():
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    with TemporaryDirectory(prefix="iphoto-large-save-") as folder_name:
        folder = Path(folder_name)
        source = folder / "source.png"
        Image.new("RGB", (6000, 4000), "#758ea0").save(source)
        editor = Editor(ai_store=SettingsStore(folder / "settings"))
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
        engine.rootContext().setContextProperty("editor", editor)
        engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        try:
            editor.openImage(str(source))
            wait(lambda: editor._path == str(source) and settled(editor))
            rng = np.random.default_rng(417)
            layers = [editor._layers[0]]
            for index in range(8):
                pixels = (rng.integers(0, 2, (4000, 6000), dtype=np.uint8) * 255)
                bitmap = encode_bitmap(Image.fromarray(pixels), sampling="alpha",
                                       preserve_resolution=True)
                layer = new_layer(f"细节蒙版 {index+1}")
                layer["mask"]["bitmap"] = bitmap
                layers.append(layer)
                print("mask", index + 1, "png_base64_mb",
                      round(len(bitmap["png"]) / 1024**2, 2), flush=True)
                del pixels, bitmap
            editor._layers = layers
            editor._publish_layer_rows()
            editor.changed.emit()
            wait(lambda: settled(editor))
            ticks = []
            timer = QTimer()
            timer.setInterval(16)
            timer.timeout.connect(lambda: ticks.append(monotonic()))
            timer.start()
            editor._dirty = True
            started = monotonic()
            editor._autosave.start()
            wait(lambda: editor._recovery_path.exists(), seconds=30)
            autosave_elapsed = monotonic() - started
            QTest.qWait(120)
            timer.stop()
            gaps = [b-a for a, b in zip(ticks, ticks[1:])]
            print("autosave_elapsed_s", round(autosave_elapsed, 3),
                  "largest_ui_tick_gap_s", round(max(gaps, default=0), 3),
                  "recovery_mb", round(editor._recovery_path.stat().st_size/1024**2, 2),
                  "ui_private_working_mb", memory_mb(os.getpid()), flush=True)
            project = folder / "saved.iphoto"
            ticks.clear()
            timer.start()
            started = monotonic()
            assert editor.saveProjectAsync(str(project))
            submit_elapsed = monotonic()-started
            wait(lambda: project.exists() and not editor.savingProject)
            save_elapsed = monotonic()-started
            timer.stop()
            gaps = [b-a for a, b in zip(ticks, ticks[1:])]
            print("save_project_submit_s", round(submit_elapsed, 3),
                  "save_project_elapsed_s", round(save_elapsed, 3),
                  "largest_ui_tick_gap_s", round(max(gaps, default=0), 3),
                  "project_mb", round(project.stat().st_size/1024**2, 2),
                  "layers", len(read_project(project)["layers"]), flush=True)
            snapshot = deepcopy(editor._payload())
            threaded_target = folder / "threaded.iphoto"
            ticks.clear()
            timer.start()
            started = monotonic()
            writer = Thread(target=write_project, args=(threaded_target, snapshot), daemon=True)
            writer.start()
            wait(lambda: not writer.is_alive())
            writer.join()
            timer.stop()
            gaps = [b-a for a, b in zip(ticks, ticks[1:])]
            print("threaded_save_s", round(monotonic()-started, 3),
                  "largest_ui_tick_gap_s", round(max(gaps, default=0), 3), flush=True)
            assert not warnings, warnings
        finally:
            window.close()
            editor.close()


if __name__ == "__main__":
    main()
