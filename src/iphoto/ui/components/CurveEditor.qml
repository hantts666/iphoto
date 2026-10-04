import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

ColumnLayout {
    id: root
    objectName: "curveEditor"
    required property var workspace
    required property var editor
    property bool compact: false
    property int channel: 0
    readonly property var curveKeys: ["curve_rgb", "curve_red", "curve_green", "curve_blue"]
    readonly property string curveKey: curveKeys[channel]
    readonly property color curveColor: ["#d7e4df", "#ec8b86", "#83c993", "#86b6ee"][channel]
    property var points: [[0,0], [255,255]]
    readonly property var samples: editor.selection.curveSamples(points)
    property int pointIndex: 0
    property bool dragging: false
    property bool committing: false
    property bool editingCoordinates: false
    property string errorMessage: ""
    property string editLayer: ""
    property string editPhoto: ""
    property int editGeneration: -1
    property var originalCurve: []
    property bool originallyLocked: false
    property bool initialized: false
    Layout.fillWidth: true
    spacing: compact ? 3 : 6
    enabled: workspace.editingEnabled && !editor.activeIsGroup

    function copyPoints(value) { return value.map(function(p) { return [p[0], p[1]] }) }
    function capture() {
        editLayer = editor.activeLayerId; editPhoto = editor.originalUrl
        editGeneration = editor.documentGeneration
    }
    function sameBinding() {
        return enabled && editLayer === editor.activeLayerId && editPhoto === editor.originalUrl
            && editGeneration === editor.documentGeneration
    }
    function syncFields() {
        inputField.text = String(points[pointIndex][0]); outputField.text = String(points[pointIndex][1])
    }
    function sync() {
        points = copyPoints(editor.parameters[curveKey] || [])
        if (!points.length) points = [[0,0], [255,255]]
        pointIndex = Math.min(pointIndex, points.length-1)
        editingCoordinates = false; errorMessage = ""; syncFields(); plot.requestPaint()
    }
    function apply() {
        if (!sameBinding()) { dragging = false; editingCoordinates = false; sync(); return false }
        committing = true
        var ok = editor.selection.applyCurve(editLayer, editPhoto, editGeneration, curveKey, points)
        editGeneration = editor.documentGeneration
        committing = false
        return ok
    }
    function commitText() { return submitCoordinates(true) }
    function submitCoordinates(refocus) {
        if (!editingCoordinates) return true
        if (!sameBinding()) { sync(); return true }
        var xText = inputField.text.trim(), yText = outputField.text.trim()
        var x = Number(xText), y = Number(yText)
        var low = pointIndex ? points[pointIndex-1][0]+1 : 0
        var high = pointIndex < points.length-1 ? points[pointIndex+1][0]-1 : 255
        if (!/^\d{1,3}$/.test(xText) || !/^\d{1,3}$/.test(yText)
                || x < low || x > high || y < 0 || y > 255
                || pointIndex === 0 && x !== 0 || pointIndex === points.length-1 && x !== 255) {
            errorMessage = "输入须为 " + (pointIndex === 0 ? "0" : pointIndex === points.length-1 ? "255" : low+"～"+high)
                + "，输出须为 0～255 的整数"
            if (refocus) inputField.forceActiveFocus()
            return false
        }
        var next = copyPoints(points); next[pointIndex] = [x,y]; points = next
        if (!apply()) return false
        editingCoordinates = false; errorMessage = ""
        editor.finishGesture(); syncFields(); return true
    }
    function startCoordinates() {
        if (!editingCoordinates) capture()
        editingCoordinates = true; errorMessage = ""
    }
    function selectPoint(index) {
        if (!commitText()) return false
        pointIndex = index; syncFields(); plot.requestPaint(); return true
    }
    function revealCurve(key) {
        if (!commitText()) return
        channel = Math.max(0, curveKeys.indexOf(key)); sync()
    }
    function resetCurve() {
        if (!commitText()) return
        capture(); points = [[0,0],[255,255]]; pointIndex = 0
        if (apply()) editor.finishGesture()
        sync()
    }
    function removePoint() {
        if (!commitText() || pointIndex === 0 || pointIndex === points.length-1) return
        capture(); var next = copyPoints(points); next.splice(pointIndex,1); points = next
        pointIndex = Math.min(pointIndex, points.length-1)
        if (apply()) editor.finishGesture()
        syncFields()
    }
    function cancelDrag() {
        if (!dragging) return
        var valid = sameBinding(); dragging = false
        if (valid) {
            committing = true
            editor.selection.cancelCurve(editLayer, editPhoto, editGeneration, curveKey, originalCurve, originallyLocked)
            committing = false
        }
        sync()
    }
    onCurveKeyChanged: if (initialized) sync()
    onEnabledChanged: if (initialized && !enabled) { dragging = false; sync() }
    onVisibleChanged: if (initialized && !visible) { cancelDrag(); sync() }
    Connections {
        target: root.editor
        function onChanged() {
            if (!root.initialized || root.committing) return
            if (root.dragging || root.editingCoordinates) {
                if (!root.sameBinding()) { root.dragging = false; root.sync() }
            } else root.sync()
        }
    }
    Component.onCompleted: { initialized = true; sync() }
    SelectBox {
        objectName: "curveChannelChoice"
        Layout.fillWidth: true
        model: ["RGB 总曲线", "红通道", "绿通道", "蓝通道"]
        currentIndex: root.channel
        onActivated: function(index) {
            if (root.commitText()) root.channel = index
            else currentIndex = root.channel
        }
    }
    Caption {
        visible: !root.compact
        text: "点击添加控制点，拖动调整；左侧暗部，右侧亮部。Esc 撤回本次拖动。"
        font.pixelSize: 10; wrapMode: Text.Wrap; Layout.fillWidth: true
    }
    Canvas {
        id: plot; objectName: "curvePlot"
        Layout.fillWidth: true; Layout.preferredHeight: root.compact ? 86 : 178
        property real margin: 9
        function px(x) { return margin + x/255*(width-2*margin) }
        function py(y) { return height-margin-y/255*(height-2*margin) }
        function inputAt(x) { return Math.round(Math.max(0,Math.min(255,(x-margin)*255/(width-2*margin)))) }
        function outputAt(y) { return Math.round(Math.max(0,Math.min(255,(height-margin-y)*255/(height-2*margin)))) }
        onPaint: {
            var c = getContext("2d"); c.reset(); c.fillStyle = "#22262b"; c.fillRect(0,0,width,height)
            c.lineWidth = 1; c.strokeStyle = "#41464e"; c.beginPath()
            for (var i=0;i<5;++i) {
                var t = i*255/4; c.moveTo(px(t),py(0)); c.lineTo(px(t),py(255))
                c.moveTo(px(0),py(t)); c.lineTo(px(255),py(t))
            }
            c.stroke(); c.strokeStyle = "#67716f"; c.beginPath(); c.moveTo(px(0),py(0)); c.lineTo(px(255),py(255)); c.stroke()
            c.strokeStyle = root.curveColor; c.lineWidth = 2; c.beginPath()
            for (var j=0;j<root.samples.length;++j) {
                if (j===0) c.moveTo(px(j),py(root.samples[j])); else c.lineTo(px(j),py(root.samples[j]))
            }
            c.stroke()
            for (var k=0;k<root.points.length;++k) {
                c.beginPath(); c.arc(px(root.points[k][0]),py(root.points[k][1]),k===root.pointIndex ? 5 : 3.5,0,2*Math.PI)
                c.fillStyle = k===root.pointIndex ? "#ffffff" : root.curveColor; c.fill()
            }
        }
        Connections {
            target: root
            function onPointsChanged() { plot.requestPaint() }
            function onPointIndexChanged() { plot.requestPaint() }
            function onCurveColorChanged() { plot.requestPaint() }
        }
        MouseArea {
            id: graphMouse; anchors.fill: parent; enabled: root.enabled
            focus: true; preventStealing: true
            function movePoint(mouse) {
                if (!root.dragging) return
                var index = root.pointIndex, next = root.copyPoints(root.points)
                var x = index===0 ? 0 : index===next.length-1 ? 255
                    : Math.max(next[index-1][0]+1,Math.min(next[index+1][0]-1,plot.inputAt(mouse.x)))
                next[index] = [x,plot.outputAt(mouse.y)]; root.points = next
                root.apply(); root.syncFields()
            }
            onPressed: function(mouse) {
                if (!root.commitText()) { mouse.accepted = false; return }
                forceActiveFocus(); root.editor.finishGesture(); root.capture()
                root.originalCurve = root.copyPoints(root.editor.parameters[root.curveKey] || [])
                root.originallyLocked = root.editor.lockedFields.indexOf(root.curveKey)>=0
                var chosen = -1, distance = 144
                for (var i=0;i<root.points.length;++i) {
                    var dx = plot.px(root.points[i][0])-mouse.x, dy = plot.py(root.points[i][1])-mouse.y
                    if (dx*dx+dy*dy <= distance) { chosen = i; distance = dx*dx+dy*dy }
                }
                if (chosen<0) {
                    if (root.points.length>=16) { root.errorMessage = "最多16个控制点，请先删除一个"; mouse.accepted = false; return }
                    var x = plot.inputAt(mouse.x), next = root.copyPoints(root.points)
                    if (x===0 || x===255 || next.some(function(p) { return p[0]===x })) { mouse.accepted = false; return }
                    next.push([x,plot.outputAt(mouse.y)]); next.sort(function(a,b) { return a[0]-b[0] })
                    root.points = next; chosen = next.findIndex(function(p) { return p[0]===x })
                }
                root.pointIndex = chosen; root.dragging = true; root.apply(); root.syncFields()
            }
            onPositionChanged: function(mouse) { if (pressed) movePoint(mouse) }
            onReleased: function(mouse) {
                if (!root.dragging) return
                movePoint(mouse); var valid = root.sameBinding(); root.dragging = false
                if (valid) root.editor.finishGesture()
                root.sync()
            }
            onCanceled: root.cancelDrag()
            Keys.onEscapePressed: function(event) { root.cancelDrag(); event.accepted = true }
            Keys.onDeletePressed: function(event) { root.removePoint(); event.accepted = true }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Caption { text: "输入" }
        Field {
            id: inputField; objectName: "curvePointInput"; Layout.fillWidth: true; implicitHeight: 26
            maximumLength: 3; inputMethodHints: Qt.ImhDigitsOnly
            onTextEdited: root.startCoordinates()
            onAccepted: if (root.submitCoordinates(true)) focus = false
            onEditingFinished: if (!activeFocus && root.editingCoordinates) root.submitCoordinates(false)
            Keys.onEscapePressed: function(event) { root.sync(); focus = false; event.accepted = true }
        }
        Caption { text: "输出" }
        Field {
            id: outputField; objectName: "curvePointOutput"; Layout.fillWidth: true; implicitHeight: 26
            maximumLength: 3; inputMethodHints: Qt.ImhDigitsOnly
            onTextEdited: root.startCoordinates()
            onAccepted: if (root.submitCoordinates(true)) focus = false
            onEditingFinished: if (!activeFocus && root.editingCoordinates) root.submitCoordinates(false)
            Keys.onEscapePressed: function(event) { root.sync(); focus = false; event.accepted = true }
        }
    }
    Caption { visible: root.errorMessage!==""; text: root.errorMessage; color: "#edb4a6"; font.pixelSize: 10; wrapMode: Text.Wrap; Layout.fillWidth: true }
    RowLayout {
        Layout.fillWidth: true
        Action { objectName: "curveDeletePoint"; text: "删除点"; subtle: true; enabled: root.enabled && root.pointIndex>0 && root.pointIndex<root.points.length-1; onClicked: root.removePoint() }
        Action { objectName: "curveReset"; text: root.compact ? "重置" : "重置曲线"; subtle: true; enabled: root.enabled; onClicked: root.resetCurve() }
        Action {
            objectName: "curveUnlock"; text: "已锁 · 解锁"; subtle: true
            hint: "解锁这条曲线，交还AI调整；手动设置的其他参数继续保留"
            visible: editor.lockedFields.indexOf(root.curveKey)>=0
            onClicked: if (root.commitText()) editor.unlock(root.curveKey)
        }
    }
}
