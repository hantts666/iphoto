import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: adjustRoot
    required property var workspace
    required property var editor
    readonly property var selection: editor.selection
    property string paramMenuKey: ""
    Layout.fillWidth: true; spacing: 10
    RowLayout { Layout.fillWidth: true
        Text { text: editor.activeLayerName; color: workspace.ink; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
        Action { objectName: "unpickButton"; text: "返回范围"; subtle: true; hint: "取消选中图层，回到元素与选区模块"; onClicked: selection.clearPick() }
        Action { objectName: "autoAdjustButton"; text: "自动"; subtle: true; hint: "按直方图统计自动设置曝光、对比、阴影高光、冷暖与色偏（本地计算，可撤销）"; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.autoAdjust() }
        Action { text: "重置"; subtle: true; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.reset() }
    }
    Caption { text: editor.hasSelectionDraft ? "请先将选区输出到图层。" : "蒙版："+editor.selectionLabel; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Canvas { id: histogram; visible: !editor.activeIsGroup; Layout.fillWidth: true; Layout.preferredHeight: 45
        onPaint: { var c=getContext("2d"); c.reset(); c.fillStyle="#23272c"; c.fillRect(0,0,width,height); c.fillStyle="#77998b"; var a=editor.histogram; for(var i=0;i<a.length;i++) c.fillRect(i*width/64,height-a[i]*height,width/64-.7,a[i]*height) }
        Connections { target: editor; function onChanged() { histogram.requestPaint() } }
    }
    Caption { visible: !editor.activeIsGroup; text: "快捷效果 · 可 Ctrl+Z 撤销"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    RowLayout { visible: !editor.activeIsGroup; Layout.fillWidth: true
        Repeater { model: [{key:"natural",label:"自然"},{key:"warm",label:"暖光"},{key:"cool",label:"冷调"}]
            delegate: Action { required property var modelData; text: modelData.label; Layout.fillWidth: true; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.applyPreset(modelData.key) }
        }
        Action { objectName: "skinSmoothPresetButton"; text: "轻磨皮"; Layout.fillWidth: true; hint: "将当前图层范围的磨皮设为 35；建议先选中面部皮肤，可撤销"; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: { editor.setParameter("skin_smoothing", 35); editor.finishGesture() } }
    }
    Repeater { model: editor.activeIsGroup ? [] : [
        {title:"明暗",keys:["exposure","contrast","highlights","shadows","whites","blacks"],open:true},
        {title:"色彩",keys:["warmth","tint","saturation","vibrance"],open:false},
        {title:"细节与人像",keys:["skin_smoothing","sharpness","softness"],open:false}]
        delegate: FoldSection { id: toolGroup; required property var modelData; required property int index; title: modelData.title; expanded: modelData.open; objectName: "adjustmentSection_"+index
    Repeater { model: editor.tools.filter(function(t) { return toolGroup.modelData.keys.indexOf(t.key)>=0 })
        delegate: Item {
            required property var modelData
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
                    Caption { text: Number(editor.parameters[modelData.key] || 0).toFixed(modelData.key==="exposure" ? 2 : 0); font.family: "Consolas" }
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
