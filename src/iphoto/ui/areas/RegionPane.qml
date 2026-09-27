import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "../components/selection"

ColumnLayout {
    id: regionRoot
    required property var workspace
    required property var editor
    visible: workspace.regionModal; Layout.fillWidth: true; spacing: 10
    Text { text: "AI 分区方案"; color: workspace.ink; font.bold: true; font.pixelSize: 15 }
    Caption { text: editor.regionSummary; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Caption { text: "画布预览包含勾选区域的调整。点击区域查看蒙版；确认前原图层保持不变。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Repeater { model: editor.regionDrafts
        delegate: Rectangle { required property var modelData; Layout.fillWidth: true; implicitHeight: regionInfo.implicitHeight+16; radius: 4; color: modelData.selected ? "#405b50" : "#252b31"
            ColumnLayout { id: regionInfo; x: 8; y: 8; width: parent.width-16; spacing: 5
                RowLayout { Layout.fillWidth: true
                    CheckBox { checked: modelData.visible; enabled: !editor.busy; onClicked: editor.toggleRegion(modelData.index) }
                    Action { text: modelData.name; subtle: true; Layout.fillWidth: true; enabled: !editor.busy; onClicked: editor.selectRegion(modelData.index) }
                }
                Caption { text: modelData.changes || "无参数变化"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Caption { text: modelData.quality; font.pixelSize: 10; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
        }
    }
    TaskStatusBar { editor: regionRoot.editor; prefix: "region" }
    RefineControls { workspace: regionRoot.workspace; editor: regionRoot.editor; regionMode: true; prefix: "region" }
    Caption { text: "细化作用于当前选中的区域。创建图层后，可单独载入每层蒙版继续补选或擦除。"; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.bottomMargin: 12 }
}
