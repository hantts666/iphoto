import QtQuick
import QtQuick.Controls

// Text is a local draft until Enter or focus loss. Binding checks prevent an
// unfinished number from landing on another photo, layer, or later edit.
Field {
    id: root
    required property var editor
    required property string parameterKey
    readonly property real modelValue: editor.parameters[parameterKey] || 0
    property bool editing: false
    property bool committing: false
    property string errorMessage: ""
    property string editLayer: ""
    property string editPhoto: ""
    property int editGeneration: -1
    implicitWidth: 56; implicitHeight: 20
    leftPadding: 4; rightPadding: 4
    topPadding: 2; bottomPadding: 2
    verticalAlignment: TextInput.AlignVCenter
    font.family: "Consolas"; font.pixelSize: 11
    horizontalAlignment: TextInput.AlignRight
    maximumLength: 32
    inputMethodHints: Qt.ImhFormattedNumbersOnly
    color: errorMessage ? "#edb4a6" : Theme.ink
    background: Rectangle {
        radius: 3; color: "#22262b"
        border.color: root.errorMessage ? "#dc9587" : root.activeFocus ? "#73ac98" : Theme.line
    }
    ToolTip.visible: errorMessage !== "" && activeFocus
    ToolTip.text: errorMessage
    function syncText() { text = Number(modelValue).toFixed(parameterKey === "exposure" ? 2 : 0) }
    function sameBinding() {
        return editLayer === editor.activeLayerId && editPhoto === editor.originalUrl
            && editGeneration === editor.documentGeneration && enabled && visible
    }
    function cancel() { editing = false; errorMessage = ""; syncText() }
    function submit(refocus) {
        if (!editing) return true
        if (!sameBinding()) { cancel(); return true }
        committing = true
        var result = editor.selection.applyParameterText(editLayer, editPhoto, editGeneration, parameterKey, text)
        committing = false
        if (result.ok) { cancel(); return true }
        if (refocus) forceActiveFocus()
        errorMessage = result.message
        return false
    }
    onTextEdited: {
        if (!editing) {
            editLayer = editor.activeLayerId; editPhoto = editor.originalUrl
            editGeneration = editor.documentGeneration
        }
        editing = true; errorMessage = ""
    }
    onActiveFocusChanged: if (activeFocus) {
        if (!editing) syncText()
        Qt.callLater(function() { if (root.activeFocus) root.selectAll() })
    }
    onModelValueChanged: if (!editing && !committing) syncText()
    onEnabledChanged: if (!enabled) cancel()
    onVisibleChanged: if (!visible) cancel()
    onAccepted: if (submit()) focus = false
    onEditingFinished: if (editing && !activeFocus) submit(false)
    Keys.onEscapePressed: function(event) { cancel(); focus = false; event.accepted = true }
    Connections {
        target: root.editor
        function onChanged() {
            if (root.committing) return
            if (root.editing && !root.sameBinding()) root.cancel()
            else if (!root.editing) root.syncText()
        }
    }
    Component.onCompleted: syncText()
}
