import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "FilePaths.js" as FilePaths

Dialog {
    id: root
    objectName: "sourceRelinkDialog"
    property var editor: null
    property string sourcePath: ""
    property string projectPath: ""
    property string filePath: ""
    property string errorText: ""
    property bool pending: false
    title: pending ? "正在核对原照片…" : "找回项目的原照片"
    modal: true
    closePolicy: pending ? Popup.NoAutoClose : Popup.CloseOnEscape
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, (parent ? parent.width : 560) - 40)
    palette.window: "#2d3137"; palette.base: "#22262b"; palette.text: "#e4e8ee"; palette.windowText: "#e4e8ee"; palette.button: "#363c44"; palette.buttonText: "#e4e8ee"

    function begin(source, project, reason) {
        sourcePath = source
        projectPath = project
        filePath = ""
        errorText = reason
        pending = false
        open()
    }
    function verify() {
        if (!editor || pending || filePath.trim() === "") return
        errorText = ""
        pending = true
        if (!editor.relinkProjectSource(FilePaths.localPath(filePath))) {
            pending = false
            if (errorText === "") errorText = editor.status
        }
    }
    onOpened: pathField.forceActiveFocus()
    onClosed: if (editor && !pending) editor.cancelProjectRelink()
    Connections {
        target: root.editor
        function onSourceRelinkRequested(source, project, reason) { root.begin(source, project, reason) }
        function onSourceRelinkFailed(error) {
            root.pending = false
            root.errorText = error
            pathField.forceActiveFocus()
            pathField.selectAll()
        }
        function onSourceRelinkCompleted() { root.pending = false; root.close() }
    }
    contentItem: ColumnLayout {
        spacing: 11
        Caption { text: "项目需要原照片才能还原图层和选区。找到移动后的文件后，会先核对内容；不匹配时当前编辑不会改变。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Caption { text: "项目：" + root.projectPath; elide: Text.ElideMiddle; Layout.fillWidth: true }
        Caption { text: "原位置：" + root.sourcePath; elide: Text.ElideMiddle; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Field {
                id: pathField
                objectName: "relinkPathField"
                Layout.fillWidth: true
                text: root.filePath
                placeholderText: "选择或粘贴原照片的新位置"
                enabled: !root.pending
                onTextChanged: { root.filePath = text; root.errorText = "" }
                onAccepted: root.verify()
            }
            Action { objectName: "relinkBrowseButton"; text: "浏览…"; enabled: !root.pending; onClicked: browse.open() }
        }
        Caption { objectName: "relinkErrorText"; visible: root.errorText !== ""; text: root.errorText; color: "#edb4a6"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Caption { visible: root.pending; text: "正在读取并核对照片内容，请稍候。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    }
    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        Action { text: "取消"; enabled: !root.pending; onClicked: root.close() }
        Action { objectName: "relinkConfirmButton"; text: pending ? "核对中…" : "核对并打开"; primary: true; enabled: !root.pending && root.filePath.trim() !== ""; onClicked: root.verify() }
    }
    FileDialog {
        id: browse
        title: "选择项目原照片"
        currentFolder: FilePaths.folderUrl(root.filePath !== "" ? root.filePath : root.projectPath)
        nameFilters: ["照片 (*.jpg *.jpeg *.png *.mpo *.cr2 *.cr3 *.nef *.nrw *.arw *.srf *.sr2 *.dng *.raf *.orf *.rw2 *.pef *.srw *.x3f)", "所有文件 (*)"]
        onAccepted: root.filePath = FilePaths.localPath(selectedFile.toString())
    }
}
