"""Time cancelling real transparent-edge refinement in the Qt editor."""

import argparse
import multiprocessing
import os
import sys
import tempfile
from threading import Event, Thread
from pathlib import Path
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iphoto.ai_settings import SettingsStore  # noqa: E402
from iphoto.workspace import Editor  # noqa: E402
from qa_zoom_detail import child_memory_mb, memory_mb  # noqa: E402


def make_large_photo(path, size):
    image = Image.new("RGB", size, (34, 52, 75))
    ImageDraw.Draw(image).rectangle((size[0]//2, 0, size[0]-1, size[1]-1),
                                    fill=(220, 194, 160))
    image.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--large", action="store_true")
    parser.add_argument("--sixty", action="store_true", help="Use a 60MP source")
    parser.add_argument("--finish", action="store_true")
    parser.add_argument("--roundtrip", action="store_true", help="Save, reopen and export the full-size matte")
    args = parser.parse_args()
    if args.roundtrip and (not args.finish or not (args.large or args.sixty)):
        parser.error("--roundtrip requires --finish and --large or --sixty")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    temporary = tempfile.TemporaryDirectory(prefix="iphoto-matte-cancel-")
    folder = Path(temporary.name)
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

    def wait(condition, seconds=20):
        end = monotonic() + seconds
        while monotonic() < end:
            app.processEvents()
            if condition():
                return
            QTest.qWait(15)
        raise AssertionError("timed out: " + editor.status)

    def click(name):
        item = window.findChild(QObject, name)
        assert item is not None, name
        scroll = window.findChild(QObject, "propertyScroll")
        if scroll is not None:
            flick = scroll.property("contentItem")
            point = item.mapToScene(QPointF(0, 0))
            top = scroll.mapToScene(QPointF(0, 0))
            offset = point.y() - top.y() - scroll.height() * .4
            flick.setProperty("contentY", max(0, min(
                flick.property("contentHeight") - flick.property("height"),
                flick.property("contentY") + offset,
            )))
            QTest.qWait(40)
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(30)

    try:
        if args.large or args.sixty:
            photo = folder / "large.png"
            size = (10000, 6000) if args.sixty else (6000, 4000)
            maker = multiprocessing.Process(target=make_large_photo, args=(photo, size))
            maker.start()
            maker.join()
            assert maker.exitcode == 0 and photo.exists()
            editor.openImage(str(photo))
        else:
            editor.loadDemo()
        wait(lambda: editor.hasImage and not editor.busy and not editor._active
             and not editor._pending_render and window.property("previewReady"))
        editor.drawDraft("rect", "replace", [[.25, .15], [.75, .85]], .025)
        wait(lambda: editor.hasSelectionDraft and not editor._active
             and not editor._pending_render and bool(editor.maskUrl))
        before = editor._candidate.copy()
        click("refineMatteButton")
        wait(lambda: editor.matteBusy)
        if args.finish:
            started = monotonic()
            assert editor._active is None  # The main edit worker stays free.
            QTest.qWait(350)
            children = child_memory_mb(editor._matte_process.processId())
            actual_pid = max(children, key=lambda child: child[1][1])[0]
            samples = []
            sampling_done = Event()

            def sample_memory():
                while not sampling_done.is_set():
                    sample = memory_mb(actual_pid)
                    if sample:
                        samples.append(sample)
                    sampling_done.wait(.05)

            sampler = Thread(target=sample_memory, daemon=True)
            sampler.start()
            print(f"memory_mb_ui={memory_mb(os.getpid())} "
                  f"main_children={child_memory_mb(editor.process.processId())} "
                  f"matte_children={children}")
            try:
                wait(lambda: not editor.matteBusy and editor._candidate.get("bitmap", {}).get("sampling") == "alpha",
                     seconds=60)
            finally:
                sampling_done.set()
                sampler.join(timeout=2)
            bitmap = editor._candidate["bitmap"]
            print(f"matte_sampled_peak_private_working_mb="
                  f"({max(sample[0] for sample in samples):.1f}, "
                  f"{max(sample[1] for sample in samples):.1f}) "
                  f"samples={len(samples)}")
            print(f"matte_success_s={monotonic()-started:.2f} "
                  f"bitmap={bitmap['width']}x{bitmap['height']} "
                  f"main_worker_task={editor._active['op'] if editor._active else 'none'}")
            if args.roundtrip:
                assert bitmap["width"] == size[0] and bitmap["height"] == size[1]
                editor.acceptSelection()
                wait(lambda: not editor.busy and not editor._active and not editor._pending_render)
                project = folder / "matte.iphoto"
                saved_at = monotonic()
                editor.saveProject(str(project))
                print(f"project_save_s={monotonic()-saved_at:.2f} "
                      f"project_mb={project.stat().st_size/1024**2:.2f} "
                      f"ui_private_working_mb={memory_mb(os.getpid())}")
                editor.openProject(str(project))
                wait(lambda: editor.projectPath == str(project) and not editor.busy
                     and not editor._active and not editor._pending_render)
                assert editor._layer()["mask"]["bitmap"]["sampling"] == "alpha"
                target = folder / "roundtrip.png"
                exported_at = monotonic()
                assert editor.exportImage(str(target))
                wait(lambda: target.exists() and not editor._export_request, seconds=60)
                with Image.open(target) as output:
                    assert output.size == size
                print(f"project_reopen_export_s={monotonic()-exported_at:.2f} "
                      f"output_mb={target.stat().st_size/1024**2:.2f} "
                      f"ui_private_working_mb={memory_mb(os.getpid())}")
            return
        started = monotonic()
        click("cancelAiRequest")
        cancelled_at = monotonic()
        app.processEvents()
        print(f"cancel_click_s={cancelled_at-started:.2f} "
              f"busy_after_cancel={editor.busy} matte_busy={editor.matteBusy} "
              f"task={editor.selection.taskKind}")
        print(f"candidate_unchanged={editor._candidate == before} status={editor.status}")
        try:
            wait(lambda: not editor.matteBusy and not editor.busy,
                 seconds=5 if args.large or args.sixty else 30)
            print(f"ready_after_cancel_s={monotonic()-cancelled_at:.2f}")
        except AssertionError:
            print("ready_after_cancel_s=>5; worker still owns full-resolution matte")
        assert editor._candidate == before
        retry_at = monotonic()
        editor.refineMatte(8)
        wait(lambda: editor._matte_active is not None, seconds=10)
        editor.cancelMatte()
        wait(lambda: not editor.matteBusy)
        assert editor._candidate == before
        print(f"retry_then_cancel_s={monotonic()-retry_at:.2f}")
        draw_at = monotonic()
        editor.drawDraft("rect", "replace", [[.15, .15], [.35, .45]], .025)
        wait(lambda: not editor._active and not editor._pending_render
             and not editor._timer.isActive() and editor._candidate != before)
        print(f"draw_after_cancel_s={monotonic()-draw_at:.2f}")
        other = folder / "other.png"
        Image.new("RGB", (640, 420), (126, 55, 94)).save(other)
        open_at = monotonic()
        editor.openImage(str(other))
        wait(lambda: editor.imageName == other.name and not editor.busy
             and not editor._active and not editor._pending_render)
        QTest.qWait(100)
        assert editor._candidate is None and editor.imageName == other.name
        print(f"open_after_cancel_s={monotonic()-open_at:.2f}")
        assert not warnings, warnings
    finally:
        editor.close()
        window.close()
        temporary.cleanup()


if __name__ == "__main__":
    main()
