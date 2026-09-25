import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property bool primary: false
    property bool subtle: false
    property string hint: text
    implicitHeight: 30; implicitWidth: Math.max(30,label.implicitWidth+18)
    opacity: enabled ? 1 : .38
    background: Rectangle { radius: 4; color: control.primary ? (control.down ? "#365e54" : "#467e71") : control.hovered ? "#454c57" : control.subtle ? "transparent" : "#363c44"; border.color: control.primary || control.subtle ? "transparent" : "#49515b" }
    contentItem: Text { id: label; text: control.text; font: control.font; color: Theme.ink; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter }
    ToolTip.visible: hovered; ToolTip.text: hint; ToolTip.delay: 500
}
