import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

// Shared draft output bar: the only place a selection draft becomes real.
Rectangle {
    id: root
    required property var workspace
    required property var editor
    property string prefix: ""
    readonly property bool region: editor.hasRegionDraft
    property bool moreOutput: false
    signal refineRequested()
    visible: editor.hasSelectionDraft || editor.hasRegionDraft
    onVisibleChanged: if (!visible) moreOutput = false
    Layout.fillWidth: true
    Layout.preferredHeight: content.implicitHeight + 20
    color: "#35443e"
    ColumnLayout {
        id: content
        anchors.fill: parent; anchors.margins: 10; spacing: 6
        RowLayout { visible: !root.region; Layout.fillWidth: true; spacing: 4
            Caption { text: "已选好范围。可先检查边缘，再开始调整。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10 }
            Action { objectName: root.prefix+"jumpToRefineButton"; text: "修边 ›"; subtle: true; implicitHeight: 24; font.pixelSize: 10; hint: "查看修边方法、透明度预览与边缘调整"; onClicked: root.refineRequested() }
        }
        Action { objectName: root.prefix+"selectionToLayerButton"; text: root.region ? "开始分区调整" : "开始调整此范围"; hint: root.region ? "为各分区建立可撤销的局部调整" : "直接打开只作用于此范围的调整；原图保留，可撤销"; primary: true; Layout.fillWidth: true; enabled: !editor.busy && workspace.selectionPreviewReady && workspace.previewReady; onClicked: root.region ? editor.selection.applyRegions() : editor.selection.apply("new_layer") }
        RowLayout { Layout.fillWidth: true
            Action { objectName: root.prefix+"discardSelectionButton"; text: "取消选择"; hint: "丢弃当前范围，图层保持不变"; Layout.fillWidth: true; enabled: !editor.busy; onClicked: editor.selection.discard() }
            Action { objectName: root.prefix+"moreOutputButton"; text: root.moreOutput ? "收起" : "更多…"; subtle: true; visible: !root.region; enabled: !editor.busy; onClicked: root.moreOutput = !root.moreOutput }
        }
        Action { objectName: root.prefix+"acceptSelectionButton"; text: "替换当前层的范围"; hint: "用此范围覆盖当前图层的蒙版，可 Ctrl+Z 撤销"; visible: !root.region && root.moreOutput; Layout.fillWidth: true; enabled: !editor.busy && workspace.selectionPreviewReady; onClicked: editor.selection.apply("replace_mask") }
    }
}
