"""Real Qt controls + CPU alpha solver on the built-in lake photo."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from iphoto.ai_settings import SettingsStore
from iphoto.workspace import Editor
from iphoto.document import write_project
from qa_pixel_ui import EmptyVault

LIVE = []


def main():
    folder = ROOT / "artifacts/alpha-matting"
    folder.mkdir(exist_ok=True, parents=True)
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    LIVE.append(app)
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    e = Editor(ai_store=SettingsStore(folder / "ui-settings", EmptyVault()))
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(
        lambda values: warnings.extend(v.toString() for v in values)
    )
    engine.rootContext().setContextProperty("editor", e)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    w = engine.rootObjects()[0]
    LIVE.append((engine, e, w))
    w.resize(1440, 930)
    w.requestActivate()
    w.setProperty("chatOpen", False)

    def wait(condition, seconds=90):
        end = monotonic() + seconds
        while monotonic() < end:
            QTest.qWait(25)
            if condition():
                return
        raise AssertionError("UI timeout " + e.status)

    def idle():
        return (
            not e.busy
            and not e._active
            and not e._queue
            and not e._pending_render
            and not e._timer.isActive()
        )

    def find(name):
        obj = w.findChild(QObject, name)
        assert obj is not None, name
        return obj

    def visible(name):
        obj = find(name)
        scroll = find("propertyScroll")
        flick = scroll.property("contentItem")
        point = obj.mapToScene(QPointF(0, 0))
        top = scroll.mapToScene(QPointF(0, 0))
        offset = point.y() - top.y() - scroll.height() * 0.4
        flick.setProperty(
            "contentY",
            max(
                0,
                min(
                    flick.property("contentHeight") - flick.property("height"),
                    flick.property("contentY") + offset,
                ),
            ),
        )
        QTest.qWait(80)
        return obj

    def click(name):
        obj = visible(name)
        point = obj.mapToScene(QPointF(obj.width() / 2, obj.height() / 2)).toPoint()
        QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(50)

    try:
        e.loadDemo()
        wait(lambda: e.hasImage and idle() and w.property("previewReady"))
        before = json.loads((folder / "forest-before.json").read_text(encoding="utf-8"))
        e._layer()["mask"] = before
        e.setParameter("exposure", -0.8)
        e.finishGesture()
        wait(idle)
        original = deepcopy(e._layers)
        e.beginSelection("current")
        wait(idle)
        w.setProperty("inspectorPage", 1)
        QTest.qWait(150)
        started = monotonic()
        click("refineMatteButton")
        wait(
            lambda: idle() and e._candidate.get("bitmap", {}).get("sampling") == "alpha"
        )
        assert e._layers == original
        seconds = monotonic() - started
        # Activate the real ComboBox with keyboard events (not an editor call).
        click("maskViewBox")
        QTest.keyClick(w, Qt.Key_Home)
        QTest.keyClick(w, Qt.Key_Down)
        QTest.keyClick(w, Qt.Key_Down)
        QTest.keyClick(w, Qt.Key_Return)
        wait(lambda: idle() and e._mask_view == "adjustment")
        assert not w.property("showMask") and e._layers == original
        visible("refineMatteButton")
        QTest.qWait(200)
        assert w.grabWindow().save(str(folder / "native-ui-1440.png"))
        w.resize(1080, 700)
        QTest.qWait(200)
        visible("refineMatteButton")
        assert w.grabWindow().save(str(folder / "native-ui-1080.png"))
        refined = deepcopy(e._candidate)
        e.undo()
        assert e._candidate == before
        e.redo()
        assert e._candidate == refined
        wait(idle)
        write_project(folder / "matte-demo.iphoto", e._payload(), overwrite=True)
        assert not warnings, warnings
        (folder / "ui-report.json").write_text(
            json.dumps(
                {
                    "seconds": round(seconds, 2),
                    "warnings": warnings,
                    "sizes": [[1440, 930], [1080, 700]],
                    "real_solver": True,
                    "cloud_calls": 0,
                },
                indent=2,
            )
        )
        print("Native QML + real alpha solver passed", round(seconds, 2), flush=True)
    finally:
        e.close()
        w.hide()


if __name__ == "__main__":
    main()
