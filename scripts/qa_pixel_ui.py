"""Real Qt clicks + real local model on the bundled photo; no cloud calls."""

import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from iphoto.ai_settings import SettingsStore
from iphoto.document import write_project
from iphoto.workspace import Editor

LIVE = []


class EmptyVault:
    available = False

    def get(self, _):
        return ""


def main():
    folder = ROOT / "artifacts/selection-v15-ui"
    folder.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    editor = Editor(ai_store=SettingsStore(folder / "settings", EmptyVault()))
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    window = engine.rootObjects()[0]
    LIVE.append((engine, editor, window))
    geometry = app.primaryScreen().availableGeometry()
    window.resize(min(1440, geometry.width() - 24), min(930, geometry.height() - 24))
    window.requestActivate()
    window.setProperty("chatOpen", False)

    def wait(condition, seconds=90):
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            QTest.qWait(25)
            if condition():
                return
        raise RuntimeError("UI deadline: " + editor.status)

    def idle():
        return (
            not editor.busy
            and not editor._active
            and not editor._pending_render
            and not editor._timer.isActive()
        )

    def click(x, y, modifiers=Qt.NoModifier):
        area = window.findChild(QObject, "selectionMouse")
        point = area.mapToScene(QPointF(area.width() * x, area.height() * y)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, modifiers, point)

    try:
        editor.loadDemo()
        wait(lambda: editor.hasImage and idle() and window.property("previewReady"))
        click(0.72, 0.81)
        QTest.keyClick(window, Qt.Key_S)
        assert window.property("selectionTool") == "smart"
        records = []
        for index, (x, y, mod) in enumerate(
            ((0.72, 0.81, Qt.NoModifier), (0.70, 0.94, Qt.AltModifier))
        ):
            start = monotonic()
            click(x, y, mod)
            wait(
                lambda: (
                    idle()
                    and len(editor.pixelPoints) == index + 1
                    and window.property("selectionPreviewReady")
                )
            )
            assert editor._candidate.get("bitmap") and not editor._candidate["ops"]
            QTest.qWait(150)
            assert window.grabWindow().save(str(folder / f"real-click-{index}.png"))
            records.append(
                {
                    "step": index,
                    "seconds": round(monotonic() - start, 2),
                    "points": editor.pixelPoints,
                    "quality": editor.selectionQuality,
                }
            )
        assert len(editor._layers) == 1 and not warnings, warnings
        write_project(folder / "pixel-demo.iphoto", editor._payload(), overwrite=True)
        (folder / "report.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(records, ensure_ascii=False), flush=True)
    finally:
        editor.close()
        window.hide()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
