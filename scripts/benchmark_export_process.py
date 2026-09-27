"""Sample one real 60MP export's subprocess memory on Windows."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image
from PySide6.QtCore import QProcess
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from iphoto.ai_settings import SettingsStore
from iphoto.workspace import Editor
from qa_zoom_detail import child_memory_mb, memory_mb


class EmptyVault:
    available = False

    def get(self, target):
        return ""


def wait(condition, seconds=60):
    end = monotonic() + seconds
    while monotonic() < end:
        QTest.qWait(20)
        if condition():
            return
    raise AssertionError("export benchmark timed out")


def settled(editor):
    return (not editor._active and not editor._queue and not editor._pending_render
            and not editor._timer.isActive())


def main():
    app = QGuiApplication([])
    with TemporaryDirectory(prefix="iphoto-export-benchmark-") as temp:
        folder = Path(temp)
        source = folder / "60mp.png"
        picture = Image.new("RGB", (10000, 6000), (55, 75, 110))
        picture.save(source)
        picture.close()
        editor = Editor(ai_store=SettingsStore(folder / "settings", EmptyVault()))
        try:
            editor.openImage(str(source))
            wait(lambda: editor.hasImage and settled(editor) and editor._width == 10000)
            editor.setParameter("exposure", -0.8)
            editor.finishGesture()
            wait(lambda: settled(editor))
            destination = folder / "export.png"
            main_children = child_memory_mb(editor.process.processId())
            started = monotonic()
            assert editor.exportImage(str(destination))
            wait(lambda: editor._export_process.state() == QProcess.Running)
            QTest.qWait(120)
            children = child_memory_mb(editor._export_process.processId())
            assert children, "export launcher did not start Python child"
            pid = max(children, key=lambda item: item[1][1])[0]
            samples = []
            done = Event()

            def sample():
                while not done.is_set():
                    value = memory_mb(pid)
                    if value:
                        samples.append(value)
                    done.wait(0.025)

            sampler = Thread(target=sample, daemon=True)
            sampler.start()
            try:
                wait(lambda: editor._export_request is None and destination.exists(), 90)
            finally:
                done.set()
                sampler.join(timeout=2)
            with Image.open(destination) as result:
                assert result.size == (10000, 6000)
            assert samples
            print(f"export_s={monotonic()-started:.2f} "
                  f"peak_private_mb={max(value[0] for value in samples):.1f} "
                  f"peak_working_mb={max(value[1] for value in samples):.1f} "
                  f"main_worker_before_mb={main_children} "
                  f"ui_mb={memory_mb(os.getpid())} samples={len(samples)}")
        finally:
            editor.close()
    del app


if __name__ == "__main__":
    main()
