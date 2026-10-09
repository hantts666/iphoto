import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Tasks and photo preparation stay visible across chat and inspector navigation.
Rectangle {
    id: root
    objectName: "aiRequestProgress"
    required property var editor
    readonly property var selection: editor.selection
    Layout.fillWidth: true
    Layout.preferredHeight: Math.max(36, progressText.implicitHeight + 12)
    visible: root.selection.taskKind !== "none"
    color: "#30453e"
    readonly property bool timingPixels: visible && ["pixel", "content"].includes(root.selection.taskKind)
    property real pixelStartedAt: 0
    property int pixelElapsedSeconds: 0
    onTimingPixelsChanged: {
        pixelStartedAt = timingPixels ? Date.now() : 0
        pixelElapsedSeconds = 0
    }
    Timer {
        interval: 1000; repeat: true; running: root.timingPixels
        onTriggered: root.pixelElapsedSeconds = Math.max(0, Math.floor((Date.now() - root.pixelStartedAt) / 1000))
    }
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 8
        BusyIndicator {
            running: root.visible
            implicitWidth: 22; implicitHeight: 22
            Layout.preferredWidth: 22; Layout.preferredHeight: 22
        }
        Caption {
            id: progressText
            objectName: "aiRequestProgressText"
            text: root.selection.taskText
            Layout.fillWidth: true; color: "#c6eadb"; font.pixelSize: 12
            wrapMode: Text.Wrap
        }
        Caption {
            objectName: "localTaskElapsedText"
            text: "已用 " + root.pixelElapsedSeconds + " 秒"
            visible: root.timingPixels
            color: "#c6eadb"; font.pixelSize: 12
        }
        Action {
            objectName: "cancelAiRequest"
            text: root.selection.taskKind==="content" ? "取消导入" : root.selection.taskKind==="ai" ? "取消 AI 任务" : root.selection.taskKind==="matte" ? "取消边缘细化" : root.selection.taskKind==="warm" ? "取消照片准备" : root.selection.queuedPixelTask ? "取消等待中的点选" : "取消当前计算"
            hint: "按 Esc 也可取消；照片和已有图层保留"
            visible: root.selection.taskCancellable
            implicitHeight: 26
            onClicked: root.selection.cancelTask()
        }
    }
}
