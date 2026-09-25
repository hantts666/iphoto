import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "components"
import "areas"

ApplicationWindow {
    id: window
    property var editorContext: editor
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
    property bool showMask: false
    property string selectionTool: "inspect"
    property string selectionMode: "replace"
    property real brushRadius: .025
    property real tolerance: 24
    property int inspectorPage: 0
    property bool chatOpen: true
    property bool selectionAiOpen: false
    property bool subjectAvailable: editor.imageCapabilities.some(function(c) { return c.id==="u2net" && c.available })
    property bool hadDraft: false
    property bool discardOnClose: false
    property bool editingText: activeFocusItem !== null && typeof activeFocusItem.undo === "function" && !activeFocusItem.readOnly
    property bool textFocus: activeFocusItem !== null && typeof activeFocusItem.selectAll === "function"
    property bool modalActive: aiSettings.opened || pluginsDialog.opened || recoveryFailure.opened || importDialog.visible || exportDialog.visible || saveDialog.visible || projectDialog.visible || chatDialog.visible || canvasPane.menuOpened
    property bool menuActive: fileMenu.opened || editMenu.opened || selectionMenu.opened || viewMenu.opened || extensionsMenu.opened
    property bool navigationShortcutsEnabled: editor.hasImage && !textFocus && !modalActive && !menuActive
    property bool navigationTool: ["inspect", "hand", "zoom"].includes(selectionTool)
    onTextFocusChanged: if(textFocus) editor.viewport.resetKeys()
    onModalActiveChanged: if(modalActive) editor.viewport.resetKeys()
    onMenuActiveChanged: if(menuActive) editor.viewport.resetKeys()
    property bool selectionPreviewReady: canvasPane.selectionPreviewReady
    property bool previewReady: canvasPane.previewReady
    palette.window: panel; palette.base: "#22262b"; palette.text: ink; palette.windowText: ink
    palette.button: "#363c44"; palette.buttonText: ink; palette.highlight: "#467e71"; palette.highlightedText: "white"
    function commitPendingText() { inspectorPane.commitText() }
    function saveProject() { commitPendingText(); if(editor.projectPath) editor.saveProject(editor.projectPath); else saveDialog.open() }

    function chooseTool(tool) {
        var navigation = ["inspect", "hand", "zoom"].includes(tool)
        if(!editor.hasImage || (!navigation && (editor.busy || editor.hasRegionDraft))) return
        if(tool==="brush" && selectionTool!=="brush") selectionMode="add"
        if(tool==="smart" && selectionTool!=="smart") editor.startPixelSelection(false)
        selectionTool=tool
        if(!navigation) { if(tool!=="object" && tool!=="smart") editor.beginSelection("empty"); inspectorPage=tool==="object" ? 2 : 1; showMask=true; compare=false }
        canvasPane.focusCanvas()
    }
    function openAISettings() { aiSettings.open() }
    function openCapabilities() { editor.refreshCapabilities(); pluginsDialog.open() }
    function openChatExport() { chatDialog.open() }
    function focusSelectionInput() { inspectorPane.focusSelectionInput() }
    function reviewMask() { editor.beginSelection("current"); inspectorPage=1; showMask=true; compare=false; selectionTool="brush"; selectionMode="add" }
    onClosing: function(close) { commitPendingText(); if(!discardOnClose) close.accepted=editor.prepareClose() }





    AISettingsDialog { id: aiSettings; objectName: "aiSettingsDialog"; parent: Overlay.overlay; ai: editor.ai; palette.text: "#26372e"; palette.windowText: "#26372e"; palette.buttonText: "#26372e"; palette.base: "white"; palette.button: "#f0f2e9" }
    Dialog {
        id: recoveryFailure; objectName: "recoveryFailureDialog"; parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: "恢复副本未能保存"; width: Math.min(470,window.width-40); closePolicy: Popup.CloseOnEscape
        contentItem: ColumnLayout { spacing: 16
            Text { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "当前修改仍在窗口中。请先另存项目，或取消关闭并检查磁盘空间。选择仍然退出会放弃未保存的修改。"; color: ink }
            RowLayout {
                Action { text: "取消关闭"; onClicked: recoveryFailure.close() }
                Action { text: "另存项目"; primary: true; onClicked: { recoveryFailure.close(); saveDialog.open() } }
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
    FileDialog { id: importDialog; title: "选择照片"; nameFilters: ["照片 (*.jpg *.jpeg *.png)"]; onAccepted: editor.openImage(selectedFile.toString()) }
    FileDialog { id: exportDialog; title: "导出合成照片 · 使用新文件名"; fileMode: FileDialog.SaveFile; defaultSuffix: "png"; nameFilters: ["无损 PNG (*.png)","JPEG (*.jpg)"]; onAccepted: editor.exportImage(selectedFile.toString()) }
    FileDialog { id: saveDialog; title: "保存图层、蒙版与对话"; fileMode: FileDialog.SaveFile; defaultSuffix: "iphoto"; nameFilters: ["iPhoto 项目 (*.iphoto)"]; onAccepted: { commitPendingText(); editor.saveProject(selectedFile.toString()) } }
    FileDialog { id: projectDialog; title: "打开项目"; nameFilters: ["iPhoto 项目 (*.iphoto *.json)"]; onAccepted: editor.openProject(selectedFile.toString()) }
    FileDialog { id: chatDialog; title: "导出 AI 对话"; fileMode: FileDialog.SaveFile; defaultSuffix: "md"; nameFilters: ["Markdown (*.md)"]; onAccepted: editor.exportConversation(selectedFile.toString()) }
    menuBar: MenuBar {
        Menu { id: fileMenu; title: "文件"
            MenuItem { text: "打开照片…    Ctrl+O"; enabled: !editor.busy; onTriggered: importDialog.open() }
            MenuItem { text: "打开项目…"; enabled: !editor.busy; onTriggered: projectDialog.open() }
            MenuItem { text: "保存项目    Ctrl+S"; enabled: editor.hasImage && !editor.busy; onTriggered: saveProject() }
            MenuItem { text: "项目另存为…"; enabled: editor.hasImage && !editor.busy; onTriggered: saveDialog.open() }
            MenuSeparator {}
            MenuItem { text: "导出照片…    Ctrl+E"; enabled: editingEnabled; onTriggered: exportDialog.open() }
            MenuItem { text: "恢复最近会话"; enabled: editor.canRecover && !editor.busy; onTriggered: editor.recoverLatest() }
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
        }
        Menu { id: viewMenu; objectName: "viewMenu"; title: "视图"
            MenuItem { text: "放大    Ctrl++"; enabled: editor.hasImage; onTriggered: editor.viewport.step(1) }
            MenuItem { text: "缩小    Ctrl+−"; enabled: editor.hasImage; onTriggered: editor.viewport.step(-1) }
            MenuItem { text: "适应画布    Ctrl+0"; enabled: editor.hasImage; onTriggered: editor.viewport.fit() }
            MenuItem { text: "原图尺寸比例 100%    Ctrl+1"; enabled: editor.hasImage; onTriggered: editor.viewport.setZoom(1) }
            MenuItem { text: "显示导航图"; checkable: true; checked: canvasPane.showNavigator; onTriggered: canvasPane.showNavigator=checked }
            MenuItem { text: "直接用滚轮缩放"; checkable: true; checked: canvasPane.wheelZoom; onTriggered: canvasPane.wheelZoom=checked }
            MenuSeparator {}
            MenuItem { text: "显示 / 隐藏蒙版    Q"; onTriggered: showMask=!showMask }
            MenuItem { text: "显示 / 隐藏 AI 助手"; onTriggered: chatOpen=!chatOpen }
        }
        Menu { id: extensionsMenu; title: "扩展"
            MenuItem { text: "图像能力…"; onTriggered: { editor.refreshCapabilities(); pluginsDialog.open() } }
            MenuItem { text: "AI 连接设置…"; onTriggered: aiSettings.open() }
        }
    }
    Shortcut { sequence: "Ctrl+O"; enabled: !modalActive && !editor.busy; onActivated: importDialog.open() }
    Shortcut { sequence: "Ctrl+S"; enabled: !modalActive && editor.hasImage && !editor.busy; onActivated: saveProject() }
    Shortcut { sequence: "Ctrl+Shift+S"; enabled: !modalActive && editor.hasImage && !editor.busy; onActivated: saveDialog.open() }
    Shortcut { sequence: "Ctrl+E"; enabled: !modalActive && editingEnabled; onActivated: exportDialog.open() }
    Shortcut { sequence: "Ctrl+Z"; enabled: !textFocus && !modalActive; onActivated: editor.undo() }
    Shortcut { sequence: "Ctrl+Shift+Z"; enabled: !textFocus && !modalActive; onActivated: editor.redo() }
    Shortcut { sequence: "Ctrl+A"; enabled: !textFocus && !modalActive; onActivated: editor.draftAction("all") }
    Shortcut { sequence: "Ctrl+D"; enabled: !textFocus && !modalActive; onActivated: editor.discardSelection() }
    Shortcut { sequence: "Ctrl+Shift+I"; enabled: !textFocus && !modalActive; onActivated: editor.draftAction("invert") }
    Shortcut { sequence: "Ctrl+G"; enabled: !textFocus && !modalActive && editingEnabled; onActivated: editor.groupLayer() }
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
    Shortcut { sequence: "O"; enabled: !textFocus && !modalActive; onActivated: chooseTool("object") }
    Shortcut { sequence: "S"; enabled: !textFocus && !modalActive; onActivated: chooseTool("smart") }
    Shortcut { sequence: "W"; enabled: !textFocus && !modalActive; onActivated: chooseTool("wand") }
    Shortcut { sequence: "Q"; enabled: !textFocus && !modalActive; onActivated: showMask=!showMask }
    Shortcut { sequence: "X"; enabled: !textFocus && !modalActive; onActivated: selectionMode=selectionMode==="subtract" ? "add" : "subtract" }
    Shortcut { sequence: "["; enabled: !textFocus && !modalActive; onActivated: brushRadius=Math.max(.003,brushRadius-.005) }
    Shortcut { sequence: "]"; enabled: !textFocus && !modalActive; onActivated: brushRadius=Math.min(.15,brushRadius+.005) }

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
                Action { text: "打开照片"; enabled: !editor.busy; onClicked: importDialog.open() }
                Action { objectName: "saveProjectButton"; text: editor.dirty ? "保存项目 •" : "保存项目"; enabled: editor.hasImage && !editor.busy; onClicked: saveProject() }
                Action { objectName: "exportButton"; text: "导出照片 ↗"; primary: true; enabled: editingEnabled; onClicked: exportDialog.open() }
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 43; color: "#30353c"
            RowLayout { anchors.fill: parent; anchors.leftMargin: 13; anchors.rightMargin: 13; spacing: 8
                Caption { text: ({smart:"像素点选",object:"对象点选",inspect:"浏览 / 平移",hand:"抓手 / 平移",zoom:"缩放工具",rect:"矩形选框",ellipse:"椭圆选框",polygon:"自由套索",brush:"蒙版画笔",wand:"颜色魔棒"})[selectionTool]; Layout.preferredWidth: 104; color: ink }
                RowLayout { visible: !navigationTool && selectionTool!=="smart"; spacing: 5
                    Repeater { model: [{key:"replace",label:"新选区"},{key:"add",label:"＋ 添加"},{key:"subtract",label:"－ 减去"}]
                        delegate: Action { required property var modelData; text: modelData.label; primary: selectionMode===modelData.key; onClicked: selectionMode=modelData.key }
                    }
                    Caption { visible: selectionTool==="brush"; text: "笔刷" }
                    FineSlider { visible: selectionTool==="brush"; Layout.preferredWidth: 85; from: .003; to: .15; value: brushRadius; onMoved: brushRadius=value }
                    Caption { visible: selectionTool==="wand"; text: "容差" }
                    SpinBox { visible: selectionTool==="wand"; from: 0; to: 100; value: tolerance; implicitHeight: 30; implicitWidth: 88; onValueModified: tolerance=value }
                }
                RowLayout { visible: selectionTool==="smart"; spacing: 5
                    Caption { text: "点击保留 · Alt 排除 · "+editor.pixelPoints.length+"/6 点"; font.pixelSize: 11 }
                    Action { objectName: "newPixelTargetButton"; text: "新目标"; enabled: !editor.busy; onClicked: {editor.startPixelSelection(false); canvasPane.focusCanvas()} }
                    Action { objectName: "undoPixelPointButton"; text: "退一点"; enabled: !editor.busy && editor.pixelPoints.length>0; onClicked: {editor.undoPixelPoint(); canvasPane.focusCanvas()} }
                }
                Caption { visible: navigationTool; text: selectionTool==="zoom" ? "单击放大 · Alt 缩小 · 左右拖动连续缩放" : "空格临时抓手 · H 拖动 · Z 缩放"; Layout.fillWidth: true; elide: Text.ElideRight }
                Item { Layout.fillWidth: true }
                Action { text: "载入蒙版"; enabled: editingEnabled; hint: "将当前层蒙版载入独立选区后修正"; onClicked: reviewMask() }
                Action { text: showMask ? "隐藏蒙版 Q" : "显示蒙版 Q"; enabled: editor.hasImage; onClicked: showMask=!showMask }
                Action { text: "AI 助手"; primary: chatOpen; onClicked: chatOpen=!chatOpen }
            }
        }
        RowLayout { Layout.fillWidth: true; Layout.fillHeight: true; spacing: 1
            ToolRail { workspace: window; editor: editorContext }
            CanvasPane { id: canvasPane; workspace: window; editor: editorContext }

            InspectorPane { id: inspectorPane; workspace: window; editor: editorContext }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 25; color: "#30363d"
            RowLayout { anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                Caption { text: editor.status; Layout.fillWidth: true; elide: Text.ElideMiddle; font.pixelSize: 10 }
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
        function onChanged() {
            var draft=editor.hasSelectionDraft || editor.hasRegionDraft
            if(draft && !hadDraft) { compare=false; showMask=editor.maskView!=="adjustment"; if(inspectorPage!==2) inspectorPage=1; selectionAiOpen=false }
            else if(!draft && hadDraft) { showMask=false; selectionTool="inspect"; inspectorPage=0 }
            hadDraft=draft
        }
        function onImageOpened() { compare=false; split=.5; showMask=editor.hasSelectionDraft || editor.hasRegionDraft; selectionTool="inspect"; inspectorPage=showMask ? 1 : 0 }
        function onAiSettingsRequested() { aiSettings.open() }
    }
}

