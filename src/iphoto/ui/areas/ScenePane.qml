import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    required property var workspace
    required property var editor
    visible: workspace.inspectorPage===2 && !editor.hasRegionDraft
    Layout.fillWidth: true; spacing: 9
    Text { text: "画面元素"; color: Theme.ink; font.pixelSize: 15; font.bold: true }
    Caption { text: editor.sceneObjects.length ? "勾选对象后生成像素蒙版。粗定位轮廓只用于找目标；首次点击会进行本地贴边。S 可直接点选任意目标。" : "AI 分析对象位置，本地模型生成贴边选区。也可按 S 直接点选，无需云端。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
    RowLayout { Layout.fillWidth: true
        Action { objectName: "analyzeSceneButton"; text: editor.sceneObjects.length ? "重新分析" : "分析画面元素"; primary: true; Layout.fillWidth: true; enabled: editor.hasImage && !editor.busy; onClicked: editor.analyzeScene(editor.sceneObjects.length>0) }
        Action { text: "取消"; visible: editor.ai.busy; onClicked: editor.ai.cancel() }
        Action { objectName: "scenePointTool"; text: "画布点选 O"; enabled: editor.sceneObjects.length>0 && !editor.busy; onClicked: workspace.chooseTool("object") }
    }
    RowLayout { visible: editor.sceneObjects.length>0; Layout.fillWidth: true
        ComboBox { id: category; objectName: "sceneCategoryBox"; model: editor.sceneCategories; Layout.fillWidth: true; implicitHeight: 30 }
        Action { objectName: "checkCategoryButton"; text: "选同类"; enabled: !editor.busy; onClicked: editor.checkSceneCategory(category.currentText,true) }
        Action { text: "清勾选"; enabled: !editor.busy; onClicked: editor.clearObjectChecks() }
    }
    Repeater { model: editor.sceneObjects
        delegate: Rectangle { required property var modelData; Layout.fillWidth: true; implicitHeight: 45; radius: 4; color: modelData.checked ? "#3c554b" : "#252b31"
            RowLayout { anchors.fill: parent; anchors.leftMargin: 3; anchors.rightMargin: 8; spacing: 3
                CheckBox { objectName: "sceneCheck_"+modelData.id; checked: modelData.checked; enabled: !editor.busy; onClicked: editor.checkSceneObject(modelData.id,checked) }
                ColumnLayout { Layout.fillWidth: true; spacing: 2
                    Text { text: modelData.name; textFormat: Text.PlainText; color: Theme.ink; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                    Caption { text: modelData.category+" · "+(modelData.pixelReady ? "像素已缓存" : "待像素分割"); font.pixelSize: 9 }
                }
            }
        }
    }
    Caption { text: editor.sceneSummary; visible: editor.sceneObjects.length>0; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: 10; Layout.bottomMargin: 12 }
}
