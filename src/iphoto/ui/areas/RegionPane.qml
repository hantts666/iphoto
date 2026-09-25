import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    required property var workspace
    required property var editor
 visible: editor.hasRegionDraft; Layout.fillWidth: true; spacing: 10
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
    Action { text: "优化当前区域边缘"; enabled: !editor.busy; Layout.fillWidth: true; onClicked: editor.refineSelection("grabcut") }
    Action { text: "细化当前区域透明边缘"; enabled: !editor.busy && editor.matteAvailable; Layout.fillWidth: true; onClicked: editor.refineMatte(8) }
    Action { text: "取消边缘细化"; visible: editor.matteBusy; onClicked: editor.cancelMatte() }
    Caption { text: "创建图层后，可单独载入每层蒙版继续补选或擦除。"; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.bottomMargin: 12 }
}
