import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "FilePaths.js" as FilePaths

Dialog {
    id: root
    objectName: "conversationExportDialog"
    property var editor: null
    property string filePath: ""
    property string errorText: ""
    property bool pending: false
    title: "导出 AI 对话"
    modal: true
    closePolicy: pending ? Popup.NoAutoClose : Popup.CloseOnEscape
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(510, (parent ? parent.width : 510) - 40)
    palette.window: "#2d3137"; palette.base: "#22262b"; palette.text: "#e4e8ee"; palette.windowText: "#e4e8ee"; palette.button: "#363c44"; palette.buttonText: "#e4e8ee"

    function markdownPath(path) {
        if (/\.md$/i.test(path)) return path
        return path.replace(/\.[^\\/.]+$/, "") + ".md"
    }
    function start(path, error) {
        if (editor && editor.exportingConversation && !error) return
        filePath = path ? FilePaths.localPath(path) : (editor ? editor.suggestConversationPath() : "")
        errorText = error || ""
        pending = false
        open()
    }
    function doExport() {
        if (!editor || pending || filePath.trim() === "") return
        filePath = markdownPath(FilePaths.localPath(filePath))
        errorText = ""
        pending = true
        if (editor.exportConversationAsync(filePath)) {
            close()
        } else {
            pending = false
            errorText = editor.status
            pathField.forceActiveFocus()
            pathField.selectAll()
        }
    }
    onOpened: { pathField.forceActiveFocus(); pathField.selectAll() }
    Connections {
        target: root.editor
        function onConversationExportCompleted(path, error) {
            if (error !== "") root.start(path, error)
            else root.pending = false
        }
    }

    contentItem: ColumnLayout {
        spacing: 11
        Caption { text: "将当前对话导出为 Markdown 文本。对话也会随项目保存；已有文件不会被覆盖。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Field {
                id: pathField
                objectName: "conversationExportPathField"
                Layout.fillWidth: true
                text: root.filePath
                placeholderText: "对话记录保存位置"
                onTextChanged: { root.filePath = text; root.errorText = "" }
                onAccepted: root.doExport()
            }
            Action { objectName: "conversationExportBrowseButton"; text: "浏览…"; onClicked: browse.open() }
        }
        Caption { text: root.filePath; Layout.fillWidth: true; elide: Text.ElideMiddle; font.pixelSize: 10 }
        Caption { objectName: "conversationExportErrorText"; visible: root.errorText !== ""; text: root.errorText; color: "#edb4a6"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    }
    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        Action { objectName: "conversationExportCancelButton"; text: "取消"; onClicked: root.close() }
        Action { objectName: "conversationExportConfirmButton"; text: "导出对话"; primary: true; enabled: root.editor && root.editor.conversationCount > 0 && root.filePath.trim() !== "" && !root.editor.exportingConversation; onClicked: root.doExport() }
    }
    FileDialog {
        id: browse
        title: "选择对话记录位置"
        fileMode: FileDialog.SaveFile
        currentFolder: FilePaths.folderUrl(root.filePath)
        defaultSuffix: "md"
        nameFilters: ["Markdown (*.md)"]
        onAccepted: root.filePath = root.markdownPath(FilePaths.localPath(selectedFile.toString()))
    }
}
