import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// One refinement concept: an auto-routing primary action plus an explicit
// method menu; parameters shared by the range module and RegionPane.
ColumnLayout {
    id: root
    required property var workspace
    required property var editor
    property string prefix: ""
    property bool regionMode: false
    property bool showAdvanced: false
    readonly property var selection: editor.selection
    readonly property bool active: root.regionMode || root.editor.hasSelectionDraft
    Layout.fillWidth: true; spacing: 7

    RowLayout { Layout.fillWidth: true; spacing: 5
        Action {
            objectName: root.prefix+"refineAutoButton"; text: "自动细化"; primary: true; Layout.fillWidth: true
            hint: root.selection.autoRefineMethod==="details" ? "AI 按原图细化已有范围边缘，保留提示点；处理时可取消" : root.selection.autoRefineMethod!=="" ? "自动选择当前可用修边方法" : "没有可用的细化方法；请在扩展 → 图像能力中查看配置"
            enabled: root.active && !root.editor.busy && root.selection.autoRefineMethod!==""
            onClicked: root.selection.refine("auto", matteRadius.value)
        }
        Action { objectName: root.prefix+"refineAdvancedButton"; text: root.showAdvanced ? "收起高级" : "高级…"; subtle: true; enabled: !root.editor.busy; onClicked: root.showAdvanced = !root.showAdvanced }

    }
    Action {
        objectName: root.prefix+"channelMaskButton"; text: "通道抠图"; Layout.fillWidth: true
        visible: !root.regionMode; enabled: root.active && !root.editor.busy
        hint: "比较颜色与通道计算，调黑白场和灰度，结合 AI 保留发丝与透明边缘"
        onClicked: root.editor.channelMask.open()
    }
    ColumnLayout { visible: root.showAdvanced; Layout.fillWidth: true; spacing: 6
        Action {
            objectName: root.prefix+"refineMethodMenuButton"; text: "选方法"; subtle: true
            hint: "手动选择细化方法"
            enabled: root.active && !root.editor.busy
            onClicked: refineMenu.popup()
            Menu {
                id: refineMenu
                Repeater {
                    model: root.selection.refineMethods
                    delegate: MenuItem {
                        required property var modelData
                        text: modelData.name + (modelData.available ? "" : "（未配置）") + (root.regionMode && modelData.id==="sam" ? "（仅独立选区）" : "")
                        enabled: modelData.available && root.active && !root.editor.busy && !(root.regionMode && modelData.id==="sam")
                        onTriggered: root.selection.refine(modelData.id, matteRadius.value)
                        ToolTip.visible: hovered; ToolTip.text: modelData.description; ToolTip.delay: 400
                    }
                }
            }
        }
        Action { objectName: root.prefix+"correctPixelPointsButton"; text: "保留 / 排除点"; visible: !root.regionMode; Layout.fillWidth: true; enabled: root.active && !root.editor.busy; hint: "点击目标内部保留，Alt 点击排除"; onClicked: root.selection.refinePixelPoints() }
        RowLayout { Layout.fillWidth: true
            Caption { text: "边缘范围"; Layout.fillWidth: true }
            SpinBox { id: matteRadius; objectName: root.prefix+"matteRadiusBox"; from: 1; to: 64; value: 8; enabled: !root.editor.busy; implicitWidth: 110; implicitHeight: 30 }
            Caption { text: "原图 px" }
        }
        Action {
            objectName: root.prefix+"refineMatteButton"; text: "按原图细化边缘"; Layout.fillWidth: true
            hint: "透明边缘：按原图分辨率估计连续透明度，保留发丝等半透明过渡"
            enabled: root.active && !root.editor.busy && root.editor.matteAvailable
            onClicked: root.selection.refine("matte", matteRadius.value)
        }
        Action {
            objectName: root.prefix+"refineSelectionButton"; text: "重新识别轮廓"; visible: !root.regionMode; Layout.fillWidth: true
            hint: "以当前选区为提示，用像素模型重新分割；人脸范围保留五官保护和人脸关联"
            enabled: root.active && !root.editor.busy && root.editor.pixelAvailable
            onClicked: root.selection.refine("sam")
        }
        ColumnLayout { visible: !root.regionMode; Layout.fillWidth: true; spacing: 6
            RowLayout { Layout.fillWidth: true
                Caption { text: "内羽化" }
                FineSlider { from: 0; to: 5; stepSize: .1; value: root.editor.draftFeather; Layout.fillWidth: true; enabled: root.active && !root.editor.busy; onMoved: root.editor.setDraftFeather(value); onPressedChanged: if(!pressed) root.editor.finishSelectionGesture() }
                Caption { text: root.editor.draftFeather.toFixed(1)+"%" }
            }
            RowLayout { Layout.fillWidth: true
                Caption { text: "边缘位移" }
                FineSlider { objectName: root.prefix+"edgeShiftSlider"; from: -5; to: 5; stepSize: 1; value: root.editor.edgeShift; Layout.fillWidth: true; enabled: root.active && !root.editor.busy; onMoved: root.editor.setEdgeShift(value); onPressedChanged: if(!pressed) root.editor.finishSelectionGesture() }
                Caption { text: (root.editor.edgeShift>0 ? "+" : "")+root.editor.edgeShift+"%" }
            }
            Caption { text: "边缘位移按短边百分比收缩（负）或扩展（正）选区；羽化使其过渡柔和。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10 }

        }
    }
    Caption { visible: root.editor.selectionQuality.length>0; text: root.editor.selectionQuality; wrapMode: Text.Wrap; color: "#c6d9ca"; Layout.fillWidth: true; font.pixelSize: 10 }

}
