import QtQuick
import QtQuick.Window

Rectangle {
    id: root
    required property var navigation
    required property string previewUrl
    required property string originalUrl
    readonly property bool previewReady: thumbnailLoader.item !== null && thumbnailLoader.item.status === Image.Ready
    readonly property bool previewFailed: thumbnailLoader.item !== null && thumbnailLoader.item.status === Image.Error
    readonly property bool useOriginal: thumbnailLoader.item === null || !thumbnailLoader.item.hasFrame || previewFailed
    readonly property bool imageAvailable: useOriginal ? original.status === Image.Ready : thumbnailLoader.item.hasFrame
    width: 168; height: 128; radius: 5
    color: "#e62b3037"; border.color: "#616b78"
    Text { x: 10; y: 6; text: root.useOriginal ? "导航 · 原图定位" : "导航 · 点击或拖动定位"; color: "#c6d0db"; font.pixelSize: 10 }
    Item {
        id: content
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: parent.bottom; margins: 9; topMargin: 25 }
        Item {
            id: preview
            // Geometry belongs to the source photo, independently of a pending texture.
            readonly property real aspect: root.navigation.imageHeight > 0 ? root.navigation.imageWidth/root.navigation.imageHeight : 1
            readonly property int pixelWidth: Math.ceil(width*root.Screen.devicePixelRatio)
            readonly property int pixelHeight: Math.ceil(height*root.Screen.devicePixelRatio)
            anchors.centerIn: parent
            width: Math.min(content.width, content.height*aspect)
            height: Math.min(content.height, content.width/aspect)
            Loader {
                id: thumbnailLoader
                anchors.fill: parent
                // A retained frame belongs to one opened photo, just like the canvas.
                property string photoIdentity: root.originalUrl
                onPhotoIdentityChanged: { active=false; active=true }
                sourceComponent: Image {
                    objectName: "navigatorPreviewImage"
                    source: root.previewUrl; asynchronous: true; cache: false; retainWhileLoading: true
                    sourceSize: Qt.size(preview.pixelWidth, preview.pixelHeight)
                    property bool hasFrame: false
                    onStatusChanged: {
                        if (status === Image.Ready) hasFrame = true
                        else if (status === Image.Error || status === Image.Null) hasFrame = false
                    }
                    visible: !root.useOriginal
                }
            }
            Image {
                id: original
                objectName: "navigatorOriginalImage"
                anchors.fill: parent
                source: root.originalUrl; asynchronous: true; cache: false
                sourceSize: Qt.size(preview.pixelWidth, preview.pixelHeight)
                visible: root.useOriginal
            }
            Rectangle {
                property var region: root.navigation.visibleRect
                visible: root.imageAvailable
                x: region[0]*preview.width; y: region[1]*preview.height
                width: Math.max(2,region[2]*preview.width); height: Math.max(2,region[3]*preview.height)
                color: "#206fb9a0"; border.color: "#d4fff1"; border.width: 1
            }
            MouseArea {
                objectName: "navigatorMouse"
                enabled: root.imageAvailable
                anchors.fill: parent; preventStealing: true; cursorShape: Qt.OpenHandCursor
                function locate(mouse) { if (width > 0 && height > 0) root.navigation.centerOn(mouse.x/width, mouse.y/height) }
                onPressed: function(mouse) { locate(mouse) }
                onPositionChanged: function(mouse) { if (pressed) locate(mouse) }
            }
        }
    }
}
