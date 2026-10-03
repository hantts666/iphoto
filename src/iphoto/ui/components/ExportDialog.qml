import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "FilePaths.js" as FilePaths

// Export with explicit format + JPEG quality instead of a bare save dialog.
Dialog {
    id: root
    objectName: "exportDialog"
    property var editor: null
    property string filePath: ""
    property int quality: 92
    property int formatIndex: 0
    property string errorText: ""
    property bool pending: false
    title: pending ? "正在导出照片…" : "导出照片"
    modal: true
    closePolicy: pending ? Popup.NoAutoClose : Popup.CloseOnEscape
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, (parent ? parent.width : 440) - 40)
    palette.window: "#2d3137"; palette.base: "#22262b"; palette.text: "#e4e8ee"; palette.windowText: "#e4e8ee"; palette.button: "#363c44"; palette.buttonText: "#e4e8ee"
    function suffix() { return root.formatIndex === 0 ? ".jpg" : ".png" }
    function fixSuffix(path) {
        var lower = path.toLowerCase()
        if (!/\.(jpg|jpeg|png)$/.test(lower)) return path + root.suffix()
        if (root.formatIndex === 0 && /\.png$/.test(lower)) return path.replace(/\.png$/i, ".jpg")
        if (root.formatIndex === 1 && /\.(jpg|jpeg)$/.test(lower)) return path.replace(/\.(jpg|jpeg)$/i, ".png")
        return path
    }
    onFormatIndexChanged: {
        if (root.filePath.trim() !== "") root.filePath = root.fixSuffix(root.filePath)
        root.errorText = ""
    }
    function doExport() {
        if (!root.editor || root.pending || root.filePath.trim() === "") return
        var path = root.fixSuffix(FilePaths.localPath(root.filePath))
        root.filePath = path
        root.errorText = ""
        root.pending = true
        if (!root.editor.exportWithQuality(path, root.formatIndex === 0 ? root.quality : 100)) {
            root.pending = false
            root.errorText = root.editor.status
            pathField.forceActiveFocus()
            pathField.selectAll()
        }
    }
    onOpened: {
        root.pending = false
        root.errorText = ""
        root.filePath = root.editor ? root.editor.suggestExportPath(root.formatIndex) : ""
        pathField.forceActiveFocus()
        pathField.selectAll()
    }
    Connections { target: root.editor
        function onExportCompleted(path, error) {
            if (!root.pending) return
            root.pending = false
            if (error !== "") {
                root.errorText = error
                pathField.forceActiveFocus()
                pathField.selectAll()
            }
            else { root.errorText = ""; root.close() }
        }
    }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Field {
                id: pathField
                objectName: "exportPathField"
                Layout.fillWidth: true
                text: root.filePath
                enabled: !root.pending
                onTextChanged: { root.filePath = text; root.errorText = "" }
                onAccepted: root.doExport()
            }
            Action { objectName: "exportBrowseButton"; text: "浏览…"; enabled: !root.pending; onClicked: browse.open() }
        }
        Caption { text: root.filePath; Layout.fillWidth: true; elide: Text.ElideMiddle; font.pixelSize: 10 }
        RowLayout { Layout.fillWidth: true; spacing: 6
            Caption { text: "格式" }
            SelectBox {
                objectName: "exportFormatBox"
                Layout.fillWidth: true
                model: ["JPEG（体积小 · 可调质量）", "PNG（无损 · 体积大）"]
                currentIndex: root.formatIndex
                enabled: !root.pending
                onActivated: root.formatIndex = currentIndex
            }
        }
        RowLayout { Layout.fillWidth: true; spacing: 6; visible: root.formatIndex === 0
            Caption { text: "质量" }
            FineSlider { objectName: "exportQualitySlider"; Layout.fillWidth: true; from: 80; to: 100; stepSize: 1; value: root.quality; enabled: !root.pending; onMoved: root.quality = Math.round(value) }
            Caption { text: root.quality; Layout.preferredWidth: 24; font.family: "Consolas" }
        }
        Caption {
            text: root.formatIndex === 0
                ? "JPEG 92 适合分享；100 接近无损但体积更大。导出不会覆盖原图。"
                : "PNG 无损并保留透明通道；文件较大。导出不会覆盖原图。"
            wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10
        }
        Caption { objectName: "exportErrorText"; visible: root.errorText !== ""; text: root.errorText; color: "#edb4a6"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        RowLayout {
            visible: root.pending; Layout.fillWidth: true; spacing: 8
            BusyIndicator {
                objectName: "exportBusyIndicator"
                running: root.pending
                implicitWidth: 24; implicitHeight: 24
                Layout.preferredWidth: 24; Layout.preferredHeight: 24
            }
            Caption {
                objectName: "exportProgressText"
                text: root.editor ? root.editor.exportProgress : ""
                wrapMode: Text.Wrap; Layout.fillWidth: true; color: "#c6eadb"
            }
        }
        Caption { visible: root.pending; text: "完成后自动关闭；可随时取消。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    }
    footer: RowLayout {
        Layout.fillWidth: true; spacing: 8
        Item { Layout.fillWidth: true }
        Action { objectName: "cancelExportButton"; text: root.pending ? "取消导出" : "取消"; onClicked: root.pending ? root.editor.cancelExport() : root.close() }
        Action { objectName: "exportConfirmButton"; text: root.pending ? "导出中…" : "导出"; primary: true; enabled: !root.pending && root.editor && !root.editor.busy && root.filePath.trim() !== ""; onClicked: root.doExport() }
    }
    FileDialog {
        id: browse
        title: "导出为新文件"
        fileMode: FileDialog.SaveFile
        currentFolder: FilePaths.folderUrl(root.filePath)
        defaultSuffix: root.suffix() === ".jpg" ? "jpg" : "png"
        nameFilters: ["JPEG (*.jpg)", "PNG (*.png)"]
        selectedNameFilter.index: root.formatIndex
        onAccepted: root.filePath = root.fixSuffix(FilePaths.localPath(selectedFile.toString()))
    }
}
