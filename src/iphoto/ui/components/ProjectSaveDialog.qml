import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "FilePaths.js" as FilePaths

Dialog {
    id: root
    objectName: "projectSaveDialog"
    property var editor: null
    property string filePath: ""
    property string errorText: ""
    property bool pending: false
    title: "保存图层与选区项目"
    modal: true
    closePolicy: pending ? Popup.NoAutoClose : Popup.CloseOnEscape
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(540, (parent ? parent.width : 540) - 40)
    palette.window: "#2d3137"; palette.base: "#22262b"; palette.text: "#e4e8ee"; palette.windowText: "#e4e8ee"; palette.button: "#363c44"; palette.buttonText: "#e4e8ee"

    function withSuffix(path) {
        if (!/\.[^\\/.]+$/.test(path)) return path + ".iphoto"
        return path
    }
    function start(path, error) {
        filePath = path ? FilePaths.localPath(path) : (editor ? editor.suggestProjectPath() : "")
        errorText = error || ""
        pending = false
        open()
    }
    function doSave() {
        if (!editor || pending || filePath.trim() === "") return
        var path = withSuffix(FilePaths.localPath(filePath))
        filePath = path
        errorText = ""
        pending = true
        if (editor.saveProjectAsync(path)) {
            // Keep editing available while the background writer finishes.
            close()
        } else {
            pending = false
            if (errorText === "") errorText = editor.status
        }
    }
    onOpened: { pathField.forceActiveFocus(); pathField.selectAll() }
    Connections {
        target: root.editor
        function onProjectSaveCompleted(path, error) {
            if (error !== "") root.start(path, error)
            else root.pending = false
        }
    }
    contentItem: ColumnLayout {
        spacing: 11
        Caption { text: "项目保存图层、蒙版、选区和对话，并引用原照片。建议将项目与原照片一起保留。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Field {
                id: pathField
                objectName: "projectSavePathField"
                Layout.fillWidth: true
                text: root.filePath
                placeholderText: "项目保存位置"
                onTextChanged: { root.filePath = text; root.errorText = "" }
                onAccepted: root.doSave()
            }
            Action { objectName: "projectSaveBrowseButton"; text: "浏览…"; onClicked: browse.open() }
        }
        Caption { text: root.filePath; Layout.fillWidth: true; elide: Text.ElideMiddle; font.pixelSize: 10 }
        Caption { objectName: "projectSaveErrorText"; visible: root.errorText !== ""; text: root.errorText; color: "#edb4a6"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    }
    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        Action { objectName: "projectSaveCancelButton"; text: "取消"; onClicked: root.close() }
        Action { objectName: "projectSaveConfirmButton"; text: "保存项目"; primary: true; enabled: root.filePath.trim() !== "" && root.editor && !root.editor.busy && !root.editor.savingProject; onClicked: root.doSave() }
    }
    FileDialog {
        id: browse
        title: "选择项目保存位置"
        fileMode: FileDialog.SaveFile
        currentFolder: FilePaths.folderUrl(root.filePath)
        defaultSuffix: "iphoto"
        nameFilters: ["iPhoto 项目 (*.iphoto)"]
        onAccepted: root.filePath = FilePaths.localPath(selectedFile.toString())
    }
}
