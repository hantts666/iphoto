import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "../components/selection"

// Navigation is state-driven, no tabs: a picked layer shows its adjustments,
// no picked layer shows the unified range module (sources + scene elements).
Rectangle {
    required property var workspace
    required property var editor
    readonly property var selection: editor.selection
    readonly property bool rangeMode: selection.pickedLayerId === "" && !workspace.regionModal
    readonly property var propertyScroll: workspace.regionModal ? regionScroll : rangeMode ? rangeScroll : adjustmentScroll
    property Item pendingParameterRow: null
    property Item pendingParameterSection: null
    property string pendingParameterLayerId: ""
    property real maskReturnY: -1
    property string maskReturnLayerId: ""
    property bool pendingMaskReturn: false
    function rememberMaskPosition() {
        maskReturnY = rangeMode ? -1 : propertyScroll.contentItem.contentY
        maskReturnLayerId = rangeMode ? "" : selection.pickedLayerId
    }
    function clearMaskPosition() { maskReturnY = -1; maskReturnLayerId = ""; pendingMaskReturn = false }
    function restoreMaskPosition() {
        var y = maskReturnY, lid = maskReturnLayerId
        clearMaskPosition()
        if (y < 0 || rangeMode || workspace.regionModal || editor.hasSelectionDraft
                || selection.pickedLayerId !== lid || editor.activeLayerId !== lid) return
        var flick = propertyScroll.contentItem
        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, y))
    }
    Connections {
        target: selection
        function onDraftEnded() { if (inspectorRoot.maskReturnLayerId) inspectorRoot.pendingMaskReturn = true }
    }
    Connections {
        target: editor
        function onImageOpened() {
            inspectorRoot.clearMaskPosition()
            rangeScroll.contentItem.contentY = 0
            adjustmentScroll.contentItem.contentY = 0
            regionScroll.contentItem.contentY = 0
        }
    }
    function queueParameterReveal(row, section) {
        pendingParameterRow = row
        pendingParameterSection = section
        pendingParameterLayerId = selection.pickedLayerId
    }
    function positionParameter() {
        var row = pendingParameterRow
        var section = pendingParameterSection
        var lid = pendingParameterLayerId
        pendingParameterRow = null
        pendingParameterSection = null
        pendingParameterLayerId = ""
        if (!row || !section || !row.visible || !lid || rangeMode
                || selection.pickedLayerId !== lid || workspace.regionModal) return
        var flick = propertyScroll.contentItem
        var top = section.mapToItem(propertyScroll, 0, 0).y
        var bottom = row.mapToItem(propertyScroll, 0, row.height).y
        if (top >= 8 && bottom <= propertyScroll.height - 8) return
        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height,
                                             flick.contentY + Math.max(top - 8, bottom - propertyScroll.height + 8)))
    }
    // Calculate bounds after the expanded section has been laid out and drawn.
    // A timer can fire before the scroll area's new content height is available.
    Connections {
        target: workspace
        function onFrameSwapped() {
            if (inspectorRoot.pendingMaskReturn) inspectorRoot.restoreMaskPosition()
            if (inspectorRoot.pendingParameterRow) inspectorRoot.positionParameter()
        }
    }
    function commitText() {
        if (!adjustmentPane.commitText()) return false
        layersPane.commitText()
        return true
    }
    function focusSelectionInput() { rangePane.focusPrompt() }
    function showRefine() {
        if (!rangeMode || !rangePane.refineCard.visible) return
        var flick = propertyScroll.contentItem
        var top = rangePane.refineCard.mapToItem(propertyScroll, 0, 0).y
        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height,
                                             flick.contentY + top - propertyScroll.height * 0.12))
    }
 id: inspectorRoot; Layout.preferredWidth: 316; Layout.fillHeight: true; color: workspace.panel
    ColumnLayout { anchors.fill: parent; spacing: 0
        Rectangle { visible: workspace.regionModal; Layout.fillWidth: true; Layout.preferredHeight: 30; color: "#35443e"
            Caption { anchors.centerIn: parent; text: "分区草稿进行中 · 确认或取消后返回" }
        }
        Divider {}
        // Each mode owns its scroll geometry and position. Hidden layout items
        // keep their viewport size, so a taller sibling cannot clamp it.
        ScrollView { id: adjustmentScroll; objectName: inspectorRoot.rangeMode || workspace.regionModal ? "adjustmentPropertyScroll" : "propertyScroll"
            visible: !inspectorRoot.rangeMode && !workspace.regionModal
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout { width: adjustmentScroll.availableWidth-28; x: 14; spacing: 11
                AdjustmentPane { id: adjustmentPane; workspace: inspectorRoot.workspace; editor: inspectorRoot.editor; compactHeader: adjustmentScroll.height < 280; onParameterRevealRequested: function(row, section) { inspectorRoot.queueParameterReveal(row, section) } }
            }
        }
        ScrollView { id: rangeScroll; objectName: inspectorRoot.rangeMode ? "propertyScroll" : "rangePropertyScroll"
            visible: inspectorRoot.rangeMode
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout { width: rangeScroll.availableWidth-28; x: 14; spacing: 11
                SelectionGuide { id: rangePane; workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
            }
        }
        ScrollView { id: regionScroll; objectName: workspace.regionModal ? "propertyScroll" : "regionPropertyScroll"
            visible: workspace.regionModal
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout { width: regionScroll.availableWidth-28; x: 14; spacing: 11
                RegionPane { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
            }
        }
        Rectangle {
            visible: !inspectorRoot.rangeMode && !workspace.regionModal && !editor.activeIsGroup
            Layout.fillWidth: true; Layout.preferredHeight: editor.activeRepairInfo.count > 0 && !editor.activeRepairInfo.isolated ? 82 : 46; color: "#303e38"
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 8; spacing: 6
                RowLayout {
                    visible: !editor.activeRepairInfo.isolated; Layout.fillWidth: true; spacing: 6
                    Action { objectName: "reviewLayerAdjustmentButton"; text: "查看效果"; visible: editor.canReviewAdjustment; Layout.fillWidth: true; enabled: workspace.editingEnabled; hint: "定位到此层实际范围，以100%查看局部效果；再次点击查看下一处"; onClicked: workspace.reviewAdjustments([editor.activeLayerId]) }
                    Action { objectName: "addLayerMaskButton"; text: "补选范围"; Layout.fillWidth: true; enabled: workspace.editingEnabled; hint: "直接用画笔补上漏选，保存后继续调整当前图层"; onClicked: workspace.correctMask("add") }
                    Action { objectName: "eraseLayerMaskButton"; text: "擦除范围"; Layout.fillWidth: true; enabled: workspace.editingEnabled; hint: "直接用画笔排除嘴唇、五官或多选的背景，保存后继续调整当前图层"; onClicked: workspace.correctMask("subtract") }
                }
                RowLayout {
                    // Keep these actions at the bottom when a pending tone value
                    // makes the repair layer mixed during a pointer press.
                    visible: editor.activeRepairInfo.count > 0; Layout.fillWidth: true; spacing: 6
                    Action { objectName: "reviewLayerRepairsFooterButton"; text: editor.selection.reviewedRepair.index ? "下一笔" : editor.activeRepairInfo.count > 1 ? "逐笔查看" : "查看修复"; Layout.fillWidth: true; enabled: workspace.editingEnabled; hint: "以原图细节查看实际修复位置，再次点击查看下一笔"; onClicked: workspace.reviewRepairs([editor.activeLayerId]) }
                    Action {
                        objectName: "deleteReviewedRepairButton"; property string repairToken: ""
                        text: editor.selection.reviewedRepair.removes_layer ? "删除修复层" : editor.selection.reviewedRepair.index ? "删除第" + editor.selection.reviewedRepair.index + "笔" : "删除单笔"
                        Layout.fillWidth: true; enabled: workspace.editingEnabled && !!editor.selection.reviewedRepair.index
                        hint: "先查看要删除的笔画；最后一笔的独立修复层会一并移除，可 Ctrl+Z 撤销"
                        onPressed: repairToken = editor.selection.reviewedRepair.token || ""
                        onClicked: workspace.deleteReviewedRepair(repairToken)
                    }
                    Action { objectName: "continueLayerRepairButton"; text: "继续修复"; visible: editor.activeRepairInfo.isolated || false; Layout.fillWidth: true; enabled: workspace.editingEnabled; hint: "继续用修复画笔处理，保留已有调色"; onClicked: workspace.chooseTool("heal") }
                }
            }
        }
        Rectangle {
            // A draft owns the visible adjustment action. Object acquisition
            // stays secondary so it cannot silently restore an older cache.
            visible: inspectorRoot.rangeMode && editor.sceneObjects.length>0
                     && (!editor.hasSelectionDraft || editor.checkedObjectCount>0)
            Layout.fillWidth: true; Layout.preferredHeight: editor.hasSelectionDraft ? 46 : 70; color: "#303e38"
            ColumnLayout { anchors.fill: parent; anchors.margins: 8; spacing: 4
                Caption { visible: !editor.hasSelectionDraft; text: editor.checkedObjectCount>0 ? "已勾选 "+editor.checkedObjectCount+" 项 · 可直接开始局部调整" : "勾选画面元素后，在这里开始调整" }
                RowLayout { Layout.fillWidth: true; spacing: 4
                    Action { objectName: "adjustCheckedObjectsButton"; text: "调整已选对象"; visible: !editor.hasSelectionDraft; primary: true; Layout.fillWidth: true; enabled: !editor.busy && editor.checkedObjectCount>0; hint: "自动识别勾选对象的边缘，并直接打开可撤销的局部调整"; onClicked: editor.adjustCheckedObjects() }
                    Action { objectName: "objectCombineMenuButton"; text: editor.hasSelectionDraft ? "勾选对象：加入 / 减去…" : "组合…"; Layout.fillWidth: editor.hasSelectionDraft; subtle: true; enabled: !editor.busy && editor.checkedObjectCount>0; hint: editor.hasSelectionDraft ? "把勾选对象加入、减去或相交；重选会替换当前范围，可撤销" : "需要先修边或与已有范围组合时使用"; onClicked: objectCombineMenu.popup() }
                }
            }
            Menu {
                id: objectCombineMenu
                MenuItem { objectName: "combineObjectsButton"; text: editor.hasSelectionDraft ? "用勾选对象重新选择范围" : "只建立范围，先修边"; onTriggered: editor.combineObjects("replace") }
                MenuItem { objectName: "addObjectsButton"; text: "加入当前范围"; enabled: editor.hasSelectionDraft; onTriggered: editor.combineObjects("add") }
                MenuItem { objectName: "subtractObjectsButton"; text: "从当前范围减去"; enabled: editor.hasSelectionDraft; onTriggered: editor.combineObjects("subtract") }
                MenuItem { objectName: "intersectObjectsButton"; text: "仅保留交集"; enabled: editor.hasSelectionDraft; onTriggered: editor.combineObjects("intersect") }
            }
        }
        DraftOutputBar { workspace: inspectorRoot.workspace; editor: inspectorRoot.editor; onRefineRequested: inspectorRoot.showRefine() }
        Divider {}
        LayersPane { id: layersPane; workspace: inspectorRoot.workspace; editor: inspectorRoot.editor }
    }
}
