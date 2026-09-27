import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "../components/selection"

// Navigation is state-driven, no tabs: a picked layer shows its adjustments,
// no picked layer shows the unified range module (sources + scene elements).
Rectangle {
    required property var workspace
    required property var editor
    readonly property var selection: editor.selection
    readonly property bool rangeMode: selection.pickedLayerId === "" && !workspace.regionModal
    function commitText() { layersPane.commitText() }
    function focusSelectionInput() { rangePane.focusPrompt() }
    function showRefine() {
        if (!rangeMode || !rangePane.refineCard.visible) return
        var flick = propertyScroll.contentItem
        var top = rangePane.refineCard.mapToItem(propertyScroll, 0, 0).y
        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height,
                                             flick.contentY + top - propertyScroll.height * 0.12))
    }
 id: inspectorRoot; Layout.preferredWidth: 316; Layout.fillHeight: true; color: workspace.panel
    ColumnLayout { anchors.fill: parent; spacing: 0
        Rectangle { visible: workspace.regionModal; Layout.fillWidth: true; Layout.preferredHeight: 30; color: "#35443e"
            Caption { anchors.centerIn: parent; text: "分区草稿进行中 · 确认或取消后返回" }
        }
        Divider {}
        ScrollView { id: propertyScroll; objectName: "propertyScroll"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout { width: propertyScroll.availableWidth-28; x: 14; spacing: 11
                AdjustmentPane { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor; visible: editor.selection.pickedLayerId !== "" && !workspace.regionModal }
                SelectionGuide { id: rangePane; workspace: inspectorRoot.workspace; editor: inspectorRoot.editor; visible: inspectorRoot.rangeMode }
                RegionPane { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
            }
        }
        Rectangle { visible: inspectorRoot.rangeMode && editor.sceneObjects.length>0; Layout.fillWidth: true; Layout.preferredHeight: 70; color: "#303e38"
            ColumnLayout { anchors.fill: parent; anchors.margins: 8; spacing: 4
                Caption { text: editor.checkedObjectCount>0 ? "已勾选 "+editor.checkedObjectCount+" 项 · 可直接开始局部调整" : "勾选画面元素后，在这里开始调整" }
                RowLayout { Layout.fillWidth: true; spacing: 4
                    Action { objectName: "adjustCheckedObjectsButton"; text: "调整已选对象"; primary: true; Layout.fillWidth: true; enabled: !editor.busy && editor.checkedObjectCount>0; hint: "自动识别勾选对象的边缘，并直接打开可撤销的局部调整"; onClicked: editor.adjustCheckedObjects() }
                    Action { objectName: "objectCombineMenuButton"; text: "组合…"; subtle: true; enabled: !editor.busy && editor.checkedObjectCount>0; hint: "需要先修边或与已有范围组合时使用"; onClicked: objectCombineMenu.popup() }
                }
            }
            Menu {
                id: objectCombineMenu
                MenuItem { objectName: "combineObjectsButton"; text: "只建立范围，先修边"; onTriggered: editor.combineObjects("replace") }
                MenuItem { objectName: "addObjectsButton"; text: "加入当前范围"; enabled: editor.hasSelectionDraft; onTriggered: editor.combineObjects("add") }
                MenuItem { objectName: "subtractObjectsButton"; text: "从当前范围减去"; enabled: editor.hasSelectionDraft; onTriggered: editor.combineObjects("subtract") }
                MenuItem { objectName: "intersectObjectsButton"; text: "仅保留交集"; enabled: editor.hasSelectionDraft; onTriggered: editor.combineObjects("intersect") }
            }
        }
        DraftOutputBar { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor; onRefineRequested: inspectorRoot.showRefine() }
        Divider {}
        LayersPane { id: layersPane; workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
    }
}
