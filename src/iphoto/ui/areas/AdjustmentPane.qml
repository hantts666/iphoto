import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: adjustRoot
    required property var workspace
    required property var editor
    readonly property var selection: editor.selection
    readonly property var reviewedRepair: selection.reviewedRepair
    property bool compactHeader: false
    property string paramMenuKey: ""
    property int mixIndex: 0
    readonly property var mixColors: ["red","orange","yellow","green","aqua","blue","purple","magenta"]
    signal parameterRevealRequested(Item row, Item section)
    function commitText() {
        for (var i = 0; i < toolGroups.count; ++i)
            if (!toolGroups.itemAt(i).commitText()) return false
        return true
    }
    function revealParameter(key) {
        if (key.indexOf("hsl_") === 0) {
            mixIndex = mixColors.indexOf(key.split("_")[1])
            Qt.callLater(function() { revealParameterRow(key) })
        } else revealParameterRow(key)
    }
    function revealParameterRow(key) {
        for (var i = 0; i < toolGroups.count; ++i) {
            var group = toolGroups.itemAt(i)
            if (group.parameterKeys.indexOf(key) < 0) continue
            group.expanded = true
            var row = group.parameterRow(key)
            if (row) parameterRevealRequested(row, group)
            return
        }
    }
    Connections {
        target: selection
        function onParameterFocusRequested(lid, key) {
            Qt.callLater(function() {
                if (selection.pickedLayerId === lid && editor.activeLayerId === lid
                        && !editor.hasSelectionDraft && !editor.hasRegionDraft)
                    adjustRoot.revealParameter(key)
            })
        }
        function onRepairFocusRequested(lid) {
            Qt.callLater(function() {
                if (selection.pickedLayerId === lid && editor.activeLayerId === lid
                        && !editor.hasSelectionDraft && !editor.hasRegionDraft && repairControls.visible)
                    adjustRoot.parameterRevealRequested(repairControls, repairControls)
            })
        }
    }
    Layout.fillWidth: true; spacing: 10
    RowLayout { Layout.fillWidth: true
        Text { text: editor.activeLayerName; color: workspace.ink; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
        Action { objectName: "unpickButton"; text: "返回范围"; subtle: true; hint: "取消选中图层，回到元素与选区模块"; onClicked: selection.clearPick() }
        Action { objectName: "autoAdjustButton"; text: "自动"; subtle: true; hint: "按直方图统计自动设置曝光、对比、阴影高光、冷暖与色偏（本地计算，可撤销）"; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.autoAdjust() }
        Action { text: "重置"; subtle: true; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.reset() }
    }
    Caption { text: editor.hasSelectionDraft ? "请先将选区输出到图层。" : "蒙版："+editor.selectionLabel; wrapMode: Text.Wrap; Layout.fillWidth: true }
    ColumnLayout {
        id: repairControls; objectName: "repairControls"
        visible: editor.activeRepairInfo.count > 0
        Layout.fillWidth: true; spacing: 4
        Caption {
            objectName: "repairStrengthCaption"
            text: "修复笔画 " + editor.activeRepairInfo.count + (adjustRoot.reviewedRepair.index ? " · 当前第" + adjustRoot.reviewedRepair.index + "笔" : "")
                  + " · " + (editor.activeRepairInfo.isolated ? "修复强度 " : "图层强度 ") + Math.round(editor.activeRepairInfo.strength || 0) + "%"
                  + (!editor.activeRepairInfo.displayed ? " · 效果未显示" : editor.activeRepairInfo.effective_strength < editor.activeRepairInfo.strength ? " · 受所属组强度限制" : "")
            wrapMode: Text.Wrap; Layout.fillWidth: true
        }
        FineSlider {
            objectName: "repairStrengthSlider"; Layout.fillWidth: true; implicitHeight: 22
            visible: editor.activeRepairInfo.isolated || false
            from: 0; to: 100; stepSize: 1; value: editor.layerOpacity
            enabled: workspace.editingEnabled
            onMoved: editor.setOpacity(value)
            onPressedChanged: if (!pressed) editor.finishGesture()
        }
        Action {
            objectName: "reviewLayerRepairsButton"; text: editor.activeRepairInfo.count > 1 ? "逐笔查看修复" : "查看修复细节"
            Layout.fillWidth: true; enabled: workspace.editingEnabled
            hint: "定位到实际修复笔画，放大至100%或适应笔画；再次点击查看下一笔。不会新增修复。"
            onClicked: workspace.reviewRepairs([editor.activeLayerId])
        }
    }
    Rectangle {
        objectName: "inactiveLayerNotice"
        visible: editor.hasImage && !editor.activeDisplay.enabled
        Layout.fillWidth: true; implicitHeight: displayNoticeContent.implicitHeight + 16
        color: "#3d3930"; radius: 4; border.color: "#76674b"
        ColumnLayout {
            id: displayNoticeContent
            anchors.fill: parent; anchors.margins: 8; spacing: 6
            Text { text: editor.activeDisplay.reason; textFormat: Text.PlainText; color: "#e2d1aa"; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Action {
                objectName: "restoreLayerDisplayButton"; text: "显示当前效果"
                Layout.fillWidth: true; enabled: workspace.editingEnabled
                hint: "显示当前层及所属组；将不透明度为 0% 的层或组设为 100%。所属组中的其他可见图层也会显示。可一步撤销。"
                onClicked: editor.restoreLayerDisplay()
            }
        }
    }
    Canvas { id: histogram; visible: !editor.activeIsGroup; Layout.fillWidth: true; Layout.preferredHeight: adjustRoot.compactHeader ? 32 : 45
        onPaint: { var c=getContext("2d"); c.reset(); c.fillStyle="#23272c"; c.fillRect(0,0,width,height); c.fillStyle="#77998b"; var a=editor.histogram; for(var i=0;i<a.length;i++) c.fillRect(i*width/64,height-a[i]*height,width/64-.7,a[i]*height) }
        Connections { target: editor; function onChanged() { histogram.requestPaint() } }
    }
    Caption { visible: !editor.activeIsGroup && !adjustRoot.compactHeader; text: "快捷效果 · 可 Ctrl+Z 撤销"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    RowLayout { visible: !editor.activeIsGroup; Layout.fillWidth: true
        Repeater { model: [{key:"natural",label:"自然"},{key:"warm",label:"暖光"},{key:"cool",label:"冷调"}]
            delegate: Action { required property var modelData; text: modelData.label; Layout.fillWidth: true; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.applyPreset(modelData.key) }
        }
        Action { objectName: "skinSmoothPresetButton"; text: "轻磨皮"; Layout.fillWidth: true; hint: "将当前图层范围的磨皮设为 35，并打开强度微调；建议先选中面部皮肤，可撤销"; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: { editor.setParameter("skin_smoothing", 35); editor.finishGesture(); adjustRoot.revealParameter("skin_smoothing") } }
    }
    Repeater { id: toolGroups; model: editor.activeIsGroup ? [] : [
        {title:"明暗",keys:["exposure","contrast","highlights","shadows","whites","blacks"],open:true},
        {title:"色彩",keys:["warmth","tint","saturation","vibrance"],open:false},
        {title:"细节与人像",keys:["skin_smoothing","sharpness","softness"],open:false},
        {title:"RGB 通道",keys:["red_channel","green_channel","blue_channel"],open:false},
        {title:"分色调色 · HSL",keys:[],open:false,mixer:true}]
        delegate: FoldSection { id: toolGroup; required property var modelData; required property int index; title: modelData.title; expanded: modelData.open; objectName: "adjustmentSection_"+index
            readonly property var parameterKeys: modelData.mixer
                ? ["hue","saturation","lightness"].map(function(k) {return "hsl_"+adjustRoot.mixColors[adjustRoot.mixIndex]+"_"+k})
                : modelData.keys
            function commitText() {
                for (var i = 0; i < toolRows.count; ++i)
                    if (!toolRows.itemAt(i).commitText()) return false
                return true
            }
            function parameterRow(key) {
                for (var i = 0; i < toolRows.count; ++i) {
                    var row = toolRows.itemAt(i)
                    if (row.modelData.key === key) return row
                }
                return null
            }
            SelectBox {
                objectName:"colorMixerChoice"; visible:toolGroup.modelData.mixer || false
                model:["红色","橙色","黄色","绿色","青色","蓝色","紫色","洋红"]
                currentIndex:adjustRoot.mixIndex; Layout.fillWidth:true
                onActivated:function(index) {
                    if (toolGroup.commitText()) adjustRoot.mixIndex=index
                    else currentIndex=adjustRoot.mixIndex
                }
            }
            Caption { visible:toolGroup.index===3; text:"独立调整红、绿、蓝通道；0 为原值，-100 移除此通道。"; font.pixelSize:10; wrapMode:Text.Wrap; Layout.fillWidth:true }
            Caption { visible:toolGroup.modelData.mixer || false; text:"选择颜色后调整色相、饱和度与明度，相邻颜色平滑过渡；灰色保持。"; font.pixelSize:10; wrapMode:Text.Wrap; Layout.fillWidth:true }
    Repeater { id: toolRows; model: toolGroup.parameterKeys.map(function(key) { return editor.tools.find(function(t) { return t.key===key }) })
        delegate: Item {
            required property var modelData
            objectName: "parameterRow_"+modelData.key
            function commitText() { return valueInput.submit(true) }
            Layout.fillWidth: true
            implicitHeight: paramRow.implicitHeight
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.RightButton
                onClicked: function(mouse) { adjustRoot.paramMenuKey = modelData.key; paramMenu.popup() }
            }
            ColumnLayout {
                id: paramRow
                anchors.fill: parent
                spacing: 2
                RowLayout { Layout.fillWidth: true
                    Text { text: modelData.label; color: workspace.ink; font.pixelSize: 11 }
                    Item { Layout.fillWidth: true }
                    ParameterValueField {
                        id: valueInput; objectName: "parameterValue_"+modelData.key
                        editor: adjustRoot.editor; parameterKey: modelData.key
                        enabled: workspace.editingEnabled && !editor.activeIsGroup
                        Layout.preferredWidth: 56
                    }
                }
                FineSlider { objectName: "parameter_"+modelData.key; Layout.fillWidth: true; implicitHeight: 22; from: modelData.from; to: modelData.to; stepSize: modelData.step; value: editor.parameters[modelData.key] || 0; enabled: workspace.editingEnabled && !editor.activeIsGroup; onMoved: editor.setParameter(modelData.key,value); onPressedChanged: { if(!pressed) editor.finishGesture() } }
                Caption { visible: modelData.key==="skin_smoothing"; text: "只作用于当前图层范围；选中面部皮肤后效果更准确。"; font.pixelSize: 9; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Caption { visible: editor.lockedFields.indexOf(modelData.key)>=0; text: "已锁定 · 右键可解锁"; font.pixelSize: 9; Layout.fillWidth: true }
            }
        }
    }
        }
    }
    Menu {
        id: paramMenu
        MenuItem { objectName: "paramMenuUnlock"; text: "解锁此项（交还 AI）"; visible: editor.lockedFields.indexOf(adjustRoot.paramMenuKey)>=0; onTriggered: editor.unlock(adjustRoot.paramMenuKey) }
        MenuItem { objectName: "paramMenuReset"; text: "重置此项为 0"; onTriggered: { editor.setParameter(adjustRoot.paramMenuKey, 0); editor.finishGesture() } }
    }
    Caption { visible: !editor.activeIsGroup; text: "手动调整的参数会锁定，AI 将保留。"; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.bottomMargin: 12 }
    ColumnLayout { visible: editor.activeIsGroup; Layout.fillWidth: true; spacing: 14
        Caption { text: "这是图层组。组内调整先合成，再应用组的蒙版和不透明度；隐藏组会隐藏所有后代。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Action { text: "在组内新建调整层"; primary: true; Layout.fillWidth: true; enabled: workspace.editingEnabled; onClicked: editor.addGlobalLayer() }
        Action { text: "编辑组蒙版"; Layout.fillWidth: true; enabled: workspace.editingEnabled; onClicked: workspace.reviewMask() }
        Action { text: "复制整个组"; Layout.fillWidth: true; enabled: workspace.editingEnabled; onClicked: editor.duplicateLayer() }
        Caption { text: "下方可将图层移入其他组，或向外移动一级。最多四级组嵌套，合计32个图层与组。"; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.bottomMargin: 12 }
    }
}
