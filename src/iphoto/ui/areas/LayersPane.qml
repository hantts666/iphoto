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
    property string menuLayerId: ""
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
        ScrollBar.vertical: ScrollBar {}
        delegate: Rectangle { required property var layerRow; width: layerList.width; height: 40; radius: 3; color: selection.pickedLayerId===layerRow.id ? "#435c53" : "#252a30"
            RowLayout { anchors.fill: parent; anchors.margins: 4; spacing: 4
                Item { Layout.preferredWidth: layerRow.depth*12 }
                Action { text: layerRow.visible ? "显" : "隐"; hint: layerRow.effectiveVisible ? "显示 / 隐藏" : "自身或父组已隐藏"; opacity: layerRow.effectiveVisible ? 1 : .5; implicitWidth: 24; subtle: true; enabled: workspace.editingEnabled; onClicked: editor.toggleLayer(layerRow.id) }
                Action { objectName: "groupToggle_"+layerRow.id; text: layerRow.collapsed ? "+" : "-"; visible: layerRow.kind==="group"; implicitWidth: 22; subtle: true; enabled: !editor.busy; onClicked: editor.toggleGroup(layerRow.id) }
                Rectangle { visible: layerRow.kind!=="group"; Layout.preferredWidth: 33; Layout.preferredHeight: 26; color: "black"; border.color: "#9eb2a8"
                    Image {
                        id: layerThumb
                        objectName: "layerMaskThumb_"+layerRow.id
                        anchors.fill: parent; anchors.margins: 2; cache: false
                        property var shownMaskToken: null
                        function refresh() {
                            if (shownMaskToken === layerRow.maskToken) return
                            shownMaskToken = layerRow.maskToken
                            source = editor.layerMaskThumbnail(layerRow.id)
                        }
                        Component.onCompleted: refresh()
                        Connections { target: editor; function onLayersChanged() { layerThumb.refresh() } }
                    }
                    MouseArea { anchors.fill: parent; enabled: workspace.editingEnabled; onClicked: { editor.selection.pickLayer(layerRow.id); workspace.reviewMask() } }
                }
                Item { Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; spacing: 1
                        Text { text: layerRow.name; textFormat: Text.PlainText; color: Theme.ink; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                        Caption { text: layerRow.kind==="group" ? "组 · "+layerRow.childCount+" 项" : layerRow.mask.label; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    MouseArea { objectName: "layerSelect_"+layerRow.id; anchors.fill: parent; enabled: workspace.editingEnabled; onClicked: editor.selection.pickedLayerId === layerRow.id ? editor.selection.clearPick() : editor.selection.pickLayer(layerRow.id) }
                }
            }
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.RightButton
                onClicked: function(mouse) { layerRoot.menuLayerId = layerRow.id; layerMenu.popup() }
            }
        }
    }
    Menu {
        id: layerMenu
        MenuItem { objectName: "layerMenuPick"; text: "编辑此层（调整）"; onTriggered: selection.pickLayer(layerRoot.menuLayerId) }
        MenuItem { objectName: "layerMenuRange"; text: "载入蒙版为范围"; onTriggered: { selection.pickLayer(layerRoot.menuLayerId); workspace.reviewMask() } }
        MenuItem { objectName: "layerMenuRename"; text: "重命名"; onTriggered: { selection.pickLayer(layerRoot.menuLayerId); layerRoot.focusName() } }
        MenuItem { objectName: "layerMenuDuplicate"; text: "复制层"; onTriggered: { editor.selectLayer(layerRoot.menuLayerId); editor.duplicateLayer() } }
        MenuItem { objectName: "layerMenuToggle"; text: "显示 / 隐藏"; onTriggered: editor.toggleLayer(layerRoot.menuLayerId) }
        MenuSeparator {}
        MenuItem { objectName: "layerMenuUp"; text: "上移"; onTriggered: { editor.selectLayer(layerRoot.menuLayerId); editor.moveLayer(1) } }
        MenuItem { objectName: "layerMenuDown"; text: "下移"; onTriggered: { editor.selectLayer(layerRoot.menuLayerId); editor.moveLayer(-1) } }
        MenuItem { objectName: "layerMenuGroup"; text: "编组  Ctrl+G"; onTriggered: { editor.selectLayer(layerRoot.menuLayerId); editor.groupLayer() } }
        MenuItem { objectName: "layerMenuUngroup"; text: "移出组"; enabled: editor.activeParentId !== ""; onTriggered: { editor.selectLayer(layerRoot.menuLayerId); editor.moveOutOfGroup() } }
        Menu {
            title: "移入组"
            Repeater {
                model: editor.groupTargets
                delegate: MenuItem { required property var modelData; text: modelData.name; onTriggered: { editor.selectLayer(layerRoot.menuLayerId); editor.moveToGroup(modelData.id) } }
            }
        }
        MenuSeparator {}
        MenuItem { objectName: "layerMenuDelete"; text: "删除（可撤销）"; onTriggered: { editor.selectLayer(layerRoot.menuLayerId); editor.deleteLayer() } }
    }
    FoldSection { id: layerProperties; objectName: "layerPropertiesSection"; title: "图层属性"; expanded: true; visible: layerRoot.detailsVisible && !layerRoot.rangeMode
    Field { id: layerNameInput; objectName: "layerNameInput"; visible: detailsVisible; Layout.fillWidth: true; implicitHeight: 26; text: editor.activeLayerName; enabled: workspace.editingEnabled; onEditingFinished: editor.renameLayer(text) }
    RowLayout { visible: detailsVisible; Layout.fillWidth: true
        Caption { text: "透明度"; font.pixelSize: 10 }
        FineSlider { objectName: "layerOpacitySlider"; Layout.fillWidth: true; implicitHeight: 25; from: 0; to: 100; value: editor.layerOpacity; enabled: workspace.editingEnabled; onMoved: editor.setOpacity(value); onPressedChanged: if(!pressed) editor.finishGesture() }
        Caption { text: Math.round(editor.layerOpacity)+"%"; font.pixelSize: 10 }
    }
    Caption { visible: detailsVisible; text: "右键图层：复制 / 删除 / 编组 / 移动 / 载入蒙版。点击蒙版缩略图修正范围。"; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
    }
}
