import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    required property var workspace
    required property var editor
    function focusPrompt() { targetSection.expanded=true; selectionPrompt.forceActiveFocus() }
    visible: workspace.inspectorPage===1 && !editor.hasRegionDraft
    Layout.fillWidth: true; spacing: 8
    Text { text: "选择与蒙版"; color: workspace.ink; font.bold: true; font.pixelSize: 15 }
    Caption { text: editor.hasSelectionDraft ? "草稿 · "+editor.draftLabel : "先选目标，再修边缘，最后应用到图层。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    RowLayout { Layout.fillWidth: true
        Action { text: "新选区"; enabled: workspace.editingEnabled; Layout.fillWidth: true; onClicked: workspace.chooseTool("rect") }
        Action { text: "编辑当前层蒙版"; enabled: workspace.editingEnabled; Layout.fillWidth: true; onClicked: workspace.reviewMask() }
    }
    FoldSection { id: targetSection; objectName: "selectionTargetSection"; title: "1  选择目标"; expanded: !editor.hasSelectionDraft || workspace.selectionAiOpen
        RowLayout { Layout.fillWidth: true
            Action { objectName: "pixelSelectButton"; text: "点击目标 S"; primary: true; enabled: editor.hasImage && !editor.busy; Layout.fillWidth: true; onClicked: workspace.chooseTool("smart") }
            Action { text: "框选 M"; enabled: editor.hasImage && !editor.busy; Layout.fillWidth: true; onClicked: workspace.chooseTool("rect") }
        }
        Field { id: selectionPrompt; objectName: "selectionDescriptionInput"; Layout.fillWidth: true; placeholderText: "描述选哪里，例如天空，排除树枝"; onAccepted: editor.selectByDescription(text) }
        RowLayout { Layout.fillWidth: true
            Action { objectName: "aiSelectionButton"; text: "AI 识别"; Layout.fillWidth: true; enabled: editor.hasImage && !editor.busy && selectionPrompt.text.trim().length>0; onClicked: editor.selectByDescription(selectionPrompt.text) }
            Action { text: "画面元素"; Layout.fillWidth: true; enabled: editor.hasImage && !editor.busy; onClicked: workspace.inspectorPage=2 }
        }
        Action { objectName: "directSelectionButton"; text: "清单没有？直接识别此目标"; subtle: true; Layout.fillWidth: true; enabled: editor.hasImage && !editor.busy && selectionPrompt.text.trim().length>0; onClicked: editor.sendMessage(selectionPrompt.text,"selection") }
        Action { text: "取消 AI 请求"; visible: editor.ai.busy; onClicked: editor.ai.cancel() }
    }
    Caption { visible: editor.pixelBusy || editor.matteBusy; text: editor.status; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Action { text: "取消当前计算"; visible: editor.pixelBusy || editor.matteBusy; Layout.fillWidth: true; onClicked: editor.matteBusy ? editor.cancelMatte() : editor.cancelPixelSelection() }
    FoldSection { objectName: "selectionEdgeSection"; title: "2  检查与修边"; expanded: editor.hasSelectionDraft
        RowLayout { Layout.fillWidth: true
            Caption { text: "查看" }
            ComboBox { id: viewBox; objectName: "maskViewBox"; Layout.fillWidth: true; enabled: !editor.busy; model: ["绿色覆盖","黑白透明度","调色效果"]; currentIndex: editor.maskView==="adjustment" ? 2 : editor.maskView==="grayscale" ? 1 : 0; implicitHeight: 30; onActivated: { workspace.showMask=currentIndex<2; editor.setMaskView(currentIndex===2 ? "adjustment" : currentIndex===1 ? "grayscale" : "overlay") } }
        }
        Caption { visible: editor.maskView==="adjustment"; text: "使用当前层调色参数预览草稿。参数为零时不会有调色变化。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10 }
        RowLayout { Layout.fillWidth: true
            Caption { text: "边缘范围"; Layout.fillWidth: true }
            SpinBox { id: matteRadius; objectName: "matteRadiusBox"; from: 1; to: 64; value: 8; enabled: !editor.busy; implicitWidth: 110; implicitHeight: 30 }
            Caption { text: "原图 px" }
        }
        Action { objectName: "refineMatteButton"; text: "按原图细化边缘"; primary: true; Layout.fillWidth: true; enabled: editor.hasSelectionDraft && !editor.busy && editor.matteAvailable; onClicked: editor.refineMatte(matteRadius.value) }
        RowLayout { Layout.fillWidth: true
            Caption { text: "边缘色彩保护" }
            FineSlider { objectName: "edgeProtectionSlider"; from: 0; to: 100; stepSize: 10; value: editor.edgeProtection; Layout.fillWidth: true; enabled: editor.hasSelectionDraft && !editor.busy; onMoved: editor.setEdgeProtection(value); onPressedChanged: if(!pressed) editor.finishSelectionGesture() }
            Caption { text: Math.round(editor.edgeProtection)+"%" }
        }
        Caption { text: "上色后有亮边，可适当提高色彩保护；默认关闭。不会修复选错的对象。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10 }
        Caption { text: editor.selectionQuality; wrapMode: Text.Wrap; color: "#c6d9ca"; Layout.fillWidth: true; font.pixelSize: 10 }
    }
    FoldSection { objectName: "selectionManualSection"; title: "手动修正与更多工具"; expanded: false
        RowLayout { Layout.fillWidth: true
            Action { objectName: "pixelRefinePointsButton"; text: "补点 / 排除点"; enabled: editor.hasSelectionDraft && !editor.busy; Layout.fillWidth: true; onClicked: {workspace.chooseTool("smart"); editor.startPixelSelection(true)} }
            Action { text: "画笔 B"; enabled: editor.hasSelectionDraft && !editor.busy; Layout.fillWidth: true; onClicked: {workspace.chooseTool("brush"); workspace.selectionMode="add"} }
        }
        Caption { text: "点击保留目标；Alt＋点击排除。画笔中 X 切换补选 / 减选，[ ] 改大小。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10 }
        RowLayout { Layout.fillWidth: true
            Action { objectName: "selectAllButton"; text: "全选"; enabled: editor.hasImage && !editor.busy; onClicked: editor.draftAction("all") }
            Action { text: "清空"; enabled: editor.hasSelectionDraft && !editor.busy; onClicked: editor.draftAction("clear") }
            Action { text: "反选"; enabled: editor.hasSelectionDraft && !editor.busy; onClicked: editor.draftAction("invert") }
        }
        Action { objectName: "refineSelectionButton"; text: "重新识别轮廓"; enabled: editor.hasSelectionDraft && !editor.busy; Layout.fillWidth: true; onClicked: editor.pixelRefine() }
        Action { text: workspace.subjectAvailable ? "选择主体" : "配置主体模型"; Layout.fillWidth: true; enabled: editor.hasImage && !editor.busy; onClicked: { if(workspace.subjectAvailable) editor.refineSelection("u2net"); else workspace.openCapabilities() } }
        RowLayout { Layout.fillWidth: true
            Caption { text: "内羽化" }
            FineSlider { Layout.fillWidth: true; from: 0; to: 5; stepSize: .1; value: editor.draftFeather; enabled: editor.hasSelectionDraft && !editor.busy; onMoved: editor.setDraftFeather(value); onPressedChanged: if(!pressed) editor.finishSelectionGesture() }
            Caption { text: editor.draftFeather.toFixed(1)+"%" }
        }
    }
}
