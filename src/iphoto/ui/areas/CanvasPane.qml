import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: canvasRoot
    required property var workspace
    required property var editor
    readonly property bool previewReady: viewport.previewReady
    readonly property bool selectionPreviewReady: viewport.selectionPreviewReady
    readonly property bool menuOpened: canvasMenu.opened
    property alias wheelZoom: viewport.wheelZoom
    property alias showNavigator: viewport.showNavigator
    function focusCanvas() { viewport.focusCanvas() }
    Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
    RowLayout {
        Layout.fillWidth: true; Layout.preferredHeight: 38; Layout.leftMargin: 14; Layout.rightMargin: 14
        Text { text: editor.imageName || "打开一张照片开始"; color: workspace.ink; Layout.fillWidth: true; elide: Text.ElideMiddle }
        Action { objectName: "undoButton"; text: "撤销"; enabled: editor.canUndo && !editor.busy && !editor.hasRegionDraft; subtle: true; onClicked: editor.undo() }
        Action { objectName: "redoButton"; text: "重做"; enabled: editor.canRedo && !editor.busy && !editor.hasRegionDraft; subtle: true; onClicked: editor.redo() }
        Action { text: workspace.compare ? "结束对比" : "原图对比"; enabled: editor.hasImage; subtle: true; onClicked: { workspace.compare=!workspace.compare; if(workspace.compare) workspace.selectionTool="inspect" } }
    }
    Rectangle {
        Layout.fillWidth: true; Layout.preferredHeight: 32; color: editor.hasSelectionDraft || editor.hasRegionDraft ? "#3c483e" : "#2c373b"
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14
            Text { objectName: "editingContextLabel"; text: editor.hasRegionDraft ? "分区方案预览 · 逐区检查后创建图层" : editor.hasSelectionDraft ? "独立选区草稿 · 尚未改变图层" : "调整对象："+editor.activeLayerName+"  /  "+editor.selectionLabel; color: "#c4d9cf"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
            Action { text: editor.hasRegionDraft ? "取消方案" : "取消选区"; visible: editor.hasSelectionDraft || editor.hasRegionDraft; enabled: !editor.busy; implicitHeight: 24; subtle: true; onClicked: editor.hasRegionDraft ? editor.discardRegions() : editor.discardSelection() }
        }
    }
    CanvasViewport {
        id: viewport
        workspace: canvasRoot.workspace; editor: canvasRoot.editor
        holdingOriginal: holdOriginal.pressed
        Layout.fillWidth: true; Layout.fillHeight: true
    }
    RowLayout {
        Layout.fillWidth: true; Layout.preferredHeight: 37; Layout.leftMargin: 12; Layout.rightMargin: 12; spacing: 5
        Action { objectName: "zoomOutButton"; text: "−"; hint: "缩小 Ctrl+−"; enabled: editor.hasImage; subtle: true; onClicked: { editor.viewport.step(-1); canvasRoot.focusCanvas() } }
        Field {
            id: zoomInput
            objectName: "zoomPercentInput"
            Layout.preferredWidth: 78; implicitHeight: 27; font.pixelSize: 11
            enabled: editor.hasImage; horizontalAlignment: TextInput.AlignHCenter
            text: (editor.viewport.zoom*100).toFixed(editor.viewport.zoom < .1 ? 2 : 1) + "%"
            selectByMouse: true
            property bool userEdited: false
            onTextEdited: userEdited = true
            function sync() { text=(editor.viewport.zoom*100).toFixed(editor.viewport.zoom < .1 ? 2 : 1)+"%" }
            function commit() {
                if (!userEdited) { sync(); return }
                userEdited = false
                var value=Number(text.trim().replace(/%$/, ""))
                if (isFinite(value) && value > 0) editor.viewport.setZoom(value/100)
                sync()
            }
            onAccepted: { commit(); canvasRoot.focusCanvas() }
            onEditingFinished: commit()
            Keys.onEscapePressed: { userEdited=false; sync(); canvasRoot.focusCanvas() }
            Connections { target: editor.viewport; function onChanged() { if(!zoomInput.activeFocus) zoomInput.sync() } }
            ToolTip.visible: hovered; ToolTip.text: "输入原图缩放比例并回车 · 1%–3200%\n100% 为一个原图像素对应一个屏幕像素；当前画布按原图分辨率合成。"; ToolTip.delay: 600
        }
        Action { objectName: "zoomInButton"; text: "+"; hint: "放大 Ctrl++ / Ctrl+="; enabled: editor.hasImage; subtle: true; onClicked: { editor.viewport.step(1); canvasRoot.focusCanvas() } }
        Action { objectName: "fitCanvasButton"; text: "适应"; hint: "适应画布 Ctrl+0 · 双击抓手也可复位"; primary: editor.viewport.fitMode; enabled: editor.hasImage; implicitHeight: 27; onClicked: { editor.viewport.fit(); canvasRoot.focusCanvas() } }
        Action { objectName: "actualSizeButton"; text: "100%"; hint: "原图尺寸比例 Ctrl+1"; enabled: editor.hasImage; implicitHeight: 27; onClicked: { editor.viewport.setZoom(1); canvasRoot.focusCanvas() } }
        Action { objectName: "navigatorToggle"; text: "导航"; hint: "显示 / 隐藏角落导航图"; primary: viewport.showNavigator; implicitHeight: 27; onClicked: { viewport.showNavigator=!viewport.showNavigator; canvasRoot.focusCanvas() } }
        Item { Layout.fillWidth: true }
        Action { id: holdOriginal; text: "按住看原图"; subtle: true; implicitHeight: 27; font.pixelSize: 10 }
        Action {
            objectName: "canvasHelpButton"; text: "?"; hint: "画布操作与滚轮设置"; subtle: true; implicitHeight: 27
            onClicked: canvasMenu.popup()
            Menu {
                id: canvasMenu
                y: -implicitHeight; width: 330
                MenuItem { text: "直接用滚轮缩放"; checkable: true; checked: viewport.wheelZoom; onTriggered: viewport.wheelZoom=checked }
                MenuSeparator {}
                MenuItem { text: "空格＋拖动 / 中键拖动：临时平移"; enabled: false }
                MenuItem { text: "H 抓手 · 双击适应画布"; enabled: false }
                MenuItem { text: "Z 缩放 · 单击放大 / Alt＋单击缩小"; enabled: false }
                MenuItem { text: "Z＋左右拖动：连续缩放"; enabled: false }
                MenuItem { text: "Alt＋滚轮：以鼠标为中心缩放"; enabled: false }
                MenuItem { text: "滚轮上下移 · Shift＋滚轮左右移"; enabled: false }
                MenuItem { text: "Ctrl＋加减：缩放 · Ctrl+0：适应"; enabled: false }
                MenuItem { text: "Ctrl+1：原图 100% 尺寸比例"; enabled: false }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true; Layout.preferredHeight: 22; Layout.leftMargin: 14; Layout.rightMargin: 14
        Caption { text: editor.imageInfo + " · 原图分辨率"; font.pixelSize: 10 }
        Caption { text: "空格拖动 · Alt＋滚轮缩放"; font.pixelSize: 10; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight }
    }
    ConversationPane { workspace: canvasRoot.workspace; editor: canvasRoot.editor }
}
