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
    readonly property bool compact: editor.hasSelectionDraft || editor.hasRegionDraft || workspace.inspectorPage===2
    readonly property bool detailsVisible: expanded
    function commitText() { if(layerNameInput.activeFocus) editor.renameLayer(layerNameInput.text) }
    Layout.fillWidth: true; Layout.preferredHeight: detailsVisible ? (layerProperties.expanded ? (workspace.height<800 ? 310 : 360) : (workspace.height<800 ? 170 : 220)) : 38
    Layout.leftMargin: 12; Layout.rightMargin: 12; Layout.bottomMargin: 6; spacing: 5
    RowLayout { Layout.fillWidth: true; Layout.topMargin: 6
        Action { objectName: "collapseLayersButton"; text: detailsVisible ? "▾ 图层" : "▸ 图层"; subtle: true; font.bold: true; onClicked: { expanded=!expanded } }
        Item { Layout.fillWidth: true }
        Action { objectName: "groupLayerButton"; text: "编组"; hint: "将当前层放进一个新组 Ctrl+G；组可继续嵌套"; enabled: workspace.editingEnabled; implicitHeight: 25; onClicked: editor.groupLayer() }
        Action { objectName: "addLayerButton"; text: "＋ 调整层"; hint: "选中组时在组内新建"; enabled: workspace.editingEnabled; implicitHeight: 25; onClicked: editor.addGlobalLayer() }
    }
    ListView { id: layerList; objectName: "layerList"; visible: detailsVisible; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 3; model: editor.layers
        ScrollBar.vertical: ScrollBar {}
        delegate: Rectangle { required property var modelData; width: layerList.width; height: 40; radius: 3; color: editor.activeLayerId===modelData.id ? "#435c53" : "#252a30"
            RowLayout { anchors.fill: parent; anchors.margins: 4; spacing: 4
                Item { Layout.preferredWidth: modelData.depth*12 }
                Action { text: modelData.visible ? "显" : "隐"; hint: modelData.effectiveVisible ? "显示 / 隐藏" : "自身或父组已隐藏"; opacity: modelData.effectiveVisible ? 1 : .5; implicitWidth: 24; subtle: true; enabled: workspace.editingEnabled; onClicked: editor.toggleLayer(modelData.id) }
                Action { objectName: "groupToggle_"+modelData.id; text: modelData.collapsed ? "+" : "-"; visible: modelData.kind==="group"; implicitWidth: 22; subtle: true; enabled: !editor.busy; onClicked: editor.toggleGroup(modelData.id) }
                Rectangle { visible: modelData.kind!=="group"; Layout.preferredWidth: 33; Layout.preferredHeight: 26; color: "black"; border.color: "#9eb2a8"
                    Image { id: layerThumb; anchors.fill: parent; anchors.margins: 2; source: editor.layerMaskThumbnail(modelData.id); cache: false
                        Connections { target: editor; function onChanged() { layerThumb.source=editor.layerMaskThumbnail(modelData.id) } }
                    }
                    MouseArea { anchors.fill: parent; enabled: workspace.editingEnabled; onClicked: { editor.selectLayer(modelData.id); workspace.reviewMask() } }
                }
                Item { Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; spacing: 1
                        Text { text: modelData.name; textFormat: Text.PlainText; color: Theme.ink; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                        Caption { text: modelData.kind==="group" ? "组 · "+modelData.childCount+" 项" : modelData.mask.label; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    MouseArea { objectName: "layerSelect_"+modelData.id; anchors.fill: parent; enabled: workspace.editingEnabled; onClicked: editor.selectLayer(modelData.id) }
                }
            }
        }
    }
    FoldSection { id: layerProperties; objectName: "layerPropertiesSection"; title: "图层属性"; expanded: false; visible: layerRoot.detailsVisible
    Field { id: layerNameInput; objectName: "layerNameInput"; visible: detailsVisible; Layout.fillWidth: true; implicitHeight: 26; text: editor.activeLayerName; enabled: workspace.editingEnabled; onEditingFinished: editor.renameLayer(text) }
    RowLayout { visible: detailsVisible; Layout.fillWidth: true
        Caption { text: "移入"; font.pixelSize: 10 }
        ComboBox { id: targetGroup; objectName: "groupTargetBox"; model: editor.groupTargets; textRole: "name"; Layout.fillWidth: true; implicitHeight: 26; enabled: workspace.editingEnabled }
        Action { objectName: "moveToGroupButton"; text: "确定"; implicitHeight: 26; enabled: workspace.editingEnabled; onClicked: editor.moveToGroup(editor.groupTargets[targetGroup.currentIndex].id) }
        Action { objectName: "moveOutGroupButton"; text: "移出"; implicitHeight: 26; enabled: workspace.editingEnabled && editor.activeParentId!==""; onClicked: editor.moveOutOfGroup() }
    }
    RowLayout { visible: detailsVisible; Layout.fillWidth: true
        Caption { text: "透明度"; font.pixelSize: 10 }
        FineSlider { objectName: "layerOpacitySlider"; Layout.fillWidth: true; implicitHeight: 25; from: 0; to: 100; value: editor.layerOpacity; enabled: workspace.editingEnabled; onMoved: editor.setOpacity(value); onPressedChanged: if(!pressed) editor.finishGesture() }
        Caption { text: Math.round(editor.layerOpacity)+"%"; font.pixelSize: 10 }
        Action { text: "↑"; implicitWidth: 25; implicitHeight: 25; enabled: workspace.editingEnabled; onClicked: editor.moveLayer(1) }
        Action { text: "↓"; implicitWidth: 25; implicitHeight: 25; enabled: workspace.editingEnabled; onClicked: editor.moveLayer(-1) }
        Action { text: "删"; implicitWidth: 25; implicitHeight: 25; hint: "删除组会删除组内各层，可撤销"; enabled: workspace.editingEnabled; onClicked: editor.deleteLayer() }
    }
    Caption { visible: detailsVisible; text: editor.activeIsGroup ? "组内先合成，再应用组蒙版和透明度" : "点击蒙版修正 · 组支持四级嵌套"; font.pixelSize: 9; Layout.fillWidth: true; elide: Text.ElideRight }
    }
}
