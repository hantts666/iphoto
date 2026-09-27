"""Scene list hover must preview the object mask on canvas, row after row."""

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled
from test_v14 import catalog


@pytest.mark.parametrize("size", [(1440, 930)])
def test_row_hover_previews_and_switches_without_race(qt_app, ai_store, tmp_path, size):
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        pytest.skip("hover events require a windowing platform")
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.resize(*size)

    def find(name):
        # Repeater delegates are not reachable via QObject.findChild.
        stack = [window.contentItem()]
        while stack:
            item = stack.pop()
            if item.objectName() == name:
                return item
            stack.extend(item.childItems())
        raise AssertionError(name)

    try:
        image = Image.new("RGB", (600, 420), (44, 75, 102))
        draw = ImageDraw.Draw(image)
        draw.rectangle((60, 42, 240, 294), fill=(230, 151, 65))
        draw.rectangle((360, 42, 540, 294), fill=(94, 175, 136))
        path = tmp_path / "two-objects.png"
        image.save(path)
        editor.openImage(str(path))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor._scene.set(catalog())
        editor.changed.emit()
        QTest.qWait(250)
        ids = [row["id"] for row in editor.sceneObjects][:2]
        assert len(ids) == 2
        row1, row2 = find("sceneRow_" + ids[0]), find("sceneRow_" + ids[1])
        overlays = find("canvasOverlays")

        def center(item):
            return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()

        QTest.mouseMove(window, center(row1), 60)
        QTest.qWait(120)
        assert window.property("sceneHoverId") == ids[0]
        assert overlays.property("hoverPreview") != ""
        # Moving straight to the next row must switch, not clear (activation race).
        QTest.mouseMove(window, center(row2), 60)
        QTest.qWait(120)
        assert window.property("sceneHoverId") == ids[1]
        assert overlays.property("hoverPreview") != ""
        QTest.mouseMove(window, QPointF(200, 400).toPoint(), 60)
        QTest.qWait(120)
        assert window.property("sceneHoverId") == ""
        assert not warnings, warnings
    finally:
        window.close()
        editor.close()
        qt_app.processEvents()


def test_catalog_rows_carry_hover_previews():
    from iphoto.scene import SceneIndex

    index = SceneIndex()
    index.set(catalog())
    rows = index.rows()
    assert rows
    assert all(row["maskPreview"].startswith("data:image/png") for row in rows)
