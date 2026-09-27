import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Dark-themed ComboBox: the Basic style popup highlighted/hovered rows used
// palette colors that made item text unreadable on this theme.
ComboBox {
    id: control
    implicitHeight: 30
    // Basic style derives a huge implicitWidth from popup content; cap it so
    // RowLayout parents keep their fillWidth siblings inside the column.
    implicitWidth: 120
    contentItem: Text {
        leftPadding: 8
        text: control.displayText
        color: "#e4e8ee"
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle { radius: 4; color: control.pressed ? "#3a4149" : "#363c44"; border.color: "#49515b" }
    popup: Popup {
        y: control.height - 1
        width: control.width
        implicitHeight: contentItem.implicitHeight + 10
        padding: 1
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle { radius: 4; color: "#262b31"; border.color: "#49515b" }
    }
    delegate: MenuItem {
        required property var modelData
        required property int index
        text: control.textRole ? modelData[control.textRole] : modelData
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            leftPadding: 8
            text: parent.text
            color: "#e4e8ee"
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle { implicitHeight: 30; color: parent.highlighted ? "#467e71" : parent.hovered ? "#343b43" : "transparent" }
    }
}
