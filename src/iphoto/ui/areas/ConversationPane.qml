import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    required property var workspace
    required property var editor
    function sendPrompt() { if(editor.ai.enabled && !editor.ai.ready) { workspace.openAISettings(); return } editor.sendMessage(prompt.text,["edit","advice","regions"][chatMode.currentIndex]) }
 visible: workspace.chatOpen; Layout.fillWidth: true; Layout.preferredHeight: workspace.height<800 ? 176 : 224; color: "#292e34"
    ColumnLayout { anchors.fill: parent; anchors.margins: 11; spacing: 7
        RowLayout { Layout.fillWidth: true
            Text { text: "AI 修图助手"; font.bold: true; color: workspace.ink }
            Caption { text: "对话随项目保存" }
            Item { Layout.fillWidth: true }
            Action { text: "导出对话"; implicitHeight: 23; subtle: true; enabled: editor.conversation.length>0; onClicked: workspace.openChatExport() }
            Action { text: "收起⌄"; implicitHeight: 23; subtle: true; onClicked: workspace.chatOpen=false }
        }
        ListView { id: chatList; objectName: "conversationList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 7; model: editor.conversation
            ScrollBar.vertical: ScrollBar {}
            onCountChanged: Qt.callLater(positionViewAtEnd)
            Text { visible: chatList.count===0; width: parent.width-12; text: "调整当前图层、讨论建议，或让 AI 规划多个区域分别修图。\n需要选择物体时，使用右侧“选区”面板。"; color: workspace.muted; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.45 }
            delegate: Rectangle { required property var modelData; width: chatList.width-10; height: messageColumn.implicitHeight+16; radius: 4; color: modelData.role==="error" ? "#503b37" : modelData.role==="user" ? "#34423f" : "#323840"
                ColumnLayout { id: messageColumn; x: 9; y: 8; width: parent.width-18; spacing: 4
                    RowLayout { Layout.fillWidth: true
                        Caption { text: (modelData.role==="user" ? "你" : modelData.role==="assistant" ? "AI" : modelData.role==="error" ? "未完成" : "记录")+" · "+modelData.layer_name; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 10 }
                        Action { text: "复制"; subtle: true; implicitHeight: 20; font.pixelSize: 10; onClicked: editor.copyMessage(modelData.id) }
                    }
                    TextEdit { text: modelData.text; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: workspace.ink; font.pixelSize: 11; Layout.fillWidth: true; Layout.preferredHeight: contentHeight }
                    RowLayout { visible: modelData.role==="assistant"; Layout.fillWidth: true
                        Caption { text: modelData.model+" · "+(({catalog:"画面清单",applied:"已应用",proposed:"建议未应用",stale:"建议已过期",draft:"待检查选区",region_draft:"待检查分区",confirmed:"选区已确认",discarded:"已取消",unsupported:"当前能力不支持"})[modelData.state] || ""); Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 9 }
                        Action { text: "应用建议"; visible: modelData.state==="proposed"; implicitHeight: 24; primary: true; enabled: workspace.editingEnabled; onClicked: editor.applyAdvice(modelData.id) }
                    }
                }
            }
        }
        RowLayout { Layout.fillWidth: true; spacing: 6
            ComboBox { id: chatMode; objectName: "chatModeBox"; model: ["当前图层","只给建议","自动分区"]; implicitWidth: 110; implicitHeight: 34 }
            Field { id: prompt; objectName: "descriptionInput"; Layout.fillWidth: true; placeholderText: chatMode.currentIndex===2 ? "例如：人物提亮，背景压暗，分别调整" : "描述修图要求…"; onAccepted: sendPrompt() }
            Action { objectName: "applyDescriptionButton"; text: editor.ai.enabled && !editor.ai.ready ? "连接 AI" : chatMode.currentIndex===2 ? "规划分区" : chatMode.currentIndex===1 ? "获取建议" : editor.ai.enabled ? "AI 修图" : "应用规则"; primary: true; enabled: workspace.editingEnabled && (!editor.activeIsGroup || chatMode.currentIndex===2) && (prompt.text.trim().length>0 || editor.ai.enabled && !editor.ai.ready); onClicked: sendPrompt() }
            Action { objectName: "cancelAiRequest"; text: "取消"; visible: editor.ai.busy; onClicked: editor.ai.cancel() }
        }
        Caption { text: editor.activeIsGroup ? "请先选择组内调整层；也可让 AI 创建分区图层。" : editor.hasSelectionDraft ? "请先在右侧输出或取消选区，再开始修图。" : editor.hasRegionDraft ? "正在预览分区，确认后建立独立图层。" : chatMode.currentIndex===2 ? "AI 先判断区域与调整方式，预览确认后创建图层。" : "作用范围："+editor.activeLayerName+" / "+editor.selectionLabel; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 10 }
    }
}
