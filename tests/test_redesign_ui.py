from copy import deepcopy
from pathlib import Path
import pytest
from PySide6.QtCore import QObject,QPointF,Qt,QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from iphoto.workspace import Editor,ROOT
from test_redesign import scene,region_completion
from test_editor import settled
from test_ai import wait_for,mock_api,configure

LIVE=[]


@pytest.mark.parametrize("size",[(1440,930),(1080,700)])
def test_draft_output_and_region_review_are_separate_from_chat(qt_app,ai_store,tmp_path,size,pixel_protocol_stub):
    for name in ("msyh.ttc","msyhbd.ttc","segoeui.ttf"):
        font=Path("C:/Windows/Fonts")/name
        if font.exists(): QFontDatabase.addApplicationFont(str(font))
    editor=Editor(ai_store=ai_store);engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor",editor)
    engine.load(QUrl.fromLocalFile(str(ROOT/"src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(),warnings
    window=engine.rootObjects()[0];window.resize(*size);LIVE.append((engine,editor,window))
    def find(name):
        obj=window.findChild(QObject,name); assert obj is not None,name
        return obj
    def click(name):
        obj=find(name);assert obj.property("enabled"),name
        point=obj.mapToScene(QPointF(obj.width()/2,obj.height()/2)).toPoint()
        assert 0<point.x()<size[0] and 0<point.y()<size[1],name
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(80)
    try:
        path=tmp_path/"scene.png";scene()[0].save(path)
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor) and window.property("previewReady"))
        original=deepcopy(editor._layers)
        click("tool_rect")
        area=find("selectionMouse")
        a=area.mapToScene(QPointF(area.width()*.24,area.height()*.14)).toPoint()
        b=area.mapToScene(QPointF(area.width()*.76,area.height()*.86)).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,a);QTest.mouseMove(window,b,80);QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,b)
        wait_for(lambda:settled(editor) and window.property("selectionPreviewReady"))
        assert editor._layers==original
        assert not find("applyDescriptionButton").property("enabled")
        QTest.keyClick(window,Qt.Key_D,Qt.ControlModifier)
        assert not editor.hasSelectionDraft and editor._layers==original
        click("tool_rect")
        editor.drawDraft("rect","replace",[[.24,.14],[.76,.86]],.02)
        editor.refineSelection("grabcut")
        wait_for(lambda:settled(editor) and window.property("selectionPreviewReady"))
        assert "bitmap" in editor._candidate
        # The output controls remain visible even when the property panel scrolls.
        assert window.grabWindow().save(str(ROOT/f"artifacts/v13-selection-{size[0]}.png"))
        click("selectionToLayerButton")
        wait_for(lambda:settled(editor) and window.property("previewReady"))
        assert len(editor.layers)==2 and not editor.hasSelectionDraft
        assert not window.property("showMask")
        editor.undo();wait_for(lambda:settled(editor))
        assert editor._layers==original
        with mock_api(region_completion()) as (url,_):
            configure(editor.ai,url)
            mode=find("chatModeBox");mode.forceActiveFocus()
            QTest.keyClick(window,Qt.Key_End);QTest.keyClick(window,Qt.Key_Return)
            find("descriptionInput").setProperty("text","主体提亮，背景分别压暗")
            click("applyDescriptionButton")
            wait_for(lambda:editor.hasRegionDraft and settled(editor) and window.property("previewReady") and window.property("selectionPreviewReady"))
        assert editor._layers==original
        assert not find("exportButton").property("enabled")
        assert window.grabWindow().save(str(ROOT/f"artifacts/v13-regions-mock-{size[0]}.png"))
        click("selectionToLayerButton")
        assert len(editor.layers)==3 and not editor.hasRegionDraft
        editor.undo();assert editor._layers==original
        click("imageCapabilitiesButton")
        wait_for(lambda:find("imageCapabilitiesDialog").property("opened"))
        assert window.property("modalActive")
        QTest.keyClick(window,Qt.Key_M)
        assert not editor.hasSelectionDraft  # Shortcuts must not edit behind a modal panel.
        assert window.grabWindow().save(str(ROOT/f"artifacts/v13-capabilities-{size[0]}.png"))
        find("imageCapabilitiesDialog").close()
        click("tool_brush")
        assert window.property("selectionMode")=="add"
        editor.discardSelection()
        assert not warnings,warnings
    finally:
        window.close();editor.close();qt_app.processEvents()
