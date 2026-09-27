import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Tool-context options bound to the SelectionController: one source of truth
// for mode / brush size / wand tolerance / pixel prompt controls.
RowLayout {
    id: root
    required property var workspace
    required property var editor
    signal needsFocus()
    readonly property var selection: editor.selection
    spacing: 8
    Caption { text: ({smart:"像素点选",object:"对象点选",inspect:"浏览 / 平移",hand:"抓手 / 平移",zoom:"缩放工具",rect:"矩形选框",ellipse:"椭圆选框",polygon:"自由套索",brush:"蒙版画笔",wand:"颜色魔棒",heal:"修复画笔"})[root.selection.tool]; Layout.preferredWidth: 104; color: root.workspace.ink }
    RowLayout { visible: !root.selection.navigationTool && root.selection.tool!=="smart" && root.selection.tool!=="heal"; spacing: 5
        Repeater { model: [{key:"replace",label:"新选区"},{key:"add",label:"＋ 添加"},{key:"subtract",label:"－ 减去"}]
            delegate: Action { required property var modelData; text: modelData.label; primary: root.selection.mode===modelData.key; onClicked: root.selection.setMode(modelData.key) }
        }
        Caption { visible: root.selection.tool==="brush"; text: "笔刷" }
        FineSlider { visible: root.selection.tool==="brush" || root.selection.tool==="heal"; Layout.preferredWidth: 85; from: .003; to: .15; value: root.selection.brushRadius; onMoved: root.selection.setBrushRadius(value) }
        Caption { visible: root.selection.tool==="wand"; text: "容差" }
        SpinBox { visible: root.selection.tool==="wand"; from: 0; to: 100; value: root.selection.wandTolerance; implicitHeight: 30; implicitWidth: 88; onValueModified: root.selection.setWandTolerance(value) }
    }
    RowLayout { visible: root.selection.tool==="heal"; spacing: 5
        Caption { text: "在瑕疵上拖动 · 松开即用周围内容修复 · 可撤销"; font.pixelSize: 11 }
    }
    RowLayout { visible: root.selection.tool==="smart"; spacing: 5
        Caption { text: "点目标内部保留 · Alt 点排除 · 已记 "+root.editor.pixelPoints.length+"/6 点"; font.pixelSize: 11 }
        Action { objectName: "newPixelTargetButton"; text: "新目标"; enabled: !root.editor.busy; onClicked: { root.selection.newPixelTarget(); root.needsFocus() } }
        Action { objectName: "undoPixelPointButton"; text: "退一点"; enabled: !root.editor.busy && root.editor.pixelPoints.length>0; onClicked: { root.editor.undoPixelPoint(); root.needsFocus() } }
    }
    Caption { visible: root.selection.navigationTool; text: root.selection.tool==="zoom" ? "单击放大 · Alt 缩小 · 左右拖动连续缩放" : "空格临时抓手 · H 拖动 · Z 缩放"; Layout.fillWidth: true; elide: Text.ElideRight }
}
