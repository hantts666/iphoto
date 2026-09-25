import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

TextField {
    implicitHeight: 34; color: Theme.ink; placeholderTextColor: "#909aa6"; selectByMouse: true; leftPadding: 9
    background: Rectangle { radius: 4; color: "#22262b"; border.color: parent.activeFocus ? "#73ac98" : Theme.line }
}
