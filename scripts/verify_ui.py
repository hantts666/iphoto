"""Exercise real QML controls and save a screenshot of the running application."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from pathlib import Path
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from iphoto.app import Editor
from iphoto.ai_settings import AISettings, SettingsStore

QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
for name in ("msyh.ttc", "msyhbd.ttc", "georgia.ttf", "consola.ttf"):
    if (fonts / name).exists(): QFontDatabase.addApplicationFont(str(fonts / name))
app.setFont(QFont("Microsoft YaHei", 10))
settings_dir = tempfile.TemporaryDirectory(prefix="iphoto-ui-test-")
store = SettingsStore(settings_dir.name)
store.save(AISettings(enabled=False), "")
editor = Editor(ai_store=store)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("editor", editor)
engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
assert engine.rootObjects(), "QML failed to load"
window = engine.rootObjects()[0]


def wait_for(test, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if test(): return
        QTest.qWait(30)
    raise AssertionError("UI condition timed out")


def idle():
    return editor.hasImage and not editor._active and not editor._pending_render and not editor._timer.isActive()


def find(name):
    def walk(item):
        if item.objectName() == name: return item
        for child in item.childItems():
            found = walk(child)
            if found is not None: return found
        return None
    item = walk(window.contentItem())
    assert item is not None, name
    return item


def click(name):
    item = find(name)
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(60)


try:
    editor.loadDemo()
    wait_for(idle)
    wait_for(lambda: window.property("previewReady"))
    original_exposure = editor.parameters["exposure"]
    find("descriptionInput").setProperty("text", "提亮暗部，压低高光，让色调温暖一点")
    click("applyDescriptionButton")
    wait_for(idle)
    assert editor.parameters["warmth"] == 20
    assert editor.parameters["exposure"] > original_exposure
    click("undoButton")
    wait_for(idle)
    assert editor.parameters["warmth"] == 0
    click("compareButton")
    assert not window.property("compare")
    click("compareButton")
    assert window.property("compare")
    slider = find("parameter_exposure")
    point = slider.mapToScene(QPointF(slider.width() * .62, slider.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(idle)
    assert "exposure" in editor.lockedFields
    editor.undo()
    wait_for(idle)
    click("applyDescriptionButton")
    wait_for(idle)
    wait_for(lambda: window.property("previewReady"))
    QTest.qWait(350)
    target = ROOT / "artifacts/iphoto-preview.png"
    assert window.grabWindow().save(str(target))
    print("UI PASS: description button, undo, compare toggle, slider, latest preview and screenshot")
    print("SCREENSHOT " + str(target))
finally:
    window.close()
    editor.close()
    settings_dir.cleanup()
