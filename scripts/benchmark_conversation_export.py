"""Measure UI stalls while exporting a near-limit conversation from real QML."""

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
from PySide6.QtCore import QPointF, QTimer, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from iphoto.ai_settings import SettingsStore
from iphoto.workspace import Editor


def wait(condition, seconds=12):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        QGuiApplication.processEvents()
        if condition():
            return
        QTest.qWait(10)
    raise AssertionError("QML operation timed out")


def find(root, name):
    stack = [root.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    raise AssertionError(name)


def click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def main():
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    with TemporaryDirectory(prefix="iphoto-chat-export-") as folder_name:
        folder = Path(folder_name)
        photo = folder / "photo.png"
        Image.new("RGB", (300, 200), "#637d92").save(photo)
        editor = Editor(ai_store=SettingsStore(folder / "settings"))
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
        engine.rootContext().setContextProperty("editor", editor)
        engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        try:
            editor.openImage(str(photo))
            wait(lambda: editor._path == str(photo) and not editor.busy)
            window.setProperty("chatOpen", False)
            message = "长对话内容。" * 1000
            editor._conversation = [
                {"id": str(i), "role": "user", "text": message,
                 "time": "2026-09-27", "model": "", "layer_name": "全图调整", "state": ""}
                for i in range(1900)
            ]
            editor._conversation_model.replace(editor._conversation)
            editor.conversationChanged.emit()
            editor.changed.emit()
            window.setProperty("chatOpen", True)
            QTest.qWait(80)
            click(window, find(window, "exportConversationButton"))
            from PySide6.QtCore import QObject
            dialog = window.findChild(QObject, "conversationExportDialog")
            assert dialog and dialog.property("opened")
            target = folder / "conversation.md"
            dialog.setProperty("filePath", str(target))
            ticks = []
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: ticks.append(monotonic()))
            timer.start()
            QTest.qWait(80)
            started = monotonic()
            click(window, find(window, "conversationExportConfirmButton"))
            click_elapsed = monotonic() - started
            wait(lambda: target.exists() and not dialog.property("opened"))
            QTest.qWait(100)
            timer.stop()
            gaps = [b-a for a, b in zip(ticks, ticks[1:])]
            print("messages", len(editor._conversation),
                  "markdown_mb", round(target.stat().st_size / 1024**2, 2),
                  "click_elapsed_s", round(click_elapsed, 3),
                  "largest_ui_tick_gap_s", round(max(gaps, default=0), 3),
                  "qml_warnings", len(warnings), flush=True)
            ticks.clear()
            timer.start()
            QTest.qWait(80)
            started = monotonic()
            editor._message("user", "刚追加的一条")
            append_elapsed = monotonic() - started
            QTest.qWait(100)
            timer.stop()
            gaps = [b-a for a, b in zip(ticks, ticks[1:])]
            print("append_message_elapsed_ms", round(append_elapsed * 1000, 2),
                  "append_largest_ui_tick_gap_ms", round(max(gaps, default=0) * 1000, 2),
                  flush=True)
            assert not warnings
        finally:
            window.close()
            editor.close()
            app.processEvents()


if __name__ == "__main__":
    main()
