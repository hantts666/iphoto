"""Exercise source-resolution mask overlay in the actual Qt canvas."""

import argparse
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PIL import Image
from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from qa_zoom_detail import child_memory_mb, wait

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iphoto.ai_settings import SettingsStore  # noqa: E402
from iphoto.workspace import Editor  # noqa: E402


def make_photo(path, size):
    Image.new("RGB", size, (74, 123, 142)).save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=6000)
    parser.add_argument("--height", type=int, default=4000)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    with tempfile.TemporaryDirectory(prefix="iphoto-zoom-mask-") as temporary:
        folder = Path(temporary)
        photo = folder / "mask-photo.png"
        maker = multiprocessing.Process(
            target=make_photo, args=(photo, (args.width, args.height))
        )
        maker.start()
        maker.join()
        assert maker.exitcode == 0 and photo.exists()
        editor = Editor(ai_store=SettingsStore(folder / "settings"))
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
        engine.rootContext().setContextProperty("editor", editor)
        engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.resize(1440, 930)
        window.setProperty("chatOpen", False)
        try:
            editor.openImage(str(photo))
            wait(app, lambda: editor.hasImage and window.property("previewReady")
                 and not editor._active and not editor._pending_render)
            editor.drawDraft("rect", "replace", [[.48, .2], [.8, .8]], .025)
            wait(app, lambda: editor.hasSelectionDraft and bool(editor.maskUrl)
                 and not editor._active and not editor._pending_render)
            assert editor.selection.showMask
            started = monotonic()
            editor.viewport.setZoom(1)
            editor.viewport.centerOn(.5, .5)
            wait(app, lambda: window.property("maskDetailReady"), seconds=30)
            print(f"size={args.width}x{args.height} mask_detail_ready_s={monotonic()-started:.2f}")
            print(f"detail_rect={editor.detailRect} detail_mask={editor.detailMaskUrl != ''}")
            print(f"detail_children={child_memory_mb(editor._detail_process.processId())}")
            if args.width == 6000:
                screenshot = ROOT / "artifacts/ux-zoom-mask-detail.png"
                assert window.grabWindow().save(str(screenshot))
                print(f"screenshot={screenshot}")
            current_detail = editor.detailUrl
            editor.selection.toggleShowMask()
            app.processEvents()
            assert not window.property("maskDetailReady") and editor.detailUrl == current_detail
            editor.selection.toggleShowMask()
            app.processEvents()
            assert window.property("maskDetailReady") and editor.detailUrl == current_detail
            print("mask_toggle_reuses_tile=true")
            before = editor.detailMaskUrl
            editor.viewport.pan(-700, 0)
            app.processEvents()
            assert not window.property("maskDetailReady")
            wait(app, lambda: window.property("maskDetailReady")
                 and editor.detailMaskUrl != before, seconds=30)
            print("pan_reloaded_mask=true")
            editor.viewport.fit()
            wait(app, lambda: not editor.detailMaskUrl
                 and editor._detail_process.state() == QProcess.NotRunning)
            print("fit_released_detail_process=true")
            assert not warnings, warnings
        finally:
            editor.close()
            window.close()


if __name__ == "__main__":
    main()
