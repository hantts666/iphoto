"""Exercise an immediate S-tool click while its cold model warm-up is active."""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, QPointF, QProcess, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iphoto.ai_settings import SettingsStore  # noqa: E402
from iphoto.workspace import Editor  # noqa: E402


def wait(app, condition, seconds=60):
    until = monotonic() + seconds
    while monotonic() < until:
        app.processEvents()
        if condition():
            return
        QTest.qWait(20)
    raise AssertionError("Qt or worker timed out")


def click(window, name, x=0.5, y=0.5):
    item = window.findChild(QObject, name)
    assert item is not None, name
    point = item.mapToScene(QPointF(item.width() * x, item.height() * y)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-cancel", choices=("pixel", "rect", "open"), default="pixel")
    parser.add_argument("--wait-before-cancel", type=float, default=0)
    parser.add_argument("--open-during-warm", action="store_true")
    parser.add_argument("--rect-during-warm", action="store_true")
    parser.add_argument("--cancel-active", choices=("pixel", "rect", "open"))
    parser.add_argument("--cancel-via-escape", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    with tempfile.TemporaryDirectory(prefix="iphoto-cold-pixel-") as temporary:
        folder = Path(temporary)
        previous_cache_root = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(folder / "app-data")
        photo = folder / "photo.jpg"
        image = Image.new("RGB", (4000, 3000), "#315f8e")
        ImageDraw.Draw(image).ellipse((500, 400, 2400, 2600), fill="#e1a349")
        image.save(photo, quality=90)
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
            wait(
                app,
                lambda: editor.hasImage
                and window.property("previewReady")
                and not editor._active
                and not editor._queue
                and not editor._pending_render,
            )
            editor.selection.pickLayer(editor.activeLayerId)
            click(window, "tool_smart")
            assert editor.selection.tool == "smart"
            if args.open_during_warm or args.rect_during_warm:
                QTest.qWait(800)
                assert editor._warm_process.state() == QProcess.Running
            if args.rect_during_warm:
                switched = monotonic()
                editor.selection.chooseTool("rect")
                editor.drawDraft("rect", "replace", [[0.55, 0.1], [0.9, 0.5]], 0.02)
                wait(app, lambda: bool(editor.maskUrl) and not editor._active)
                print(f"rect_during_warm_s={monotonic() - switched:.2f}", flush=True)
                assert editor._warm_process.state() == QProcess.Running
                assert not warnings, warnings
                return
            if args.open_during_warm:
                other = folder / "other.jpg"
                Image.new("RGB", (2000, 1400), "#c27f58").save(other, quality=90)
                switched = monotonic()
                editor.openImage(str(other))
                wait(
                    app,
                    lambda: editor.imageName == other.name
                    and editor._warm_process.state() == QProcess.NotRunning
                    and not editor._active,
                )
                print(f"direct_switch_during_warm_s={monotonic() - switched:.2f}", flush=True)
                assert not warnings, warnings
                return
            if args.cancel_active:
                editor._stop_warm()
                wait(app, lambda: editor._warm_process.state() == QProcess.NotRunning)
                click(window, "selectionMouse", 0.30, 0.5)
                wait(app, lambda: editor._pixel_active is not None and editor._pixel_active["op"] == "segment")
                QTest.qWait(800)
                button = window.findChild(QObject, "cancelAiRequest")
                assert button and button.property("visible")
                cancelled = monotonic()
                if args.cancel_via_escape:
                    QTest.keyClick(window, Qt.Key_Escape)
                else:
                    click(window, "cancelAiRequest")
                if args.cancel_via_escape:
                    QTest.qWait(20)
                    assert not editor.busy, "Escape did not cancel the active selection"
                wait(app, lambda: not editor.busy and not editor._pixel_active)
                print(f"active_cancel_release_s={monotonic() - cancelled:.2f}", flush=True)
                assert not editor.hasSelectionDraft
                if args.cancel_active == "pixel":
                    resumed = monotonic()
                    wait(app, lambda: editor._pixel_process.state() == QProcess.NotRunning)
                    click(window, "selectionMouse", 0.30, 0.5)
                    wait(app, lambda: editor.hasSelectionDraft and not editor._pixel_active and not editor._pixel_queue)
                    print(f"active_cancel_next_pixel_s={monotonic() - resumed:.2f}", flush=True)
                elif args.cancel_active == "rect":
                    resumed = monotonic()
                    editor.selection.chooseTool("rect")
                    editor.drawDraft("rect", "replace", [[0.55, 0.1], [0.9, 0.5]], 0.02)
                    wait(app, lambda: bool(editor.maskUrl) and not editor._active)
                    print(f"active_cancel_rect_s={monotonic() - resumed:.2f}", flush=True)
                else:
                    other = folder / "other.jpg"
                    Image.new("RGB", (2000, 1400), "#c27f58").save(other, quality=90)
                    resumed = monotonic()
                    editor.openImage(str(other))
                    wait(app, lambda: editor.imageName == other.name and not editor._active)
                    print(f"active_cancel_open_s={monotonic() - resumed:.2f}", flush=True)
                assert not warnings, warnings
                return
            started = monotonic()
            click(window, "selectionMouse", 0.30, 0.5)
            QTest.qWait(100)
            queued = [
                request
                for request in editor._pixel_queue
                if request["op"] == "segment" and request.get("priority") != "low"
            ]
            button = window.findChild(QObject, "cancelAiRequest")
            guide = window.findChild(QObject, "selectionGuideRoot")
            print(
                f"queued={len(queued)} warm_state={editor._warm_process.state().name} "
                f"active={editor._pixel_active.get('context', {}).get('purpose') if editor._pixel_active else None} "
                f"task={editor.selection.taskKind} cancellable={editor.selection.taskCancellable} picked={bool(editor.selection.pickedLayerId)} "
                f"guide_visible={guide.property('visible') if guide else None} "
                f"cancel_visible={button.property('visible') if button else None} "
                f"bar_visible={button.parentItem().property('visible') if button else None} "
                f"cancel_size={(button.width(), button.height()) if button else None}",
                flush=True,
            )
            assert queued and editor.selection.taskKind == "pixel"
            assert not editor.selection.pickedLayerId
            assert button and button.property("visible")
            screenshot = ROOT / "artifacts/ux-cold-pixel-queued.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            assert window.grabWindow().save(str(screenshot))
            if args.wait_before_cancel:
                QTest.qWait(round(args.wait_before_cancel * 1000))
            cancel_started = monotonic()
            if args.cancel_via_escape:
                QTest.keyClick(window, Qt.Key_Escape)
            else:
                click(window, "cancelAiRequest")
            wait(app, lambda: editor._warm_process.state() == QProcess.NotRunning, seconds=3)
            assert not editor.hasSelectionDraft
            print(f"cancel_release_s={monotonic() - cancel_started:.2f}", flush=True)
            if args.after_cancel == "rect":
                resumed = monotonic()
                editor.selection.chooseTool("rect")
                editor.drawDraft("rect", "replace", [[0.55, 0.1], [0.9, 0.5]], 0.02)
                wait(app, lambda: bool(editor.maskUrl) and not editor._active and not editor._pending_render)
                print(f"rect_preview_s={monotonic() - resumed:.2f}", flush=True)
            elif args.after_cancel == "open":
                other = folder / "other.jpg"
                Image.new("RGB", (2000, 1400), "#c27f58").save(other, quality=90)
                resumed = monotonic()
                editor.openImage(str(other))
                wait(app, lambda: editor.imageName == other.name and not editor._active)
                print(f"switch_photo_s={monotonic() - resumed:.2f}", flush=True)
            else:
                wait(app, lambda: not editor._active and not editor._queue and not editor._pixel_active and not editor._pixel_queue and not editor._pending_render)
                resumed = monotonic()
                click(window, "selectionMouse", 0.30, 0.5)
                wait(app, lambda: editor.hasSelectionDraft and not editor._active and not editor._queue and not editor._pixel_active and not editor._pixel_queue)
                print(f"next_click_s={monotonic() - resumed:.2f} status={editor.status}", flush=True)
            print(
                f"total_flow_s={monotonic() - started:.2f} "
                f"draft={editor.hasSelectionDraft} status={editor.status}",
                flush=True,
            )
            assert not warnings, warnings
        finally:
            editor.close()
            assert editor._warm_process.state() == QProcess.NotRunning
            assert editor._pixel_process.state() == QProcess.NotRunning
            window.close()
            if previous_cache_root is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous_cache_root


if __name__ == "__main__":
    main()
