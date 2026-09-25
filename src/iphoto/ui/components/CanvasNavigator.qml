import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    required property var navigation
    required property string previewUrl
    width: 168; height: 128; radius: 5
    color: "#e62b3037"; border.color: "#616b78"
    Text { x: 10; y: 6; text: "导航 · 点击或拖动定位"; color: "#c6d0db"; font.pixelSize: 10 }
    Image {
        id: thumbnail
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: parent.bottom; margins: 9; topMargin: 25 }
        source: root.previewUrl; asynchronous: true; cache: false
        fillMode: Image.PreserveAspectFit
        Item {
            id: preview
            anchors.centerIn: parent
            width: thumbnail.paintedWidth; height: thumbnail.paintedHeight
            Rectangle {
                property var region: root.navigation.visibleRect
                x: region[0]*preview.width; y: region[1]*preview.height
                width: Math.max(2,region[2]*preview.width); height: Math.max(2,region[3]*preview.height)
                color: "#206fb9a0"; border.color: "#d4fff1"; border.width: 1
            }
            MouseArea {
                objectName: "navigatorMouse"
                anchors.fill: parent; preventStealing: true; cursorShape: Qt.OpenHandCursor
                function locate(mouse) { if (width > 0 && height > 0) root.navigation.centerOn(mouse.x/width, mouse.y/height) }
                onPressed: function(mouse) { locate(mouse) }
                onPositionChanged: function(mouse) { if (pressed) locate(mouse) }
            }
        }
    }
}
