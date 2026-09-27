import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "components"
import "components/FilePaths.js" as FilePaths
import "components/selection"
import "areas"

ApplicationWindow {
    id: window
    property var editorContext: editor
    readonly property var selection: editor.selection
    width: 1440; height: 930; minimumWidth: 1080; minimumHeight: 700
    visible: true
    title: "iPhoto 1.7.0 · 原图工作室" + (editor.dirty ? " *" : "")
    color: "#23262a"; font.family: "Microsoft YaHei"; font.pixelSize: 12
    property color ink: "#e4e8ee"
    property color muted: "#a4adb8"
    property color line: "#40454d"
    property color panel: "#2d3137"
    property bool editingEnabled: editor.hasImage && !editor.busy && !editor.hasSelectionDraft && !editor.hasRegionDraft
    property bool compare: false
    property real split: .5
    readonly property bool showMask: selection.showMask
    readonly property string selectionTool: selection.tool
    readonly property string selectionMode: selection.mode
    readonly property real brushRadius: selection.brushRadius
    readonly property real tolerance: selection.wandTolerance
    property string sceneHoverId: ""
    readonly property bool regionModal: editor.hasRegionDraft
    // Give the photo usable space on the minimum-height window. Once the user
    // toggles the assistant, QML drops this binding and keeps that choice.
    property bool chatOpen: height >= 800
    property bool subjectAvailable: editor.imageCapabilities.some(function(c) { return c.id==="u2net" && c.available })
    property bool discardOnClose: false
    property bool editingText: activeFocusItem !== null && typeof activeFocusItem.undo === "function" && !activeFocusItem.readOnly
    property bool textFocus: activeFocusItem !== null && typeof activeFocusItem.selectAll === "function"
    property bool modalActive: aiSettings.opened || pluginsDialog.opened || recoveryFailure.opened || importDialog.visible || exportDialog.opened || sourceRelinkDialog.opened || projectSaveDialog.opened || projectDialog.visible || conversationExportDialog.opened || canvasPane.menuOpened
    property bool menuActive: fileMenu.opened || editMenu.opened || selectionMenu.opened || viewMenu.opened || extensionsMenu.opened
    property bool navigationShortcutsEnabled: editor.hasImage && !textFocus && !modalActive && !menuActive
    readonly property bool navigationTool: selection.navigationTool
    onTextFocusChanged: if(textFocus) editor.viewport.resetKeys()
    onModalActiveChanged: if(modalActive) editor.viewport.resetKeys()
    onMenuActiveChanged: if(menuActive) editor.viewport.resetKeys()
    property bool selectionPreviewReady: canvasPane.selectionPreviewReady
    property bool previewReady: canvasPane.previewReady
    property bool detailReady: canvasPane.detailReady
    property bool maskDetailReady: canvasPane.maskDetailReady
    palette.window: panel; palette.base: "#22262b"; palette.text: ink; palette.windowText: ink
    palette.button: "#363c44"; palette.buttonText: ink; palette.highlight: "#467e71"; palette.highlightedText: "white"
    // Basic-style delegates use light/mid for hovered+highlighted rows; keep them dark.
    palette.light: "#343b43"; palette.mid: "#3a4149"; palette.midlight: "#3a4149"; palette.dark: "#22262b"
    function commitPendingText() { inspectorPane.commitText() }
    function saveProject() { commitPendingText(); if(editor.projectPath) editor.saveProjectAsync(editor.projectPath); else projectSaveDialog.start() }

    function chooseTool(tool) { selection.chooseTool(tool) }
    function openAISettings() { aiSettings.open() }
    function openCapabilities() { editor.refreshCapabilities(); pluginsDialog.open() }
    function openChatExport() { conversationExportDialog.start() }
    function focusSelectionInput() { inspectorPane.focusSelectionInput() }
    function reviewMask() { selection.reviewMask() }
    onClosing: function(close) { commitPendingText(); if(!discardOnClose) close.accepted=editor.prepareClose() }





    AISettingsDialog { id: aiSettings; objectName: "aiSettingsDialog"; parent: Overlay.overlay; ai: editor.ai; palette.text: "#26372e"; palette.windowText: "#26372e"; palette.buttonText: "#26372e"; palette.base: "white"; palette.button: "#f0f2e9" }
    Dialog {
        id: recoveryFailure; objectName: "recoveryFailureDialog"; parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "恢复副本未能保存"; width: Math.min(470,window.width-40); closePolicy: Popup.CloseOnEscape
        contentItem: ColumnLayout { spacing: 16
            Text { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "当前修改仍在窗口中。请先另存项目，或取消关闭并检查磁盘空间。选择仍然退出会放弃未保存的修改。"; color: ink }
            RowLayout {
                Action { text: "取消关闭"; onClicked: recoveryFailure.close() }
                Action { text: "另存项目"; primary: true; onClicked: { recoveryFailure.close(); projectSaveDialog.start() } }
                Action { objectName: "discardAndCloseButton"; text: "仍然退出"; onClicked: { discardOnClose=true; recoveryFailure.close(); window.close() } }
            }
        }
    }
    Dialog {
        id: pluginsDialog; objectName: "imageCapabilitiesDialog"; parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "图像能力 · 本地扩展"; width: Math.min(620,window.width-40); height: Math.min(560,window.height-50)
        footer: Item { implicitHeight: 48; Action { text: "关闭"; anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; implicitWidth: 90; onClicked: pluginsDialog.close() } }
        contentItem: ScrollView { id: capabilityScroll; clip: true; contentWidth: availableWidth
            ColumnLayout { width: capabilityScroll.availableWidth; spacing: 14
                Caption { text: "以下状态来自本机依赖检测。大型模型按需配置，处理照片时不会自动下载。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Repeater { model: editor.imageCapabilities
                    delegate: Rectangle { required property var modelData; Layout.fillWidth: true; implicitHeight: details.implicitHeight+24; color: "#24282e"; radius: 5
                        ColumnLayout { id: details; x: 12; y: 12; width: parent.width-24; spacing: 8
                            RowLayout { Layout.fillWidth: true
                                Text { text: modelData.name; color: ink; font.bold: true; Layout.fillWidth: true }
                                Caption { text: modelData.status; color: modelData.available ? "#94d0b9" : "#e1bc80" }
                            }
                            Caption { text: modelData.description; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            TextEdit { text: modelData.model_path ? "模型位置："+modelData.model_path : "无需模型文件"; readOnly: true; selectByMouse: true; color: muted; font.pixelSize: 11; wrapMode: TextEdit.Wrap; Layout.fillWidth: true; Layout.preferredHeight: contentHeight }
                            RowLayout { Layout.fillWidth: true
                                Caption { text: modelData.license; Layout.fillWidth: true; elide: Text.ElideRight }
                                Action { text: "项目文档 ↗"; subtle: true; onClicked: Qt.openUrlExternally(modelData.source) }
                            }
                        }
                    }
                }
                Caption { text: "EfficientSAM 用于本地像素选区。发丝抠图模型、SAM 3 和局部重绘尚未集成；不兼容 Photoshop 的 8bf 插件。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Action { text: "重新检测"; onClicked: editor.refreshCapabilities() }
            }
        }
    }
    FileDialog { id: importDialog; title: "选择照片"; currentFolder: FilePaths.directoryUrl(editor.photoBrowseDirectory); nameFilters: ["照片 (*.jpg *.jpeg *.png *.mpo)", "相机 RAW (*.cr2 *.cr3 *.nef *.nrw *.arw *.srf *.sr2 *.dng *.raf *.orf *.rw2 *.pef *.srw *.x3f)", "所有文件 (*)"]; onAccepted: editor.openImage(selectedFile.toString()) }
    ProjectSaveDialog { id: projectSaveDialog; editor: editorContext }
    ExportDialog { id: exportDialog; editor: editorContext }
    SourceRelinkDialog { id: sourceRelinkDialog; editor: editorContext }
    FileDialog { id: projectDialog; title: "打开项目"; currentFolder: FilePaths.directoryUrl(editor.projectBrowseDirectory); nameFilters: ["iPhoto 项目 (*.iphoto *.json)"]; onAccepted: editor.openProject(selectedFile.toString()) }
    ConversationExportDialog { id: conversationExportDialog; editor: editorContext }
    menuBar: MenuBar {
        Menu { id: fileMenu; title: "文件"
            MenuItem { text: "打开照片…    Ctrl+O"; enabled: !editor.busy && !editor.savingProject; onTriggered: importDialog.open() }
            MenuItem { text: "打开项目…"; enabled: !editor.busy && !editor.savingProject; onTriggered: projectDialog.open() }
            MenuItem { text: "保存项目    Ctrl+S"; enabled: editor.hasImage && !editor.busy && !editor.savingProject; onTriggered: saveProject() }
            MenuItem { text: "项目另存为…"; enabled: editor.hasImage && !editor.busy && !editor.savingProject; onTriggered: { commitPendingText(); projectSaveDialog.start() } }
            MenuSeparator {}
            MenuItem { text: "导出照片…    Ctrl+E"; enabled: editingEnabled; onTriggered: exportDialog.open() }
            MenuItem { text: "恢复最近会话"; enabled: editor.canRecover && !editor.busy && !editor.savingProject; onTriggered: editor.recoverLatest() }
        }
        Menu { id: editMenu; title: "编辑"
            MenuItem { text: "撤销    Ctrl+Z"; enabled: editor.canUndo && !editor.busy; onTriggered: editor.undo() }
            MenuItem { text: "重做    Ctrl+Shift+Z"; enabled: editor.canRedo && !editor.busy; onTriggered: editor.redo() }
            MenuItem { text: "复制调整层"; enabled: editingEnabled; onTriggered: editor.duplicateLayer() }
        }
        Menu { id: selectionMenu; title: "选择"
            MenuItem { text: "新建选区"; enabled: editingEnabled; onTriggered: chooseTool("rect") }
            MenuItem { text: "载入当前层蒙版"; enabled: editingEnabled; onTriggered: reviewMask() }
            MenuItem { text: "全选    Ctrl+A"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; onTriggered: editor.draftAction("all") }
            MenuItem { text: "反选    Ctrl+Shift+I"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: editor.draftAction("invert") }
            MenuItem { text: "取消选区    Ctrl+D"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: editor.discardSelection() }
            MenuItem { text: "内容感知填充…"; enabled: editor.hasSelectionDraft && !editor.busy && selection.inpaintAvailable; onTriggered: selection.apply("inpaint") }
        }
        Menu { id: viewMenu; objectName: "viewMenu"; title: "视图"
            MenuItem { text: "放大    Ctrl++"; enabled: editor.hasImage; onTriggered: editor.viewport.step(1) }
            MenuItem { text: "缩小    Ctrl+−"; enabled: editor.hasImage; onTriggered: editor.viewport.step(-1) }
            MenuItem { text: "适应画布    Ctrl+0"; enabled: editor.hasImage; onTriggered: editor.viewport.fit() }
            MenuItem { text: "原图尺寸比例 100%    Ctrl+1"; enabled: editor.hasImage; onTriggered: editor.viewport.setZoom(1) }
            MenuItem { text: "显示导航图"; checkable: true; checked: canvasPane.showNavigator; onTriggered: canvasPane.showNavigator=checked }
            MenuItem { text: "直接用滚轮缩放"; checkable: true; checked: canvasPane.wheelZoom; onTriggered: canvasPane.wheelZoom=checked }
            MenuSeparator {}
            MenuItem { text: "显示 / 隐藏蒙版    Q"; onTriggered: selection.toggleShowMask() }
            MenuItem { text: "显示 / 隐藏 AI 助手"; onTriggered: chatOpen=!chatOpen }
        }
        Menu { id: extensionsMenu; title: "扩展"
            MenuItem { text: "图像能力…"; onTriggered: { editor.refreshCapabilities(); pluginsDialog.open() } }
            MenuItem { text: "AI 连接设置…"; onTriggered: aiSettings.open() }
        }
    }
    Shortcut { sequence: "Ctrl+O"; enabled: !modalActive && !editor.busy && !editor.savingProject; onActivated: importDialog.open() }
    Shortcut { sequence: "Ctrl+S"; enabled: !modalActive && editor.hasImage && !editor.busy && !editor.savingProject; onActivated: saveProject() }
    Shortcut { sequence: "Ctrl+Shift+S"; enabled: !modalActive && editor.hasImage && !editor.busy && !editor.savingProject; onActivated: { commitPendingText(); projectSaveDialog.start() } }
    Shortcut { sequence: "Ctrl+E"; enabled: !modalActive && editingEnabled; onActivated: exportDialog.open() }
    Shortcut { sequence: "Ctrl+Z"; enabled: !textFocus && !modalActive; onActivated: editor.undo() }
    Shortcut { sequence: "Ctrl+Shift+Z"; enabled: !textFocus && !modalActive; onActivated: editor.redo() }
    Shortcut { sequence: "Ctrl+A"; enabled: !textFocus && !modalActive; onActivated: editor.draftAction("all") }
    Shortcut { sequence: "Ctrl+D"; enabled: !textFocus && !modalActive; onActivated: editor.discardSelection() }
    Shortcut { sequence: "Ctrl+Shift+I"; enabled: !textFocus && !modalActive; onActivated: editor.draftAction("invert") }
    Shortcut { sequence: "Ctrl+G"; enabled: !textFocus && !modalActive && editingEnabled; onActivated: editor.groupLayer() }
    Shortcut { sequence: "Escape"; enabled: !textFocus && !modalActive && !menuActive && selection.taskCancellable; onActivated: selection.cancelTask() }
    Shortcut { sequence: "Ctrl+0"; enabled: navigationShortcutsEnabled; onActivated: editor.viewport.fit() }
    Shortcut { sequence: "Ctrl+1"; enabled: navigationShortcutsEnabled; onActivated: editor.viewport.setZoom(1) }
    Shortcut { sequences: ["Ctrl++", "Ctrl+="]; enabled: navigationShortcutsEnabled; onActivated: editor.viewport.step(1) }
    Shortcut { sequence: "Ctrl+-"; enabled: navigationShortcutsEnabled; onActivated: editor.viewport.step(-1) }
    Shortcut { sequence: "Z"; enabled: navigationShortcutsEnabled; onActivated: chooseTool("zoom") }
    Shortcut { sequence: "V"; enabled: !textFocus && !modalActive; onActivated: chooseTool("inspect") }
    Shortcut { sequence: "H"; enabled: navigationShortcutsEnabled; onActivated: chooseTool("hand") }
    Shortcut { sequence: "M"; enabled: !textFocus && !modalActive; onActivated: chooseTool("rect") }
    Shortcut { sequence: "L"; enabled: !textFocus && !modalActive; onActivated: chooseTool("polygon") }
    Shortcut { sequence: "B"; enabled: !textFocus && !modalActive; onActivated: chooseTool("brush") }
    Shortcut { sequence: "J"; enabled: !textFocus && !modalActive; onActivated: chooseTool("heal") }
    Shortcut { sequence: "O"; enabled: !textFocus && !modalActive; onActivated: chooseTool("object") }
    Shortcut { sequence: "S"; enabled: !textFocus && !modalActive; onActivated: chooseTool("smart") }
    Shortcut { sequence: "W"; enabled: !textFocus && !modalActive; onActivated: chooseTool("wand") }
    Shortcut { sequence: "Q"; enabled: !textFocus && !modalActive; onActivated: selection.toggleShowMask() }
    Shortcut { sequence: "X"; enabled: !textFocus && !modalActive; onActivated: selection.toggleMode() }
    Shortcut { sequence: "["; enabled: !textFocus && !modalActive; onActivated: selection.adjustBrush(-1) }
    Shortcut { sequence: "]"; enabled: !textFocus && !modalActive; onActivated: selection.adjustBrush(1) }

    ColumnLayout {
        anchors.fill: parent; spacing: 0
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 49; color: "#2a2e33"
            RowLayout { anchors.fill: parent; anchors.leftMargin: 15; anchors.rightMargin: 15; spacing: 10
                Image { source: "../../../assets/icon.svg"; Layout.preferredWidth: 27; Layout.preferredHeight: 27 }
                Text { text: "iPhoto"; font.family: "Georgia"; font.pixelSize: 24; color: ink }
                Caption { text: "1.7.0  /  原图工作室" }
                Item { Layout.fillWidth: true }
                Action { objectName: "imageCapabilitiesButton"; text: "图像能力"; subtle: true; onClicked: { editor.refreshCapabilities(); pluginsDialog.open() } }
                Action { objectName: "aiSettingsButton"; text: "AI 设置"; onClicked: aiSettings.open() }
                Action { objectName: "recoverSessionButton"; text: "恢复未保存"; hint: "找回上次未保存的图层与选区"; visible: editor.canRecover; enabled: !editor.busy && !editor.savingProject; onClicked: editor.recoverLatest() }
                Action { text: "打开照片"; enabled: !editor.busy && !editor.savingProject; onClicked: importDialog.open() }
                Action { objectName: "saveProjectButton"; text: editor.savingProject ? "保存中…" : editor.dirty ? "保存项目 •" : "保存项目"; enabled: editor.hasImage && !editor.busy && !editor.savingProject; onClicked: saveProject() }
                Action { objectName: "exportButton"; text: "导出照片 ↗"; primary: true; enabled: editingEnabled; onClicked: exportDialog.open() }
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 43; color: "#30353c"
            RowLayout { anchors.fill: parent; anchors.leftMargin: 13; anchors.rightMargin: 13; spacing: 8
                ToolOptionsBar { workspace: window; editor: editorContext; onNeedsFocus: canvasPane.focusCanvas() }
                Item { Layout.fillWidth: true }
                Action { text: "载入蒙版"; enabled: editingEnabled; hint: "将当前层蒙版载入独立选区后修正"; onClicked: selection.reviewMask() }
                Action { text: selection.showMask ? "隐藏蒙版 Q" : "显示蒙版 Q"; enabled: editor.hasImage; onClicked: selection.toggleShowMask() }
                Action { objectName: "chatToggleButton"; text: "AI 助手"; primary: chatOpen; onClicked: chatOpen=!chatOpen }
            }
        }
        RowLayout { Layout.fillWidth: true; Layout.fillHeight: true; spacing: 1
            ToolRail { workspace: window; editor: editorContext }
            CanvasPane { id: canvasPane; workspace: window; editor: editorContext }

            InspectorPane { id: inspectorPane; workspace: window; editor: editorContext }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 25; color: "#30363d"
            RowLayout { anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                Caption { objectName: "workspaceStatusText"; text: editor.ai.busy ? editor.ai.requestProgress : editor.status; Layout.fillWidth: true; elide: Text.ElideMiddle; font.pixelSize: 10 }
                Caption { text: editor.rendering ? "正在合成…" : editor.renderTime; font.pixelSize: 10 }
                Caption { text: editor.ai.privacyStatus; font.pixelSize: 10; visible: window.width>1200 }
            }
        }
    }
    Rectangle { id: toast; property string message: ""; property bool isError: false; visible: toastTimer.running; anchors.horizontalCenter: parent.horizontalCenter; y: 100; z: 30; width: Math.min(window.width-120,toastText.implicitWidth+34); height: toastText.height+24; radius: 5; color: isError ? "#8b5144" : "#3e6859"
        Text { id: toastText; x: 17; y: 12; width: Math.min(implicitWidth,window.width-154); text: toast.message; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: "white"; font.pixelSize: 12 }
        Timer { id: toastTimer; interval: 4400 }
    }
    Connections { target: editor
        function onNotification(message,error) { toast.message=message; toast.isError=error; toastTimer.restart() }
        function onRecoverySaveFailed() { recoveryFailure.open() }
        function onImageOpened() { compare=false; split=.5 }
        function onAiSettingsRequested() { aiSettings.open() }
    }
    Connections { target: editor.selection
        function onToolChosen(tool) {
            canvasPane.focusCanvas()
            if (!["inspect","hand","zoom"].includes(tool)) compare=false
        }
        function onDraftBegan(kind) { compare=false }
    }
}

