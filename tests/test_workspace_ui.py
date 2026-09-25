from pathlib import Path
import pytest
from PIL import Image
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.app import Editor, ROOT
from test_ai import mock_api, configure, wait_for
from test_editor import settled
from test_layers import selection_completion

LIVE = []


@pytest.mark.parametrize("size", [(1440,930),(1080,700)])
def test_draw_layer_ai_draft_and_advice_controls(qt_app, ai_store, tmp_path, size, pixel_protocol_stub):
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists(): QFontDatabase.addApplicationFont(str(path))
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(w.toString() for w in warnings))
    engine.rootContext().setContextProperty("editor",editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), errors
    window = engine.rootObjects()[0]; window.resize(*size)
    LIVE.append((engine,editor,window))

    def find(name):
        obj = window.findChild(QObject,name)
        assert obj is not None, name
        return obj
    def click(name):
        obj = find(name)
        point = obj.mapToScene(QPointF(obj.width()/2,obj.height()/2)).toPoint()
        assert 0 < point.x() < size[0] and 0 < point.y() < size[1], name
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point); QTest.qWait(80)

    try:
        # UI geometry is tested against the real bundled photograph, never a user image.
        editor.loadDemo(); wait_for(lambda: editor.hasImage and settled(editor))
        click("addLayerButton")
        assert len(editor.layers)==2 and editor.selectionLabel=="全图"
        editor.renameLayer("天空练习")
        click("tool_rect")
        assert window.property("selectionTool") == "rect"
        area = find("selectionMouse")
        start = area.mapToScene(QPointF(area.width()*.2,area.height()*.1)).toPoint()
        end = area.mapToScene(QPointF(area.width()*.8,area.height()*.4)).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(window,end,100)
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end)
        wait_for(lambda: settled(editor))
        assert editor._candidate["ops"][0]["kind"] == "rect"
        assert editor._layer()["mask"]["base"] == "full"  # Selection is detached until output.
        wait_for(lambda: window.property("selectionPreviewReady"))
        click("acceptSelectionButton")
        editor.setParameter("exposure",.7); editor.finishGesture()
        wait_for(lambda: settled(editor))
        with mock_api(selection_completion()) as (url,requests):
            configure(editor.ai,url)
            editor.setAutoRefine(False)  # This case isolates cloud protocol/UI; real GrabCut is tested separately.
            click("selectionTab")
            find("descriptionInput").setProperty("text","让照片更亮")
            find("selectionDescriptionInput").setProperty("text","选出主体，生成粗选区")
            click("directSelectionButton")
            wait_for(lambda: editor.hasSelectionDraft and settled(editor))
            wait_for(lambda: window.property("selectionPreviewReady"))
            assert find("acceptSelectionButton").property("visible")
            click("acceptSelectionButton")
            wait_for(lambda: not editor.hasSelectionDraft and settled(editor))
            assert editor._layer()["mask"]["bitmap"] and not editor._layer()["mask"]["ops"]
            assert len(requests) == 1
            import json
            sent=json.loads(requests[0][2]["messages"][1]["content"][0]["text"])
            assert sent["mode"]=="selection" and sent["request"]=="选出主体，生成粗选区"
            assert find("descriptionInput").property("text")=="让照片更亮"
        assert len(editor.conversation) == 4
        wait_for(lambda: window.property("previewReady"))
        QTest.qWait(200)
        assert find("conversationList").property("count") == 4
        # Export reproducible UI evidence, with mock provenance explicit in the filename.
        path = ROOT / "artifacts" / f"iphoto-v1.3-ui-mock-{size[0]}.png"
        assert window.grabWindow().save(str(path))
        assert not errors, errors
    finally:
        window.close(); editor.close(); qt_app.processEvents()


