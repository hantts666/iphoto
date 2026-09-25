from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from iphoto.app import Editor, ROOT
from test_ai import mock_api, wait_for

LIVE_QML = []


@pytest.mark.parametrize("size", [(1440, 930), (1080, 700)])
def test_settings_visible_and_complete_cloud_ui_flow(qt_app, ai_store, tmp_path, size):
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(item.toString() for item in warnings))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    LIVE_QML.append((engine, editor, window))
    window.resize(*size)

    def find(name):
        value = window.findChild(QObject, name)
        assert value is not None, name
        return value

    def click(name):
        item = find(name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        assert 0 < point.x() < size[0] and 0 < point.y() < size[1], name + " outside window"
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(80)

    try:
        photo = tmp_path / "photo.png"
        Image.new("RGB", (320, 200), (60, 95, 140)).save(photo)
        editor.openImage(str(photo))
        wait_for(lambda: editor.hasImage and not editor._active and not editor._pending_render)
        QTest.qWait(150)
        click("aiSettingsButton")
        dialog = window.findChild(QObject, "aiSettingsDialog")
        wait_for(lambda: dialog.property("opened"))
        field = find("aiApiKey")
        viewport = find("aiSettingsScroll")
        assert field.mapToScene(QPointF(0, field.height())).y() <= viewport.mapToScene(QPointF(0, viewport.height())).y(), "Key input must be visible without scrolling"
        with mock_api() as (url, requests):
            # Select custom through the actual keyboard interaction (fires activated).
            combo = find("aiProvider")
            combo.forceActiveFocus()
            QTest.keyClick(window, Qt.Key_End)
            QTest.keyClick(window, Qt.Key_Return)
            QTest.qWait(100)
            assert combo.property("currentIndex") == len(editor.ai.providers) - 1
            find("aiBaseUrl").setProperty("text", url)
            find("aiModel").setProperty("text", "test-vision")
            find("aiApiKey").setProperty("text", "sk-ui-test-not-a-real-key")
            assert find("aiApiKey").property("displayText") != "sk-ui-test-not-a-real-key"
            click("testAiConnection")
            wait_for(lambda: not editor.ai.busy)
            assert "连接成功" in editor.ai.message
            click("saveAiSettings")
            wait_for(lambda: not dialog.property("visible"))
            assert editor.ai.ready
            assert ai_store.load().provider == "custom"
            assert find("applyDescriptionButton").property("text").startswith("AI 修图")
            find("descriptionInput").setProperty("text", "提亮暗部，颜色自然")
            click("applyDescriptionButton")
            wait_for(lambda: not editor.busy and editor.parameters["shadows"] == 18)
            assert len(requests) == 2
            click("aiSettingsButton")
            wait_for(lambda: dialog.property("opened"))
            assert find("aiApiKey").property("text") == ""
            assert dialog.property("keyAvailable")
            # The saved credential is recoverable by a freshly constructed controller.
            from iphoto.ai_settings import SettingsStore
            from iphoto.ai import AIController
            restarted = AIController(store=SettingsStore(ai_store.directory, ai_store.vault))
            assert restarted.ready
            restarted.close()
            dialog.close()
        assert not errors, errors
    except Exception:
        import traceback
        traceback.print_exc()
        raise
    finally:
        window.close()
        editor.close()
        qt_app.processEvents()
