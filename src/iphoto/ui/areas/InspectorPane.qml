import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    required property var workspace
    required property var editor
    function commitText() { layersPane.commitText() }
    function focusSelectionInput() { selectionPane.focusPrompt() }
 id: inspectorRoot; Layout.preferredWidth: 316; Layout.fillHeight: true; color: workspace.panel
    ColumnLayout { anchors.fill: parent; spacing: 0
        RowLayout { Layout.fillWidth: true; Layout.preferredHeight: 40; Layout.leftMargin: 12; Layout.rightMargin: 12
            Action { objectName: "adjustmentsTab"; text: "调整"; primary: workspace.inspectorPage===0 && !editor.hasRegionDraft; Layout.fillWidth: true; onClicked: workspace.inspectorPage=0 }
            Action { objectName: "sceneTab"; text: "元素"; primary: workspace.inspectorPage===2 && !editor.hasRegionDraft; Layout.fillWidth: true; onClicked: { workspace.inspectorPage=2; if(editor.sceneObjects.length) workspace.chooseTool("object") } }
            Action { objectName: "selectionTab"; text: "选区"; primary: workspace.inspectorPage===1 || editor.hasRegionDraft; Layout.fillWidth: true; onClicked: workspace.inspectorPage=1 }
        }
        ScrollView { id: propertyScroll; objectName: "propertyScroll"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout { width: propertyScroll.availableWidth-28; x: 14; spacing: 11
                AdjustmentPane { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
                SelectionPane { id: selectionPane; workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
                RegionPane { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
                ScenePane { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
            }
        }
        Rectangle { visible: workspace.inspectorPage===2 && !editor.hasRegionDraft && editor.sceneObjects.length>0; Layout.fillWidth: true; Layout.preferredHeight: 70; color: "#303e38"
            ColumnLayout { anchors.fill: parent; anchors.margins: 8; spacing: 4
                Caption { text: "已勾选 "+editor.checkedObjectCount+" 项 · 对象轮廓为草稿" }
                RowLayout { Layout.fillWidth: true; spacing: 4
                    Action { objectName: "combineObjectsButton"; text: "新选区"; primary: true; Layout.fillWidth: true; enabled: !editor.busy && editor.checkedObjectCount>0; onClicked: editor.combineObjects("replace") }
                    Action { objectName: "addObjectsButton"; text: "添加"; enabled: !editor.busy && editor.hasSelectionDraft && editor.checkedObjectCount>0; onClicked: editor.combineObjects("add") }
                    Action { objectName: "subtractObjectsButton"; text: "减去"; enabled: !editor.busy && editor.hasSelectionDraft && editor.checkedObjectCount>0; onClicked: editor.combineObjects("subtract") }
                    Action { objectName: "intersectObjectsButton"; text: "相交"; enabled: !editor.busy && editor.hasSelectionDraft && editor.checkedObjectCount>0; onClicked: editor.combineObjects("intersect") }
                }
            }
        }
        Rectangle { visible: editor.hasSelectionDraft || editor.hasRegionDraft; Layout.fillWidth: true; Layout.preferredHeight: 91; color: "#35443e"
            ColumnLayout { anchors.fill: parent; anchors.margins: 10; spacing: 6
                Action { objectName: "selectionToLayerButton"; text: editor.hasRegionDraft ? "确认并创建分区图层" : "选区 → 新建调整层"; Layout.fillWidth: true; primary: true; enabled: !editor.busy && workspace.selectionPreviewReady && workspace.previewReady; onClicked: editor.hasRegionDraft ? editor.acceptRegions() : editor.selectionToLayer() }
                RowLayout { Layout.fillWidth: true
                    Action { objectName: "acceptSelectionButton"; text: "替换当前层蒙版"; visible: editor.hasSelectionDraft; Layout.fillWidth: true; enabled: !editor.busy && workspace.selectionPreviewReady; onClicked: editor.acceptSelection() }
                    Action { objectName: "discardSelectionButton"; text: "取消"; Layout.fillWidth: true; enabled: !editor.busy; onClicked: editor.hasRegionDraft ? editor.discardRegions() : editor.discardSelection() }
                }
            }
        }
        Divider {}
        LayersPane { id: layersPane; workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
    }
}
