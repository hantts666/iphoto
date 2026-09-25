from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from .workspace import Editor, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture")
    parser.add_argument("--quit-after", type=int, default=0)
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--ai-settings", action="store_true")
    parser.add_argument("--project", help="Open an existing .iphoto project")
    parser.add_argument(
        "--scene-panel", action="store_true", help="Start with the object browser"
    )
    args = parser.parse_args()
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv[:1])
    # Offscreen Qt on Windows has no system font enumeration; register the same
    # system fonts explicitly so screenshots and the interactive UI stay legible.
    if sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in (
            "msyh.ttc",
            "msyhbd.ttc",
            "segoeui.ttf",
            "georgia.ttf",
            "consola.ttf",
        ):
            if (fonts / name).exists():
                QFontDatabase.addApplicationFont(str(fonts / name))
    app.setFont(QFont("Microsoft YaHei", 10))
    app.setApplicationName("iPhoto")
    app.setOrganizationName("iPhoto")
    app.setWindowIcon(QIcon(str(ROOT / "assets/icon.svg")))
    editor = Editor()
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("editor", editor)
    qml.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    if not qml.rootObjects():
        editor.close()
        sys.exit(1)
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and app.primaryScreen():
        geometry = app.primaryScreen().availableGeometry()
        window = qml.rootObjects()[0]
        window.resize(
            min(1440, geometry.width() - 24), min(930, geometry.height() - 24)
        )
    app.aboutToQuit.connect(editor.close)
    if args.scene_panel:

        def show_objects():
            window = qml.rootObjects()[0]
            window.setProperty("inspectorPage", 2)
            window.setProperty("selectionTool", "object")
            window.setProperty("chatOpen", False)

        editor.imageOpened.connect(lambda: QTimer.singleShot(0, show_objects))
    if args.project:
        QTimer.singleShot(200, lambda: editor.openProject(args.project))
    elif not args.empty:
        QTimer.singleShot(200, editor.loadDemo)
    if args.ai_settings:
        QTimer.singleShot(600, editor.aiSettingsRequested.emit)
    if args.capture:

        def capture():
            window = qml.rootObjects()[0]
            if (
                editor.hasImage
                and not editor.busy
                and not editor.rendering
                and editor._preview != editor._original
                and window.property("previewReady")
            ):
                window.grabWindow().save(args.capture)
                print("CAPTURED " + args.capture, flush=True)
            else:
                QTimer.singleShot(500, capture)

        QTimer.singleShot(2200, capture)
    if args.quit_after:
        QTimer.singleShot(args.quit_after, app.quit)
    sys.exit(app.exec())