def test_text_undo_long_brush_and_close_failure(qt_app, ai_store, tmp_path, monkeypatch):
    from copy import deepcopy
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine(); errors = []
    engine.warnings.connect(lambda warnings: errors.extend(w.toString() for w in warnings))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), errors
    window = engine.rootObjects()[0]; window.resize(1080,700)
    LIVE.append((engine,editor,window))
    def find(name):
        item = window.findChild(QObject,name)
        assert item is not None, name
        return item
    try:
        path = tmp_path / "photo.png"
        Image.new("RGB",(400,300),(80,110,150)).save(path)
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        editor.addLayer()
        editor.selectionAction("all")
        before = deepcopy(editor._layers)
        field = find("descriptionInput"); field.forceActiveFocus()
        for key in (Qt.Key_A,Qt.Key_B,Qt.Key_C): QTest.keyClick(window,key)
        assert field.property("text") == "abc"
        assert window.property("editingText")
        QTest.keyClick(window,Qt.Key_Z,Qt.ControlModifier)
        assert field.property("text") == ""
        assert editor._layers == before

        project = tmp_path / "work.iphoto"
        editor.saveProject(str(project))
        layer_name = find("layerNameInput"); layer_name.forceActiveFocus()
        QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
        for key in (Qt.Key_N,Qt.Key_E,Qt.Key_W): QTest.keyClick(window,key)
        QTest.keyClick(window,Qt.Key_S,Qt.ControlModifier)
        from iphoto.document import read_project
        assert read_project(project)["layers"][-1]["name"] == "new"

        editor.selectionAction("clear")
        brush=find("tool_brush")
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,brush.mapToScene(QPointF(brush.width()/2,brush.height()/2)).toPoint()); QTest.qWait(100)
        assert window.property("selectionTool") == "brush"
        area = find("selectionMouse")
        start = area.mapToScene(QPointF(area.width()*.1,area.height()*.2)).toPoint()
        end = area.mapToScene(QPointF(area.width()*.92,area.height()*.85)).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
        for i in range(620):
            point = area.mapToScene(QPointF(area.width()*(.1+(i%100)/125),area.height()*(.2+i/1000))).toPoint()
            QTest.mouseMove(window,point,0)
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end)
        wait_for(lambda: settled(editor))
        points = editor._candidate["ops"][-1]["points"]
        assert len(points) <= 512
        assert points[-1][0] == pytest.approx(.92, abs=.01)
        assert points[-1][1] == pytest.approx(.85, abs=.01)

        def fail(*args, **kwargs): raise OSError("disk full")
        with monkeypatch.context() as patch:
            patch.setattr("iphoto.controllers.session.write_project",fail)
            assert not window.close()
            dialog = find("recoveryFailureDialog")
            wait_for(lambda: dialog.property("opened"))
            assert window.isVisible()
            assert window.grabWindow().save(str(ROOT / "artifacts/iphoto-quality-close-protection.png"))
            dialog.close()
        assert editor.prepareClose()
        assert not errors, errors
    finally:
        window.setProperty("discardOnClose",True)
        window.close(); editor.close(); qt_app.processEvents()


def test_selection_review_exits_comparison(qt_app, ai_store, tmp_path, pixel_protocol_stub):
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine(); errors = []
    engine.warnings.connect(lambda warnings: errors.extend(w.toString() for w in warnings))
    engine.rootContext().setContextProperty("editor",editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), errors
    window = engine.rootObjects()[0]
    LIVE.append((engine,editor,window))
    try:
        path = tmp_path / "photo.png"
        Image.new("RGB",(400,300),(80,110,150)).save(path)
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        window.setProperty("compare",True)
        with mock_api(selection_completion()) as (url,_):
            configure(editor.ai,url)
            editor.sendMessage("选中主体","selection")
            wait_for(lambda: editor.hasSelectionDraft and settled(editor) and window.property("selectionPreviewReady"))
        assert not window.property("compare") and window.property("showMask")
        project = tmp_path / "draft.iphoto"; editor.saveProject(str(project))
        editor.discardSelection(); wait_for(lambda: settled(editor))
        window.setProperty("compare",True)
        editor.openProject(str(project))
        wait_for(lambda: editor.hasSelectionDraft and settled(editor) and window.property("selectionPreviewReady"))
        assert not window.property("compare")
        assert not errors, errors
    finally:
        window.close(); editor.close(); qt_app.processEvents()
