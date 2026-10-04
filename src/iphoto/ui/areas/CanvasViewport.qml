import QtQuick
import QtQuick.Controls
import QtQuick.Window
import "../components"

Rectangle {
    id: root
    objectName: "canvasViewport"
    required property var workspace
    required property var editor
    readonly property var selection: editor.selection
    property bool holdingOriginal: false
    property bool showNavigator: true
    property bool wheelZoom: false
    readonly property var navigation: editor.viewport
    readonly property bool previewReady: previewLoader.item !== null && previewLoader.item.status === Image.Ready
    readonly property bool previewFailed: previewLoader.item !== null && previewLoader.item.status === Image.Error
    readonly property bool originalFallback: previewLoader.item === null || !previewLoader.item.hasFrame
    readonly property bool previewRecovering: previewLoader.item !== null && previewLoader.item.hadFailure && previewLoader.item.status === Image.Loading
    readonly property bool detailCoversViewport: coversViewport(detailImage.displayedRect)
    readonly property bool detailReady: detailImage.visible && detailImage.frameCurrent && detailCoversViewport
    readonly property bool originalDetailReady: originalDetail.frameCurrent && coversViewport(originalDetail.displayedRect)
    readonly property bool maskDetailReady: detailMaskImage.visible && detailMaskImage.frameCurrent
    readonly property bool detailWanted: editor.hasImage && editor.wantsDetail(navigation.zoom, navigation.visibleRect)
    // Start the first native view immediately. Subsequent viewport changes
    // still share the timer below so a scroll/zoom burst can settle.
    onDetailWantedChanged: if (detailWanted) Qt.callLater(function() {
        // Leave the binding evaluation before the controller emits changed.
        if (root.detailWanted) editor.requestDetail()
    })
    readonly property string detailStatus: {
        if (root.holdingOriginal || root.originalFallback)
            return root.originalDetailReady ? "原图细节已显示（正在看原图）"
                : root.detailWanted ? "原图预览（正在载入原像素对比细节）" : "原图预览"
        if (workspace.compare && root.detailWanted && !root.originalDetailReady)
            return "正在载入原图对比细节，暂显快速预览"
        if (root.detailReady) return "原图细节已显示"
        if (editor.detailFailed || detailImage.status === Image.Error) return "快速预览（细节未能更新）"
        if (root.detailWanted && editor.photoPreparing)
            return detailImage.visible && root.detailCoversViewport
                ? "照片正在准备，暂显上一次细节"
                : "照片正在准备，完成后加载当前区域细节"
        if (detailImage.visible && root.detailCoversViewport) return "正在更新细节，暂显上一次效果"
        if (!root.detailWanted && previewLoader.item !== null && editor.previewGeneration >= 0
                && previewLoader.item.displayedGeneration !== editor.documentGeneration)
            return "快速预览正在更新，暂显上一次效果"
        return root.detailWanted ? "正在载入当前区域细节…" : "快速预览 1600px"
    }
    readonly property bool maskDetailCoversViewport: {
        return detailMaskImage.frameCurrent && detailImage.frameCurrent && detailImage.visible
            && detailMaskImage.displayedRect.every(function(value, index) {
                return Math.abs(value - detailImage.displayedRect[index]) < 0.000001
            }) && coversViewport(detailMaskImage.displayedRect)
    }
    readonly property bool selectionPreviewReady: editor.maskUrl.length > 0 && maskImage.status === Image.Ready
    readonly property bool selectionTool: !["inspect", "hand", "zoom"].includes(workspace.selectionTool)
    color: "#191c20"; clip: true
    function coversViewport(tile) {
        var view = navigation.visibleRect, tolerance = 0.000001
        return tile[2] > 0 && tile[3] > 0 && tile[0] <= view[0] + tolerance && tile[1] <= view[1] + tolerance
            && tile[0] + tile[2] + tolerance >= view[0] + view[2]
            && tile[1] + tile[3] + tolerance >= view[1] + view[3]
    }
    function focusCanvas() { photo.forceActiveFocus() }
    function updateSize() { navigation.resize(surface.width, surface.height, root.Screen.devicePixelRatio) }
    Component.onCompleted: { navigation.attachWindow(workspace); updateSize() }
    Screen.onDevicePixelRatioChanged: updateSize()
    Timer { id: detailTimer; interval: 180; repeat: false; onTriggered: editor.requestDetail() }
    Connections { target: editor; function onChanged() { detailTimer.restart() } }
    Connections { target: root.selection; function onChanged() { detailTimer.restart() } }

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
            Loader {
                id: previewLoader
                anchors.fill: parent
                // Retain frames only within one source photo. A new original
                // creates a fresh image and cancels the previous photo's load.
                property string photoIdentity: editor.originalUrl
                onPhotoIdentityChanged: { active=false; active=true }
                sourceComponent: Image {
                    objectName: "photoPreviewImage"
                    source: editor.previewUrl
                    cache: false; asynchronous: true; retainWhileLoading: true
                    fillMode: Image.Stretch
                    property bool hasFrame: false
                    property bool hadFailure: false
                    property int requestedGeneration: -1
                    property int displayedGeneration: -1
                    onSourceChanged: requestedGeneration = editor.previewGeneration
                    onStatusChanged: {
                        if (status === Image.Ready) { hasFrame=true; hadFailure=false; displayedGeneration=requestedGeneration }
                        else if (status === Image.Error) { hasFrame=false; hadFailure=true }
                        else if (status === Image.Null) hasFrame=false
                    }
                }
            }
            ViewportDetailImage {
                id: detailImage; objectName: "detailImage"
                editor: root.editor
                x: displayedRect[0] * photo.width; y: displayedRect[1] * photo.height
                width: displayedRect[2] * photo.width; height: displayedRect[3] * photo.height
                source: editor.detailUrl
                visible: hasFrame && displayedPhoto === editor.originalUrl && !root.holdingOriginal && !root.originalFallback
                    && (root.detailCoversViewport && (!editor.detailFailed || frameCurrent)
                        || previewLoader.item !== null && displayedGeneration === previewLoader.item.displayedGeneration && frameCurrent)
            }
            Item {
                visible: workspace.compare || root.holdingOriginal || root.originalFallback
                width: root.holdingOriginal || root.originalFallback ? photo.width : photo.width*workspace.split
                height: photo.height; clip: true
                Image { objectName: "originalPhotoImage"; width: photo.width; height: photo.height; source: editor.originalUrl; fillMode: Image.Stretch }
                ViewportDetailImage {
                    id: originalDetail; objectName: "originalDetailImage"
                    editor: root.editor; sourceFrame: true
                    x: displayedRect[0] * photo.width; y: displayedRect[1] * photo.height
                    width: displayedRect[2] * photo.width; height: displayedRect[3] * photo.height
                    source: editor.detailOriginalUrl
                    visible: root.originalDetailReady
                }
            }
            Image {
                id: maskImage; anchors.fill: parent; source: editor.maskUrl
                visible: workspace.showMask && !root.holdingOriginal && !root.originalFallback && !workspace.compare && !root.maskDetailCoversViewport
                cache: false; asynchronous: true; fillMode: Image.Stretch
            }
            ViewportDetailImage {
                id: detailMaskImage; objectName: "detailMaskImage"
                editor: root.editor
                x: displayedRect[0] * photo.width; y: displayedRect[1] * photo.height
                width: displayedRect[2] * photo.width; height: displayedRect[3] * photo.height
                source: editor.detailMaskUrl
                visible: workspace.showMask && !root.holdingOriginal && !root.originalFallback && !workspace.compare
                         && detailImage.visible && root.maskDetailCoversViewport
            }
            MouseArea {
                id: selectionMouse
                objectName: "selectionMouse"
                anchors.fill: parent
                enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft && root.selectionTool && !workspace.compare
                cursorShape: workspace.selectionTool === "object" ? Qt.PointingHandCursor : Qt.CrossCursor
                preventStealing: true; hoverEnabled: ["object", "brush", "heal", "transparency"].includes(workspace.selectionTool)
                property var points: []
                property string strokeMode: "replace"
                property string repairLayer: ""
                property string repairPhoto: ""
                property int repairGeneration: -1
                function clearStroke() { points = []; overlays.hoverId = ""; overlays.repaint() }
                function point(mouse) { return [Math.max(0,Math.min(1,mouse.x/width)),Math.max(0,Math.min(1,mouse.y/height))] }
                function appendPoint(p) { var a=points.slice(); if(a.length>=510) a=a.filter(function(_,i){return i%2===0}); a.push(p); points=a }
                onPressed: function(mouse) {
                    photo.forceActiveFocus()
                    repairLayer = editor.activeLayerId; repairPhoto = editor.originalUrl
                    repairGeneration = editor.documentGeneration
                    strokeMode = mouse.modifiers & Qt.AltModifier ? "subtract" : mouse.modifiers & Qt.ShiftModifier ? "add" : workspace.selectionMode
                    points = [point(mouse)]; overlays.repaint()
                }
                onPositionChanged: function(mouse) {
                    if (workspace.selectionTool === "object") { overlays.hoverId=editor.objectAt(mouse.x/width,mouse.y/height); return }
                    if (workspace.selectionTool === "smart") return
                    if (pressed && points.length) {
                        if (["brush", "polygon", "heal", "transparency"].includes(workspace.selectionTool)) appendPoint(point(mouse))
                        else points = [points[0],point(mouse)]
                        overlays.repaint()
                    }
                }
                onReleased: function(mouse) {
                    if (!points.length) return
                    if (workspace.selectionTool === "smart") editor.pixelPoint(point(mouse), !(mouse.modifiers & Qt.AltModifier))
                    else if (workspace.selectionTool === "object") editor.clickObject(point(mouse),strokeMode)
                    else if (workspace.selectionTool === "wand") editor.wandSelection(point(mouse),workspace.tolerance,strokeMode)
                    else if (workspace.selectionTool === "heal") {
                        appendPoint(point(mouse))
                        editor.selection.paintRepair(repairLayer, repairPhoto, repairGeneration, points, workspace.brushRadius)
                    }
                    else if (workspace.selectionTool === "transparency") {
                        appendPoint(point(mouse))
                        editor.selection.paintTransparency(repairLayer, repairPhoto, repairGeneration, points, workspace.brushRadius)
                    }
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
                visible: workspace.compare && !root.holdingOriginal && !root.originalFallback; x: photo.width*workspace.split-1; width: 2; height: parent.height; color: "white"
                MouseArea {
                    objectName: "compareHandle"
                    anchors.fill: parent; anchors.leftMargin: -12; anchors.rightMargin: -12
                    cursorShape: Qt.SplitHCursor; preventStealing: true
                    onPositionChanged: function(mouse) { if (pressed) { var p=mapToItem(photo,mouse.x,mouse.y); workspace.split=Math.max(.02,Math.min(.98,p.x/photo.width)) } }
                }
            }
        }
        CanvasOverlays {
            id: overlays; anchors.fill: parent
            workspace: root.workspace; editor: root.editor; photo: photo; input: selectionMouse
            visible: !root.holdingOriginal && !root.originalFallback && !workspace.compare && !workspace.modalActive
        }
        Rectangle {
            objectName: "previewLoadFailure"
            visible: previewLoader.item !== null && previewLoader.item.hadFailure
            anchors.horizontalCenter: parent.horizontalCenter; anchors.top: parent.top; anchors.topMargin: 8
            width: Math.min(parent.width-16, previewErrorText.implicitWidth+20)
            height: previewErrorText.implicitHeight+14; radius: 4; color: "#503b37"
            Text {
                id: previewErrorText
                anchors.centerIn: parent; width: parent.width-20
                text: root.previewRecovering ? "正在重试预览，暂时显示原图；修改已保留。" : "预览未能更新，当前显示原图；修改已保留，可重新调节以重试。"
                textFormat: Text.PlainText; color: "#f1d1c8"; font.pixelSize: 11; wrapMode: Text.Wrap
            }
        }
        MouseArea {
            id: navigationMouse
            objectName: "navigationMouse"
            anchors.fill: parent
            enabled: editor.hasImage && !workspace.modalActive
            acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
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
                if (mouse.button === Qt.RightButton) { canvasMenu.popup(); mouse.accepted = true; return }
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
            navigation: root.navigation; previewUrl: editor.previewUrl; originalUrl: editor.originalUrl
            visible: root.showNavigator && editor.hasImage && !root.navigation.fitMode && !selectionMouse.pressed && !navigationMouse.pressed
        }
    }
    Connections {
        target: root.navigation
        function onChanged() { checker.requestPaint(); detailTimer.restart(); if (selectionMouse.pressed) selectionMouse.clearStroke() }
        function onKeysChanged() { if (root.navigation.spaceHeld) selectionMouse.clearStroke(); overlays.repaint() }
        function onCancelGesture() { navigationMouse.gesture = ""; selectionMouse.clearStroke() }
    }
    Menu {
        id: canvasMenu
        MenuItem { objectName: "canvasMenuAll"; text: "全选  Ctrl+A"; enabled: editor.hasImage && !editor.busy; onTriggered: editor.draftAction("all") }
        MenuItem { objectName: "canvasMenuInvert"; text: "反选  Ctrl+Shift+I"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: editor.draftAction("invert") }
        MenuItem { objectName: "canvasMenuClear"; text: "清空范围"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: editor.draftAction("clear") }
        MenuItem { objectName: "canvasMenuDiscard"; text: "取消选区  Ctrl+D"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: editor.selection.discard() }
        MenuSeparator {}
        MenuItem { objectName: "canvasMenuMask"; text: root.workspace.showMask ? "隐藏蒙版  Q" : "显示蒙版  Q"; enabled: editor.hasImage; onTriggered: root.selection.toggleShowMask() }
        MenuItem { objectName: "canvasMenuCompare"; text: root.workspace.compare ? "结束对比" : "原图对比"; enabled: editor.hasImage; onTriggered: { root.workspace.compare = !root.workspace.compare; if (root.workspace.compare) root.selection.chooseTool("inspect") } }
        MenuSeparator {}
        MenuItem { objectName: "canvasMenuFit"; text: "适应画布  Ctrl+0"; enabled: editor.hasImage; onTriggered: root.navigation.fit() }
        MenuItem { objectName: "canvasMenu100"; text: "原图 100%  Ctrl+1"; enabled: editor.hasImage; onTriggered: root.navigation.setZoom(1) }
    }
    Column {
        anchors.centerIn: parent; spacing: 16; visible: !editor.hasImage
        Text { text: "从一张照片开始"; color: workspace.ink; font.pixelSize: 23 }
        Action { text: "打开示例"; primary: true; onClicked: editor.loadDemo() }
    }
    DropArea { anchors.fill: parent; onDropped: function(drop) { if (drop.hasUrls) editor.openImage(drop.urls[0].toString()) } }
}
