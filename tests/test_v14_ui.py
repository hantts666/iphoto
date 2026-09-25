from copy import deepcopy
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from iphoto.document import raster_mask
from iphoto.workspace import Editor, ROOT
from test_ai import wait_for, mock_api, configure
from test_editor import settled
from test_v14 import catalog, target_response

LIVE = []


@pytest.mark.parametrize("size", [(1440, 930), (1080, 700)])
def test_object_hover_click_combine_and_nested_group_controls(
    qt_app, ai_store, tmp_path, size
):
    from iphoto.segmentation.models import available
    if not available(): pytest.skip("Optional EfficientSAM weights not installed")
    for font in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        p = Path("C:/Windows/Fonts") / font
        if p.exists():
            QFontDatabase.addApplicationFont(str(p))
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.resize(*size)
    LIVE.append((engine, editor, window))

    def find(name):
        item = window.findChild(QObject, name)
        assert item is not None, name
        return item

    def click(name):
        item = find(name)
        assert item.property("enabled") and item.property("visible"), name
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        assert 0 < point.x() < size[0] and 0 < point.y() < size[1], (name, point)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(90)

    def idle():
        return not editor.busy and settled(editor) and window.property("previewReady")

    try:
        image = Image.new("RGB", (600, 420), (44, 75, 102))
        draw = ImageDraw.Draw(image)
        draw.rectangle((60, 42, 240, 294), fill=(230, 151, 65))
        draw.rectangle((360, 42, 540, 294), fill=(94, 175, 136))
        path = tmp_path / "two-objects.png"
        image.save(path)
        editor.openImage(str(path))
        wait_for(lambda: editor.hasImage and idle())
        editor._scene.set(catalog())
        editor.changed.emit()
        before = deepcopy(editor._layers)
        click("tool_object")
        assert not editor.hasSelectionDraft
        mouse = find("selectionMouse")

        def point(x, y):
            return mouse.mapToScene(
                QPointF(mouse.width() * x, mouse.height() * y)
            ).toPoint()

        QTest.mouseMove(window, point(0.2, 0.3), 50)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point(0.2, 0.3))
        wait_for(lambda: idle() and window.property("selectionPreviewReady"), seconds=60)
        assert (
            editor._layers == before
            and raster_mask(editor._candidate, (200, 140)).getpixel((40, 40)) == 255
        )
        QTest.mouseClick(window, Qt.LeftButton, Qt.ShiftModifier, point(0.8, 0.3))
        wait_for(idle)
        assert raster_mask(editor._candidate, (200, 140)).getpixel((150, 40)) == 255
        QTest.mouseClick(window, Qt.LeftButton, Qt.AltModifier, point(0.2, 0.3))
        wait_for(idle)
        assert raster_mask(editor._candidate, (200, 140)).getpixel((40, 40)) == 0
        editor.clearObjectChecks()
        click("checkCategoryButton")
        assert editor.checkedObjectCount == 2
        click("combineObjectsButton")
        wait_for(idle)
        assert raster_mask(editor._candidate, (200, 140)).getpixel((40, 40)) == 255
        QTest.qWait(400)
        folder = ROOT / "artifacts"
        folder.mkdir(exist_ok=True)
        assert window.grabWindow().save(str(folder / f"v14-objects-mock-{size[0]}.png"))
        click("selectionToLayerButton")
        wait_for(idle)
        assert len(editor._layers) == 2
        click("groupLayerButton")
        wait_for(idle)
        assert editor.activeIsGroup
        click("addLayerButton")
        wait_for(idle)
        assert not editor.activeIsGroup and editor.activeParentId
        click("groupLayerButton")
        wait_for(idle)
        assert max(row["depth"] for row in editor.layers) == 2
        QTest.qWait(200)
        assert window.grabWindow().save(str(folder / f"v14-groups-mock-{size[0]}.png"))
        click("moveOutGroupButton")
        wait_for(idle)
        assert not editor.activeParentId
        # Typing a Photoshop shortcut in a rename field must not create a group.
        click("layerNameInput")
        count = len(editor._layers)
        QTest.keyClick(window, Qt.Key_G, Qt.ControlModifier)
        assert len(editor._layers) == count
        editor.selectLayer(editor._layers[0]["id"])
        click("selectionTab")
        field = find("selectionDescriptionInput")
        field.setProperty("text", "两个方块")
        with mock_api(target_response()) as (url, _):
            configure(editor.ai, url)
            click("aiSelectionButton")
            wait_for(lambda: editor.hasSelectionDraft and idle())
        assert editor.checkedObjectCount == 2
        assert not warnings, warnings
    finally:
        editor.close()
        window.hide()
