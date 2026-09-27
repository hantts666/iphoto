import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

RowLayout {
    id: root
    required property var editor
    property string prefix: ""
    Layout.fillWidth: true
    Caption { text: "查看" }
    SelectBox {
        id: viewBox
        objectName: root.prefix+"maskViewBox"
        Layout.fillWidth: true
        enabled: !root.editor.busy
        model: ["绿色覆盖","黑白透明度","调色效果"]
        currentIndex: root.editor.maskView==="adjustment" ? 2 : root.editor.maskView==="grayscale" ? 1 : 0
        implicitHeight: 30
        onActivated: root.editor.selection.setMaskView(currentIndex===2 ? "adjustment" : currentIndex===1 ? "grayscale" : "overlay")
    }
}
