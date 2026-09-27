import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Single task/cancel surface across the pixel, matte, refine and AI pipelines.
ColumnLayout {
    id: root
    required property var editor
    property string prefix: ""
    readonly property var selection: editor.selection
    Layout.fillWidth: true; spacing: 4
    visible: root.selection.taskKind !== "none"
    Caption { text: root.editor.status; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Action {
        objectName: root.prefix+"cancelTaskButton"
        text: root.selection.taskKind==="ai" ? "取消 AI 请求" : root.selection.taskKind==="matte" ? "取消边缘细化" : root.selection.queuedPixelTask ? "取消等待中的点选" : "取消当前计算"
        hint: root.selection.taskKind==="refine" ? "本地经典计算很快，不支持中途取消" : "按 Esc 也可取消；当前选区保留"
        visible: root.selection.taskCancellable
        Layout.fillWidth: true
        onClicked: root.selection.cancelTask()
    }
}
