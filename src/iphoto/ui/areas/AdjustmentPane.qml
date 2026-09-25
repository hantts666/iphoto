import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    required property var workspace
    required property var editor
 visible: workspace.inspectorPage===0 && !editor.hasRegionDraft; Layout.fillWidth: true; spacing: 10
    RowLayout { Layout.fillWidth: true
        Text { text: editor.activeLayerName; color: workspace.ink; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
        Action { text: "重置"; subtle: true; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.reset() }
    }
    Caption { text: editor.hasSelectionDraft ? "请先将选区输出到图层。" : "蒙版："+editor.selectionLabel; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Canvas { id: histogram; visible: !editor.activeIsGroup; Layout.fillWidth: true; Layout.preferredHeight: 45
        onPaint: { var c=getContext("2d"); c.reset(); c.fillStyle="#23272c"; c.fillRect(0,0,width,height); c.fillStyle="#77998b"; var a=editor.histogram; for(var i=0;i<a.length;i++) c.fillRect(i*width/64,height-a[i]*height,width/64-.7,a[i]*height) }
        Connections { target: editor; function onChanged() { histogram.requestPaint() } }
    }
    RowLayout { visible: !editor.activeIsGroup; Layout.fillWidth: true
        Repeater { model: [{key:"natural",label:"自然"},{key:"warm",label:"暖光"},{key:"cool",label:"冷调"}]
            delegate: Action { required property var modelData; text: modelData.label; Layout.fillWidth: true; enabled: workspace.editingEnabled && !editor.activeIsGroup; onClicked: editor.applyPreset(modelData.key) }
        }
    }
    Repeater { model: editor.activeIsGroup ? [] : [
        {title:"明暗",keys:["exposure","contrast","highlights","shadows","whites","blacks"],open:true},
        {title:"色彩",keys:["warmth","tint","saturation","vibrance"],open:false},
        {title:"细节",keys:["sharpness","softness"],open:false}]
        delegate: FoldSection { id: toolGroup; required property var modelData; required property int index; title: modelData.title; expanded: modelData.open; objectName: "adjustmentSection_"+index
    Repeater { model: editor.tools.filter(function(t) { return toolGroup.modelData.keys.indexOf(t.key)>=0 })
        delegate: ColumnLayout { required property var modelData; Layout.fillWidth: true; spacing: 2
            RowLayout { Layout.fillWidth: true
                Text { text: modelData.label; color: workspace.ink; font.pixelSize: 11 }
                Action { text: "解锁"; visible: editor.lockedFields.indexOf(modelData.key)>=0; enabled: workspace.editingEnabled && !editor.activeIsGroup; subtle: true; implicitHeight: 20; font.pixelSize: 9; onClicked: editor.unlock(modelData.key) }
                Item { Layout.fillWidth: true }
                Caption { text: Number(editor.parameters[modelData.key] || 0).toFixed(modelData.key==="exposure" ? 2 : 0); font.family: "Consolas" }
            }
            FineSlider { objectName: "parameter_"+modelData.key; Layout.fillWidth: true; implicitHeight: 22; from: modelData.from; to: modelData.to; stepSize: modelData.step; value: editor.parameters[modelData.key] || 0; enabled: workspace.editingEnabled && !editor.activeIsGroup; onMoved: editor.setParameter(modelData.key,value); onPressedChanged: { if(!pressed) editor.finishGesture() } }
        }
    }
        }
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
