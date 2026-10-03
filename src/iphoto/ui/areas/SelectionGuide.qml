import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "../components/selection"

// Unified range module: every way to define a range (tools, text, scene
// elements with hover preview) plus edge refinement. Adjustments belong to
// layers: pick a layer in the list below to edit its parameters.
ColumnLayout {
    id: guide
    objectName: "selectionGuideRoot"
    required property var workspace
    required property var editor
    readonly property var selection: editor.selection
    property alias refineCard: edgeCard
    property string elementMenuId: ""
    function focusPrompt() { selectionPrompt.forceActiveFocus() }
    Layout.fillWidth: true; spacing: 9

    Menu {
        id: elementMenu
        objectName: "sceneElementMenu"
        MenuItem { objectName: "elemMenuReplace"; text: "设为当前范围"; onTriggered: selection.rowSelect(guide.elementMenuId, "replace") }
        MenuItem { objectName: "elemMenuAdd"; text: "加入当前范围"; onTriggered: selection.rowSelect(guide.elementMenuId, "add") }
        MenuItem { objectName: "elemMenuSubtract"; text: "从当前范围减去"; enabled: editor.hasSelectionDraft; onTriggered: selection.rowSelect(guide.elementMenuId, "subtract") }
    }

    RowLayout { Layout.fillWidth: true; spacing: 6
        Caption {
            objectName: "draftStateCaption"
            Layout.fillWidth: true
            text: editor.hasSelectionDraft
                ? selection.editingLayerMask
                    ? "正在修正“" + selection.maskEditLayerName + "”的范围 · 修改尚未保存"
                    : "当前范围：" + editor.draftLabel + " · 尚未开始调整"
                : editor.hasRegionDraft ? "分区预览中" : "悬停元素行可在画布预览范围；点击即设为当前范围。"
            color: editor.hasSelectionDraft ? "#c6d9ca" : Theme.muted
            wrapMode: Text.Wrap
        }
        Action { text: selection.editingLayerMask ? "取消修改" : "取消选择"; subtle: true; implicitHeight: 24; font.pixelSize: 10; visible: editor.hasSelectionDraft; enabled: !editor.busy; hint: "丢弃当前范围，图层保持不变"; onClicked: selection.discard() }
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: sourceCol.implicitHeight + 20
        radius: 6; color: "#262b31"
        ColumnLayout {
            id: sourceCol
            anchors.fill: parent; anchors.margins: 10; spacing: 7
            RowLayout { Layout.fillWidth: true; implicitWidth: 0; spacing: 4
                Text { text: "范围"; color: Theme.ink; font.bold: true; font.pixelSize: 12; Layout.fillWidth: true }
                Action { objectName: "pixelSelectButton"; text: "点选 S"; primary: selection.tool==="smart"; implicitHeight: 26; font.pixelSize: 10; hint: "在目标内部点击保留，Alt＋点击排除，可叠加"; enabled: editor.hasImage && !editor.busy; onClicked: selection.chooseTool("smart") }
                Action { objectName: "rectSelectButton"; text: "框选 M"; primary: selection.tool==="rect"; implicitHeight: 26; font.pixelSize: 10; hint: "拖出矩形范围"; enabled: editor.hasImage && !editor.busy; onClicked: selection.chooseTool("rect") }
                Action {
                    text: "更多"; subtle: true; implicitHeight: 26; font.pixelSize: 10; hint: "套索 / 魔棒 / 画笔 / 椭圆 / 主体 / 抠出 / 载入蒙版 / 全选反选"
                    onClicked: moreMenu.popup()
                    Menu {
                        id: moreMenu
                        MenuItem { text: "自由套索  L"; onTriggered: selection.chooseTool("polygon") }
                        MenuItem { text: "颜色魔棒  W"; onTriggered: selection.chooseTool("wand") }
                        MenuItem { text: "蒙版画笔  B"; onTriggered: selection.chooseTool("brush") }
                        MenuItem { text: "椭圆选框"; onTriggered: selection.chooseTool("ellipse") }
                        MenuSeparator {}
                        MenuItem { text: workspace.subjectAvailable ? "选择主体" : "配置主体模型…"; enabled: editor.hasImage && !editor.busy; onTriggered: workspace.subjectAvailable ? selection.refine("u2net") : workspace.openCapabilities() }
                        MenuItem { objectName: "cutoutSubjectButton"; text: "一键抠出主体"; enabled: editor.hasImage && !editor.busy && !editor.hasSelectionDraft && workspace.subjectAvailable; onTriggered: selection.cutoutSubject() }
                        MenuSeparator {}
                        MenuItem { objectName: "pixelRefinePointsButton"; text: "补点 / 排除点"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: selection.refinePixelPoints() }
                        MenuItem { text: "载入当前层蒙版"; enabled: workspace.editingEnabled; onTriggered: selection.reviewMask() }
                        MenuSeparator {}
                        MenuItem { objectName: "selectAllButton"; text: "全选  Ctrl+A"; enabled: editor.hasImage && !editor.busy; onTriggered: editor.draftAction("all") }
                        MenuItem { text: "反选  Ctrl+Shift+I"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: editor.draftAction("invert") }
                        MenuItem { text: "清空范围"; enabled: editor.hasSelectionDraft && !editor.busy; onTriggered: editor.draftAction("clear") }
                    }
                }
            }
            Field { id: selectionPrompt; objectName: "selectionDescriptionInput"; Layout.fillWidth: true; implicitHeight: 30; placeholderText: "或用文字描述：天空，不要树枝"; onAccepted: selection.selectByText(text) }
            RowLayout { Layout.fillWidth: true; implicitWidth: 0
                Action { objectName: "aiSelectionButton"; text: "按描述识别"; Layout.fillWidth: true; implicitHeight: 28; hint: "有元素清单时从清单匹配（快、可复核）；无清单先分析画面"; enabled: editor.hasImage && !editor.busy && selectionPrompt.text.trim().length>0; onClicked: selection.selectByText(selectionPrompt.text) }
                Action { objectName: "directSelectionButton"; text: "AI 直接识别"; subtle: true; Layout.fillWidth: true; implicitHeight: 28; hint: "跳过清单，AI 直接在图中识别目标（云端）"; enabled: editor.hasImage && !editor.busy && selectionPrompt.text.trim().length>0; onClicked: selection.selectByTextDirect(selectionPrompt.text) }
            }
            RowLayout { Layout.fillWidth: true; implicitWidth: 0; spacing: 4
                Caption { text: "画面元素"; Layout.fillWidth: true }
                Action { objectName: "analyzeSceneButton"; text: editor.sceneObjects.length ? "重新分析" : "分析画面"; subtle: true; implicitHeight: 24; font.pixelSize: 10; enabled: editor.hasImage && !editor.busy; onClicked: editor.analyzeScene(editor.sceneObjects.length>0) }
                Action { objectName: "scenePointTool"; text: "图里点选"; subtle: true; implicitHeight: 24; font.pixelSize: 10; enabled: editor.sceneObjects.length>0 && !editor.busy; onClicked: selection.chooseTool("object") }
            }
            RowLayout { visible: editor.sceneObjects.length>0; Layout.fillWidth: true; spacing: 4
                SelectBox { id: category; objectName: "sceneCategoryBox"; model: editor.sceneCategories; Layout.preferredWidth: 168; implicitHeight: 26 }
                Action { objectName: "checkCategoryButton"; text: "选同类"; implicitHeight: 26; font.pixelSize: 10; enabled: !editor.busy; onClicked: editor.checkSceneCategory(category.currentText,true) }
            }
            Repeater {
                model: editor.sceneRowsModel
                delegate: Rectangle {
                    required property var sceneRow
                    readonly property var modelData: sceneRow
                    objectName: "sceneRow_" + modelData.id
                    Layout.fillWidth: true; implicitHeight: 30; radius: 4
                    color: modelData.pixelReady ? "#2c3a34" : "#252b31"
                    RowLayout { anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 4; spacing: 3; implicitWidth: 0
                        CheckBox {
                            objectName: "sceneCheck_" + modelData.id
                            checked: modelData.checked
                            enabled: !editor.busy
                            implicitWidth: 22; implicitHeight: 26
                            ToolTip.visible: hovered; ToolTip.delay: 500
                            ToolTip.text: "勾选后点击下方「调整已选对象」即可开始；需要修边时打开「组合…」"
                            onClicked: editor.checkSceneObject(modelData.id, checked)
                        }
                        Action {
                            id: objectButton
                            objectName: "sceneSelect_" + modelData.id
                            readonly property bool needsReview: modelData.pixelWarnings.length > 0
                            text: modelData.name; subtle: true; Layout.fillWidth: true; implicitWidth: 0; implicitHeight: 26; font.pixelSize: 11
                            hint: modelData.category + " · " + (modelData.pixelStatus === "ready" ? needsReview ? modelData.pixelWarnings.join("；") + "；选中后可补点 / 排除点" : "真实轮廓已就绪，请检查边缘" : modelData.pixelStatus === "pending" ? "真实轮廓预计算中，悬停暂显示定位框" : modelData.pixelStatus === "unavailable" ? "暂未可靠贴边；点击可重试，或用框选 / 画笔" : "尚无真实轮廓；点击时尝试贴边") + "；点击设为当前范围，右键加入/减去"
                            contentItem: RowLayout {
                                spacing: 6
                                Text { text: objectButton.text; font: objectButton.font; color: Theme.ink; Layout.fillWidth: true; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                                Caption {
                                    objectName: "sceneStatus_" + modelData.id
                                    text: modelData.pixelStatus === "ready" ? objectButton.needsReview ? "需检查" : "可选" : modelData.pixelStatus === "pending" ? "准备中" : modelData.pixelStatus === "unavailable" ? "需重选" : "待准备"
                                    color: objectButton.needsReview || modelData.pixelStatus === "unavailable" ? "#e4bd7d" : Theme.muted
                                    font.pixelSize: 10
                                }
                            }
                            enabled: !editor.busy
                            onClicked: selection.rowSelect(modelData.id, "replace")
                        }
                    }
                    // Observe movement without covering the Button/CheckBox.
                    HoverHandler {
                        objectName: "sceneRowHover_" + modelData.id
                        readonly property string objectId: modelData.id
                        enabled: workspace.objectPreviewEnabled
                        blocking: false
                        signal entered()
                        signal exited()
                        onHoveredChanged: hovered ? entered() : exited()
                        function clearHover() { if (workspace && workspace.sceneHoverId === objectId) workspace.sceneHoverId = "" }
                        onEntered: if (enabled) workspace.sceneHoverId = objectId
                        onExited: clearHover()
                        onEnabledChanged: if (!enabled) clearHover()
                        Component.onDestruction: clearHover()
                    }
                    TapHandler {
                        acceptedButtons: Qt.RightButton
                        enabled: workspace.objectPreviewEnabled
                        onTapped: { guide.elementMenuId = modelData.id; elementMenu.popup() }
                    }
                }
            }
        }
    }

    Rectangle {
        id: edgeCard
        objectName: "edgeRefineCard"
        visible: editor.hasSelectionDraft
        Layout.fillWidth: true
        implicitHeight: edgeCol.implicitHeight + 20
        radius: 6; color: "#262b31"
        ColumnLayout {
            id: edgeCol
            anchors.fill: parent; anchors.margins: 10; spacing: 7
            RowLayout { Layout.fillWidth: true; spacing: 6
                Text { text: "修边缘"; color: Theme.ink; font.bold: true; font.pixelSize: 12; Layout.fillWidth: true }
                SelectBox {
                    objectName: "maskViewBox"
                    Layout.preferredWidth: 120
                    enabled: !editor.busy
                    model: ["绿色覆盖", "黑白透明度", "调色效果"]
                    currentIndex: editor.maskView === "adjustment" ? 2 : editor.maskView === "grayscale" ? 1 : 0
                    onActivated: selection.setMaskView(currentIndex === 2 ? "adjustment" : currentIndex === 1 ? "grayscale" : "overlay")
                }
            }
            RowLayout { Layout.fillWidth: true; spacing: 6
                Action { objectName: "refineAutoButton"; text: "智能修边"; primary: true; Layout.fillWidth: true; hint: selection.autoRefineMethod !== "" ? "自动选择当前最佳修边方法" : "没有可用方法；请在扩展 → 图像能力中查看"; enabled: !editor.busy && selection.autoRefineMethod !== ""; onClicked: selection.refine("auto", matteRadius.value) }
                Action {
                    objectName: "refineMethodMenuButton"; text: "选方法"; subtle: true; implicitHeight: 30
                    hint: "手动选择修边方法"
                    enabled: !editor.busy
                    onClicked: refineMenu.popup()
                    Menu {
                        id: refineMenu
                        Repeater {
                            model: selection.refineMethods
                            delegate: MenuItem {
                                required property var modelData
                                text: modelData.name + (modelData.available ? "" : "（未配置）") + (modelData.id === "sam" ? "（仅独立范围）" : "")
                                enabled: modelData.available && !editor.busy && !(editor.hasRegionDraft && modelData.id === "sam")
                                onTriggered: selection.refine(modelData.id, matteRadius.value)
                                ToolTip.visible: hovered; ToolTip.text: modelData.description; ToolTip.delay: 400
                            }
                        }
                    }
                }
                SpinBox { id: matteRadius; objectName: "matteRadiusBox"; from: 1; to: 64; value: 8; enabled: !editor.busy; implicitWidth: 88; implicitHeight: 28 }
            }
            Action { objectName: "refineMatteButton"; text: "按原图细化透明边缘"; Layout.fillWidth: true; hint: "保留发丝等半透明过渡"; enabled: !editor.busy && editor.matteAvailable; onClicked: selection.refine("matte", matteRadius.value) }
            Action { objectName: "correctPixelPointsButton"; text: "补点 / 排除点"; Layout.fillWidth: true; hint: "点击目标内部保留，Alt＋点击排除漏选的背景；可叠加提示点"; enabled: !editor.busy; onClicked: selection.refinePixelPoints() }
            RowLayout { Layout.fillWidth: true
                Caption { text: "羽化" }
                FineSlider { from: 0; to: 5; stepSize: .1; value: editor.draftFeather; Layout.fillWidth: true; enabled: !editor.busy; onMoved: editor.setDraftFeather(value); onPressedChanged: if(!pressed) editor.finishSelectionGesture() }
                Caption { text: editor.draftFeather.toFixed(1) + "%" }
            }
            RowLayout { Layout.fillWidth: true
                Caption { text: "边缘位移" }
                FineSlider { objectName: "edgeShiftSlider"; from: -5; to: 5; stepSize: 1; value: editor.edgeShift; Layout.fillWidth: true; enabled: !editor.busy; onMoved: editor.setEdgeShift(value); onPressedChanged: if(!pressed) editor.finishSelectionGesture() }
                Caption { text: (editor.edgeShift>0 ? "+" : "") + editor.edgeShift + "%" }
            }
            RowLayout { Layout.fillWidth: true
                Caption { text: "色彩保护" }
                FineSlider { objectName: "edgeProtectionSlider"; from: 0; to: 100; stepSize: 10; value: editor.edgeProtection; Layout.fillWidth: true; enabled: !editor.busy; onMoved: editor.setEdgeProtection(value); onPressedChanged: if(!pressed) editor.finishSelectionGesture() }
                Caption { text: Math.round(editor.edgeProtection) + "%" }
            }
            Caption { visible: editor.selectionQuality.length > 0; text: editor.selectionQuality; wrapMode: Text.Wrap; color: "#c6d9ca"; Layout.fillWidth: true; font.pixelSize: 10 }
        }
    }

    Action {
        objectName: "inpaintButton"; text: "内容感知填充（移除选区内容）"; subtle: true; Layout.fillWidth: true
        visible: editor.hasSelectionDraft
        hint: selection.inpaintAvailable ? "用周围内容合成填充此范围，生成独立填充层" : "需要 OpenCV；请在扩展 → 图像能力中查看"
        enabled: !editor.busy && selection.inpaintAvailable
        onClicked: selection.apply("inpaint")
    }
    Action {
        text: editor.selectionLabel === "全图" ? "调整整张照片 →" : "继续编辑当前图层 →"; subtle: true; Layout.fillWidth: true
        visible: !editor.hasSelectionDraft && !editor.hasRegionDraft
        hint: "选中当前图层，显示它的完整调整滑杆与预设"
        onClicked: selection.pickLayer(editor.activeLayerId)
    }
    Caption { text: "点下方图层可查看或修改已有调整。"; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10; Layout.bottomMargin: 8 }
}
