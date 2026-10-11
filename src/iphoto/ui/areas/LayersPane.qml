import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: layerRoot
    required property var workspace
    required property var editor
    property bool expanded: true
    property bool showDuringSelection: false
    readonly property var selection: editor.selection
    readonly property bool rangeMode: editor.selection.pickedLayerId === "" || editor.hasRegionDraft
    readonly property bool detailsVisible: expanded
    function commitText() { if(layerNameInput.activeFocus) editor.renameLayer(layerNameInput.text) }
    function focusName() { layerNameInput.forceActiveFocus(); layerNameInput.selectAll() }
    property string revealedLayerId: ""
    property string pendingRevealId: ""
    function revealFocusedLayer() {
        var lid = selection.pickedLayerId
        if (lid === revealedLayerId) return
        revealedLayerId = lid
        queueReveal(lid)
    }
    function queueReveal(lid) {
        pendingRevealId = lid
        if (!lid) { revealTimer.stop(); return }
        expanded = true
        revealTimer.restart()
    }
    function positionFocusedLayer() {
        var lid = pendingRevealId
        pendingRevealId = ""
        if (!lid || selection.pickedLayerId !== lid || !detailsVisible) return
        var rows = editor.layers
        for (var i = 0; i < rows.length; ++i) {
            if (rows[i].id === lid) {
                layerList.forceLayout()
                layerList.positionViewAtIndex(i, ListView.Contain)
                return
            }
        }
    }
    // Wait for the inspector's range/parameter layout to settle before scrolling.
    Timer { id: revealTimer; interval: 1; onTriggered: layerRoot.positionFocusedLayer() }
    Connections {
        target: selection
        function onChanged() { layerRoot.revealFocusedLayer() }
        function onLayerFocusRequested(lid) { layerRoot.queueReveal(lid) }
    }
    Component.onCompleted: revealFocusedLayer()
    property string menuLayerId: ""
    property var menuContext: ({ groups: [] })
    function refreshMenuContext() { menuContext = editor.layerContext(menuLayerId) }
    function showLayerMenu(lid, item, x, y) {
        menuLayerId = lid
        refreshMenuContext()
        var point = item.mapToItem(layerRoot, x, y)
        layerMenu.popup(layerRoot, point.x, point.y)
    }
    function runMenuAction(action, parentId) {
        var lid = menuLayerId
        layerMenu.dismiss()
        editor.runLayerAction(lid, action, parentId || "")
    }
    function pickMenuLayer() {
        if (!menuContext.pick) return false
        var lid = menuLayerId
        layerMenu.dismiss()
        selection.pickLayer(lid)
        return selection.pickedLayerId === lid && editor.activeLayerId === lid
    }
    Connections { target: editor; function onChanged() { if(layerMenu.visible) layerRoot.refreshMenuContext() } }
    Layout.fillWidth: true; Layout.preferredHeight: !detailsVisible ? 38 : rangeMode ? (workspace.height<800 ? 150 : 190) : (layerProperties.expanded ? (workspace.height<800 ? 310 : 360) : (workspace.height<800 ? 170 : 220))
    Layout.leftMargin: 12; Layout.rightMargin: 12; Layout.bottomMargin: 6; spacing: 5
    RowLayout { Layout.fillWidth: true; Layout.topMargin: 6
        Action { objectName: "collapseLayersButton"; text: detailsVisible ? "图层 -" : "图层 +"; subtle: true; font.bold: true; onClicked: { expanded=!expanded } }
        Item { Layout.fillWidth: true }
        Action { objectName: "groupLayerButton"; text: "编组"; hint: "将当前层放进一个新组 Ctrl+G；组可继续嵌套"; enabled: workspace.editingEnabled; implicitHeight: 25; onClicked: editor.groupLayer() }
        Action { objectName: "addLayerButton"; text: "＋ 调整层"; hint: "选中组时在组内新建"; enabled: workspace.editingEnabled; implicitHeight: 25; onClicked: editor.addGlobalLayer() }
    }
    Caption { visible: detailsVisible && rangeMode; text: "点击图层查看调整；再次点击返回范围"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
    ListView { id: layerList; objectName: "layerList"; visible: detailsVisible; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 3; model: editor.layerRowsModel
        onHeightChanged: if(layerRoot.pendingRevealId) revealTimer.restart()
        onContentHeightChanged: if(layerRoot.pendingRevealId) revealTimer.restart()
        ScrollBar.vertical: ScrollBar {}
        delegate: Rectangle { required property var layerRow; width: layerList.width; height: 40; radius: 3; color: selection.pickedLayerId===layerRow.id ? "#435c53" : "#252a30"
            RowLayout { anchors.fill: parent; anchors.margins: 4; spacing: 4
                Item { Layout.preferredWidth: layerRow.depth*12 }
                Action { text: layerRow.visible ? "显" : "隐"; hint: layerRow.effectiveVisible ? "显示 / 隐藏" : "自身或父组已隐藏"; opacity: layerRow.effectiveVisible ? 1 : .5; implicitWidth: 24; subtle: true; enabled: workspace.editingEnabled; onClicked: editor.toggleLayer(layerRow.id) }
                Action { objectName: "groupToggle_"+layerRow.id; text: layerRow.collapsed ? "+" : "-"; visible: layerRow.kind==="group"; implicitWidth: 22; subtle: true; enabled: !editor.busy; onClicked: editor.toggleGroup(layerRow.id) }
                Rectangle { visible: layerRow.kind!=="group"; Layout.preferredWidth: 33; Layout.preferredHeight: 26; color: "black"; border.color: "#9eb2a8"
                    Canvas {
                        anchors.fill: parent; anchors.margins: 2; visible: layerRow.hasPixelContent
                        onPaint: {
                            var c=getContext("2d"); c.reset()
                            for(var y=0;y<height;y+=6) for(var x=0;x<width;x+=6) {
                                c.fillStyle=((x/6+y/6)%2) ? "#777777" : "#bbbbbb"
                                c.fillRect(x,y,6,6)
                            }
                        }
                    }
                    Image {
                        id: layerThumb
                        objectName: (layerRow.hasPixelContent ? "layerContentThumb_" : "layerMaskThumb_")+layerRow.id
                        anchors.fill: parent; anchors.margins: 2; cache: false; fillMode: Image.PreserveAspectFit
                        readonly property string currentToken: layerRow.hasPixelContent ? "pixels:"+layerRow.contentToken : "mask:"+layerRow.maskToken
                        property string shownToken: ""
                        function refresh() {
                            if (shownToken === currentToken) return
                            shownToken = currentToken
                            source = layerRow.hasPixelContent ? editor.layerContentThumbnail(layerRow.id) : editor.layerMaskThumbnail(layerRow.id)
                        }
                        onCurrentTokenChanged: refresh()
                        Component.onCompleted: refresh()
                        Connections { target: editor; function onLayersChanged() { layerThumb.refresh() } }
                    }
                    MouseArea {
                        id: thumbMouse; anchors.fill: parent; enabled: workspace.editingEnabled; hoverEnabled: true
                        onClicked: {
                            editor.selection.pickLayer(layerRow.id)
                            if(layerRow.hasPixelContent) workspace.reviewAdjustments([layerRow.id])
                            else workspace.reviewMask()
                        }
                    }
                    ToolTip.visible: thumbMouse.containsMouse; ToolTip.delay: 600
                    ToolTip.text: layerRow.hasPixelContent ? "像素内容 · 点击放大查看实际效果" : "蒙版范围 · 点击修正范围"
                }
                Item { Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; spacing: 1
                        Text { text: layerRow.name; textFormat: Text.PlainText; color: Theme.ink; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                        Caption { text: layerRow.kind==="group" ? "组 · "+layerRow.childCount+" 项" : (layerRow.hasPixelContent ? "像素内容 · " : "")+layerRow.mask.label; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    MouseArea { objectName: "layerSelect_"+layerRow.id; anchors.fill: parent; enabled: workspace.editingEnabled; onClicked: editor.selection.pickedLayerId === layerRow.id ? editor.selection.clearPick() : editor.selection.pickLayer(layerRow.id) }
                }
            }
            MouseArea {
                id: rowMenuArea
                anchors.fill: parent
                acceptedButtons: Qt.RightButton
                onClicked: function(mouse) { layerRoot.showLayerMenu(layerRow.id, rowMenuArea, mouse.x, mouse.y) }
            }
        }
    }
    Menu {
        id: layerMenu; objectName: "layerContextMenu"; width: 260
        MenuItem { id: menuTargetLabel; objectName: "layerMenuTarget"; text: layerRoot.menuContext.name || "图层已不存在"; enabled: false
            contentItem: Text { text: menuTargetLabel.text; textFormat: Text.PlainText; font: menuTargetLabel.font; color: "#a9b7af"; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
        }
        MenuSeparator {}
        MenuItem { objectName: "layerMenuPick"; text: "编辑此层（调整）"; enabled: !!layerRoot.menuContext.pick; onTriggered: layerRoot.pickMenuLayer() }
        MenuItem { objectName: "layerMenuRange"; text: "载入蒙版为范围"; enabled: !!layerRoot.menuContext.range; onTriggered: { if(layerRoot.pickMenuLayer()) workspace.reviewMask() } }
        MenuItem { objectName: "layerMenuRename"; text: "重命名"; enabled: !!layerRoot.menuContext.rename; onTriggered: { if(layerRoot.pickMenuLayer()) layerRoot.focusName() } }
        MenuItem { objectName: "layerMenuDuplicate"; text: "复制层"; enabled: !!layerRoot.menuContext.duplicate; onTriggered: layerRoot.runMenuAction("duplicate") }
        MenuItem { objectName: "layerMenuToggle"; text: "显示 / 隐藏"; enabled: !!layerRoot.menuContext.toggle; onTriggered: layerRoot.runMenuAction("toggle") }
        MenuSeparator {}
        MenuItem { objectName: "layerMenuUp"; text: "上移"; enabled: !!layerRoot.menuContext.up; onTriggered: layerRoot.runMenuAction("up") }
        MenuItem { objectName: "layerMenuDown"; text: "下移"; enabled: !!layerRoot.menuContext.down; onTriggered: layerRoot.runMenuAction("down") }
        MenuItem { objectName: "layerMenuGroup"; text: "编组  Ctrl+G"; enabled: !!layerRoot.menuContext.group; onTriggered: layerRoot.runMenuAction("group") }
        MenuItem { objectName: "layerMenuUngroup"; text: "移出组"; enabled: !!layerRoot.menuContext.ungroup; onTriggered: layerRoot.runMenuAction("ungroup") }
        Menu {
            title: "移入组"; enabled: !!layerRoot.menuContext.move
            Repeater {
                model: layerRoot.menuContext.groups
                delegate: MenuItem { required property var modelData; objectName: "layerMenuMove_"+modelData.id; text: modelData.name; onTriggered: layerRoot.runMenuAction("move", modelData.id) }
            }
        }
        MenuSeparator {}
        MenuItem { objectName: "layerMenuDelete"; text: "删除（可撤销）"; enabled: !!layerRoot.menuContext.delete; onTriggered: layerRoot.runMenuAction("delete") }
    }
    FoldSection { id: layerProperties; objectName: "layerPropertiesSection"; title: "图层属性"; expanded: true; visible: layerRoot.detailsVisible && !layerRoot.rangeMode
    Field { id: layerNameInput; objectName: "layerNameInput"; visible: detailsVisible; Layout.fillWidth: true; implicitHeight: 26; text: editor.activeLayerName; enabled: workspace.editingEnabled; onEditingFinished: editor.renameLayer(text) }
    RowLayout { visible: detailsVisible && !editor.activeRepairInfo.isolated; Layout.fillWidth: true
        Caption { objectName: "layerOpacityLabel"; text: editor.activeIsGroup ? "整体强度" : editor.activeRepairInfo.count > 0 ? "图层强度" : "不透明度"; font.pixelSize: 10 }
        FineSlider { objectName: "layerOpacitySlider"; Layout.fillWidth: true; implicitHeight: 25; from: 0; to: 100; value: editor.layerOpacity; enabled: workspace.editingEnabled; onMoved: editor.setOpacity(value); onPressedChanged: if(!pressed) editor.finishGesture() }
        Caption { text: Math.round(editor.layerOpacity)+"%"; font.pixelSize: 10 }
    }
    Caption { visible: detailsVisible; text: "点击内容缩略图查看效果；点击蒙版缩略图修正范围。右键管理图层。"; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
    }
}
