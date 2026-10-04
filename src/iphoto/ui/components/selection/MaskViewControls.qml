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
        model: ["绿色覆盖","黑白透明度","调色效果","白底抠图","黑底抠图"]
        currentIndex: ["overlay","grayscale","adjustment","white","black"].indexOf(root.editor.maskView)
        implicitHeight: 30
        onActivated: root.editor.selection.setMaskView(["overlay","grayscale","adjustment","white","black"][currentIndex])
    }
}
