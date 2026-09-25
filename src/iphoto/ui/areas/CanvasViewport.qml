import QtQuick
import QtQuick.Controls
import QtQuick.Window
import "../components"

Rectangle {
    id: root
    objectName: "canvasViewport"
    required property var workspace
    required property var editor
    property bool holdingOriginal: false
    property bool showNavigator: true
    property bool wheelZoom: false
    readonly property var navigation: editor.viewport
    readonly property bool previewReady: afterImage.status === Image.Ready
    readonly property bool selectionPreviewReady: editor.maskUrl.length > 0 && maskImage.status === Image.Ready
    readonly property bool selectionTool: !["inspect", "hand", "zoom"].includes(workspace.selectionTool)
    color: "#191c20"; clip: true
    function focusCanvas() { photo.forceActiveFocus() }
    function updateSize() { navigation.resize(surface.width, surface.height, root.Screen.devicePixelRatio) }
    Component.onCompleted: { navigation.attachWindow(workspace); updateSize() }
    Screen.onDevicePixelRatioChanged: updateSize()

    Item {
        id: surface
        objectName: "canvasSurface"
        anchors.fill: parent; anchors.margins: 18; clip: true
        onWidthChanged: root.updateSize()
        onHeightChanged: root.updateSize()
        Canvas {
            id: checker
            anchors.fill: parent
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                var c = getContext("2d"); c.reset()
                var left = Math.max(0,photo.x), top = Math.max(0,photo.y)
                var right = Math.min(width,photo.x+photo.width), bottom = Math.min(height,photo.y+photo.height)
                c.save(); c.beginPath(); c.rect(left,top,right-left,bottom-top); c.clip()
                for (var y = Math.floor(top/12)*12; y < bottom; y += 12)
                    for (var x = Math.floor(left/12)*12; x < right; x += 12) {
                        c.fillStyle = ((x/12+y/12)%2) ? "#4a4d52" : "#383c41"; c.fillRect(x,y,12,12)
                    }
                c.restore()
            }
        }
        Item {
            id: photo
            objectName: "photoCanvas"
            x: root.navigation.imageX; y: root.navigation.imageY
            width: root.navigation.imageWidth; height: root.navigation.imageHeight
            focus: true
            Image { id: afterImage; anchors.fill: parent; source: root.holdingOriginal ? editor.originalUrl : editor.previewUrl; cache: false; asynchronous: true; fillMode: Image.Stretch }
            Item {
                visible: workspace.compare && !root.holdingOriginal
                width: photo.width*workspace.split; height: photo.height; clip: true
                Image { width: photo.width; height: photo.height; source: editor.originalUrl; fillMode: Image.Stretch }
            }
            Image { id: maskImage; anchors.fill: parent; source: editor.maskUrl; visible: workspace.showMask && !root.holdingOriginal && !workspace.compare; cache: false; asynchronous: true; fillMode: Image.Stretch }
            MouseArea {
                id: selectionMouse
                objectName: "selectionMouse"
                anchors.fill: parent
                enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft && root.selectionTool && !workspace.compare
                cursorShape: workspace.selectionTool === "object" ? Qt.PointingHandCursor : Qt.CrossCursor
                preventStealing: true; hoverEnabled: workspace.selectionTool === "object"
                property var points: []
                property string strokeMode: "replace"
                function clearStroke() { points = []; overlays.hoverId = ""; overlays.repaint() }
                function point(mouse) { return [Math.max(0,Math.min(1,mouse.x/width)),Math.max(0,Math.min(1,mouse.y/height))] }
                function appendPoint(p) { var a=points.slice(); if(a.length>=510) a=a.filter(function(_,i){return i%2===0}); a.push(p); points=a }
                onPressed: function(mouse) {
                    photo.forceActiveFocus()
                    strokeMode = mouse.modifiers & Qt.AltModifier ? "subtract" : mouse.modifiers & Qt.ShiftModifier ? "add" : workspace.selectionMode
                    points = [point(mouse)]; overlays.repaint()
                }
                onPositionChanged: function(mouse) {
                    if (workspace.selectionTool === "object") { overlays.hoverId=editor.objectAt(mouse.x/width,mouse.y/height); return }
                    if (workspace.selectionTool === "smart") return
                    if (pressed && points.length) {
                        if (workspace.selectionTool === "brush" || workspace.selectionTool === "polygon") appendPoint(point(mouse))
                        else points = [points[0],point(mouse)]
                        overlays.repaint()
                    }
                }
                onReleased: function(mouse) {
                    if (!points.length) return
                    if (workspace.selectionTool === "smart") editor.pixelPoint(point(mouse), !(mouse.modifiers & Qt.AltModifier))
                    else if (workspace.selectionTool === "object") editor.clickObject(point(mouse),strokeMode)
                    else if (workspace.selectionTool === "wand") editor.wandSelection(point(mouse),workspace.tolerance,strokeMode)
                    else {
                        if (workspace.selectionTool === "brush" || workspace.selectionTool === "polygon") appendPoint(point(mouse))
                        else points = [points[0],point(mouse)]
                        editor.drawDraft(workspace.selectionTool,strokeMode,points,workspace.brushRadius)
                    }
                    clearStroke()
                }
                onExited: overlays.hoverId = ""
                onCanceled: clearStroke()
            }
            Rectangle {
                visible: workspace.compare; x: photo.width*workspace.split-1; width: 2; height: parent.height; color: "white"
                MouseArea {
                    objectName: "compareHandle"
                    anchors.fill: parent; anchors.leftMargin: -12; anchors.rightMargin: -12
                    cursorShape: Qt.SplitHCursor; preventStealing: true
                    onPositionChanged: function(mouse) { if (pressed) { var p=mapToItem(photo,mouse.x,mouse.y); workspace.split=Math.max(.02,Math.min(.98,p.x/photo.width)) } }
                }
            }
        }
        CanvasOverlays { id: overlays; anchors.fill: parent; workspace: root.workspace; editor: root.editor; photo: photo; input: selectionMouse }
        MouseArea {
            id: navigationMouse
            objectName: "navigationMouse"
            anchors.fill: parent
            enabled: editor.hasImage && !workspace.modalActive
            acceptedButtons: Qt.LeftButton | Qt.MiddleButton
            preventStealing: true
            property string gesture: ""
            property real lastX: 0
            property real lastY: 0
            property real startX: 0
            property real startY: 0
            property real startZoom: 1
            property bool dragged: false
            property bool zoomOut: false
            readonly property bool zoomTool: root.navigation.temporaryZoom || (!root.navigation.spaceHeld && workspace.selectionTool === "zoom")
            readonly property bool handTool: root.navigation.spaceHeld || ["hand","inspect"].includes(workspace.selectionTool)
            cursorShape: gesture === "pan" ? Qt.ClosedHandCursor : zoomTool ? Qt.CrossCursor : handTool ? Qt.OpenHandCursor : workspace.selectionTool === "object" ? Qt.PointingHandCursor : Qt.CrossCursor
            onPressed: function(mouse) {
                if (mouse.button === Qt.MiddleButton) gesture = "pan"
                else if (zoomTool) gesture = "zoom"
                else if (handTool && !workspace.compare || root.navigation.spaceHeld || workspace.selectionTool === "hand") gesture = "pan"
                else { mouse.accepted = false; return }
                root.focusCanvas(); overlays.hoverId = ""
                lastX = startX = mouse.x; lastY = startY = mouse.y
                startZoom = root.navigation.zoom; dragged = false
                zoomOut = !!(mouse.modifiers & Qt.AltModifier)
            }
            onPositionChanged: function(mouse) {
                if (gesture === "pan") root.navigation.pan(mouse.x-lastX,mouse.y-lastY)
                else if (gesture === "zoom" && (dragged || Math.abs(mouse.x-startX) > 4)) {
                    dragged = true
                    root.navigation.zoomAt(startZoom*Math.exp(Math.max(-600,Math.min(600,mouse.x-startX))*.01),startX,startY)
                }
                lastX = mouse.x; lastY = mouse.y
            }
            onReleased: function(mouse) {
                if (gesture === "zoom" && !dragged) root.navigation.zoomAt(startZoom*(zoomOut ? .8 : 1.25),startX,startY)
                gesture = ""
            }
            onCanceled: gesture = ""
            onDoubleClicked: {
                if (workspace.selectionTool === "zoom") root.navigation.setZoom(1)
                else if (handTool) root.navigation.fit()
                gesture = ""
            }
            onWheel: function(wheel) {
                wheel.accepted = true
                if (selectionMouse.pressed || gesture) return
                var pixel = wheel.pixelDelta.x !== 0 || wheel.pixelDelta.y !== 0
                var dx = pixel ? wheel.pixelDelta.x : wheel.angleDelta.x/120*60
                var dy = pixel ? wheel.pixelDelta.y : wheel.angleDelta.y/120*60
                if (wheel.modifiers & (Qt.AltModifier | Qt.ControlModifier) || root.wheelZoom) {
                    var delta = pixel ? dy/240 : wheel.angleDelta.y/120
                    root.navigation.zoomAt(root.navigation.zoom*Math.pow(1.2,Math.max(-5,Math.min(5,delta))),wheel.x,wheel.y)
                } else if (wheel.modifiers & Qt.ShiftModifier) root.navigation.pan(dy || dx,0)
                else root.navigation.pan(dx,dy)
            }
        }
        CanvasNavigator {
            objectName: "canvasNavigator"
            anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 10
            navigation: root.navigation; previewUrl: editor.previewUrl
            visible: root.showNavigator && editor.hasImage && !root.navigation.fitMode && !selectionMouse.pressed && !navigationMouse.pressed
        }
    }
    Connections {
        target: root.navigation
        function onChanged() { checker.requestPaint(); if (selectionMouse.pressed) selectionMouse.clearStroke() }
        function onKeysChanged() { if (root.navigation.spaceHeld) selectionMouse.clearStroke(); overlays.repaint() }
        function onCancelGesture() { navigationMouse.gesture = ""; selectionMouse.clearStroke() }
    }
    Column {
        anchors.centerIn: parent; spacing: 16; visible: !editor.hasImage
        Text { text: "从一张照片开始"; color: workspace.ink; font.pixelSize: 23 }
        Action { text: "打开示例"; primary: true; onClicked: editor.loadDemo() }
    }
    DropArea { anchors.fill: parent; onDropped: function(drop) { if (drop.hasUrls) editor.openImage(drop.urls[0].toString()) } }
}
