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
    readonly property bool editingMask: editor.selection.editingLayerMask
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
            Caption { text: root.editingMask ? "正在修正此层范围。保存后继续调整照片。" : "已选好范围。可先检查边缘，再开始调整。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10 }
            Action { objectName: root.prefix+"jumpToRefineButton"; text: "修边 ›"; subtle: true; implicitHeight: 24; font.pixelSize: 10; hint: "查看修边方法、透明度预览与边缘调整"; onClicked: root.refineRequested() }
        }
        RowLayout { visible: !root.region; Layout.fillWidth: true; spacing: 6
            Action {
                objectName: root.prefix+"addDraftMaskButton"; text: "补选"; Layout.fillWidth: true; implicitHeight: 28
                primary: editor.selection.tool === "brush" && editor.selection.mode === "add"
                enabled: !editor.busy
                hint: "直接在画布上涂抹，补回漏选区域；保留当前范围，可撤销"
                onClicked: workspace.correctDraft("add")
            }
            Action {
                objectName: root.prefix+"eraseDraftMaskButton"; text: "擦除"; Layout.fillWidth: true; implicitHeight: 28
                primary: editor.selection.tool === "brush" && editor.selection.mode === "subtract"
                enabled: !editor.busy
                hint: "直接在画布上涂抹，擦掉多选区域；保留当前范围，可撤销"
                onClicked: workspace.correctDraft("subtract")
            }
        }
        Action {
            objectName: root.prefix+"selectionToLayerButton"
            text: root.region ? "开始分区调整" : root.editingMask ? "保存范围修改" : "开始调整此范围"
            hint: root.region ? "为各分区建立可撤销的局部调整" : root.editingMask ? "更新此层的作用范围，保留调整参数；Ctrl+Z 可撤销" : "直接打开只作用于此范围的调整；原图保留，可撤销"
            primary: true; Layout.fillWidth: true
            enabled: !editor.busy && workspace.selectionPreviewReady && workspace.previewReady
            onClicked: root.region ? editor.selection.applyRegions() : editor.selection.applyDefault()
        }
        RowLayout { Layout.fillWidth: true
            Action { objectName: root.prefix+"discardSelectionButton"; text: root.editingMask ? "取消修改" : "取消选择"; hint: "丢弃当前范围，图层保持不变"; Layout.fillWidth: true; enabled: !editor.busy; onClicked: editor.selection.discard() }
            Action { objectName: root.prefix+"moreOutputButton"; text: root.moreOutput ? "收起" : "更多…"; subtle: true; visible: !root.region; enabled: !editor.busy; onClicked: root.moreOutput = !root.moreOutput }
        }
        Action {
            objectName: root.prefix+"acceptSelectionButton"
            text: root.editingMask ? "另建调整层" : "替换当前层的范围"
            hint: root.editingMask ? "将修改后的范围用于一个新调整层" : "用此范围覆盖当前图层的蒙版，可 Ctrl+Z 撤销"
            visible: !root.region && root.moreOutput; Layout.fillWidth: true
            enabled: !editor.busy && workspace.selectionPreviewReady && workspace.previewReady
            onClicked: editor.selection.apply(root.editingMask ? "new_layer" : "replace_mask")
        }
    }
}
