import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    id: chatRoot
    required property var workspace
    required property var editor
    property string msgMenuId: ""
    property string msgMenuState: ""
    function sendPrompt() {
        if (prompt.text.trim() === "") return
        if (editor.ai.enabled && !editor.ai.ready) { workspace.openAISettings(); return }
        var mode = chatMode.currentIndex===0 && editor.ai.enabled ? "auto" : ["edit", "advice", "regions"][chatMode.currentIndex]
        if (editor.sendMessage(prompt.text, mode)) {
            prompt.clear()
            prompt.forceActiveFocus()
        }
    }
    Connections {
        target: editor
        function onConversationDraftChanged() {
            if (prompt.text !== editor.conversationDraft)
                prompt.text = editor.conversationDraft
            var modeIndex = ["edit", "advice", "regions"].indexOf(editor.conversationDraftMode)
            if (modeIndex >= 0 && chatMode.currentIndex !== modeIndex)
                chatMode.currentIndex = modeIndex
        }
    }
 visible: workspace.chatOpen; Layout.fillWidth: true; Layout.preferredHeight: workspace.height<800 ? 176 : 224; color: "#292e34"
    Menu {
        id: msgMenu
        onOpened: {
            chatRoot.msgMenuState = editor.conversationMessageState(chatRoot.msgMenuId)
        }
        MenuItem { objectName: "msgMenuCopy"; text: "复制内容"; onTriggered: editor.copyMessage(chatRoot.msgMenuId) }
        MenuItem { objectName: "msgMenuApply"; text: "应用建议"; visible: chatRoot.msgMenuState === "proposed"; enabled: workspace.editingEnabled; onTriggered: editor.applyAdvice(chatRoot.msgMenuId) }
    }
    ColumnLayout { anchors.fill: parent; anchors.margins: 11; spacing: 7
        RowLayout { Layout.fillWidth: true
            Text { text: "AI 修图助手"; font.bold: true; color: workspace.ink }
            Caption { text: "对话随项目保存" }
            Item { Layout.fillWidth: true }
            Action { objectName: "conversationNewMessageButton"; text: "新消息 " + chatList.unreadCount + " ↓"; implicitHeight: 23; subtle: true; visible: chatList.unreadCount > 0; onClicked: chatList.jumpToEnd() }
            Action { objectName: "exportConversationButton"; text: "导出对话"; implicitHeight: 23; subtle: true; enabled: editor.conversationCount>0 && !editor.exportingConversation; onClicked: workspace.openChatExport() }
            Action { objectName: "chatCollapseButton"; text: "收起⌄"; implicitHeight: 23; subtle: true; onClicked: workspace.chatOpen=false }
        }
        ListView { id: chatList; objectName: "conversationList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 7; model: editor.conversationModel
            property int seenCount: 0
            property int unreadCount: 0
            property bool followEnd: true
            property bool programmaticScroll: false
            property bool jumpScheduled: false
            function jumpToEnd() {
                if (jumpScheduled) return
                jumpScheduled = true
                Qt.callLater(function() {
                    chatList.jumpScheduled = false
                    chatList.programmaticScroll = true
                    chatList.forceLayout()
                    chatList.positionViewAtEnd()
                    chatList.contentY = Math.max(0, chatList.contentHeight - chatList.height)
                    chatList.followEnd = true
                    chatList.unreadCount = 0
                    chatList.programmaticScroll = false
                })
            }
            ScrollBar.vertical: ScrollBar {}
            onContentYChanged: {
                if (programmaticScroll || count <= 1) return
                if (atYEnd) { followEnd = true; unreadCount = 0 }
                else followEnd = false
            }
            onCountChanged: {
                var added = count - seenCount
                seenCount = count
                if (added <= 0) {
                    unreadCount = 0
                    followEnd = true
                    if (count > 0) chatList.jumpToEnd()
                } else if (followEnd) {
                    chatList.jumpToEnd()
                } else {
                    unreadCount += added
                }
            }
            Text { visible: chatList.count===0; width: parent.width-12; text: "直接描述想要的效果，例如“人物轻磨皮，背景保持清晰”。\nAI 会判断是调整当前图层，还是自行建立局部调整层；也可选择只给建议。"; color: workspace.muted; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.45 }
            delegate: Rectangle { required property var messageRow; objectName: "msg_"+messageRow.id; width: chatList.width-10; height: messageColumn.implicitHeight+16; radius: 4; color: messageRow.role==="error" ? "#503b37" : messageRow.role==="user" ? "#34423f" : "#323840"
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.RightButton
                    onClicked: function(mouse) { chatRoot.msgMenuId = messageRow.id; msgMenu.popup() }
                }
                ColumnLayout { id: messageColumn; x: 9; y: 8; width: parent.width-18; spacing: 4
                    RowLayout { Layout.fillWidth: true
                        Caption { text: (messageRow.role==="user" ? "你" : messageRow.role==="assistant" ? "AI" : messageRow.role==="error" ? "未完成" : "记录")+" · "+messageRow.layer_name; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 10 }
                    }
                    TextEdit { text: messageRow.text; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: workspace.ink; font.pixelSize: 11; Layout.fillWidth: true; Layout.preferredHeight: contentHeight }
                    RowLayout { visible: messageRow.role==="assistant"; Layout.fillWidth: true
                        Caption { text: messageRow.model+" · "+(({catalog:"画面清单",applied:"已应用",answered:"已回复",proposed:"建议未应用",stale:"建议已过期",draft:"待检查选区",region_draft:"待检查分区",confirmed:"选区已确认",discarded:"已取消",unsupported:"当前能力不支持"})[messageRow.state] || ""); Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 9 }
                        Action { text: "应用建议"; visible: messageRow.state==="proposed"; implicitHeight: 24; primary: true; enabled: workspace.editingEnabled; onClicked: editor.applyAdvice(messageRow.id) }
                    }
                }
            }
        }
        Rectangle { objectName: "aiRequestProgress"; visible: editor.ai.busy || editor.selection.taskKind==="pixel"; Layout.fillWidth: true; Layout.preferredHeight: 26; radius: 4; color: "#30453e"
            RowLayout { anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 6
                BusyIndicator { running: editor.ai.busy || editor.selection.taskKind==="pixel"; implicitWidth: 18; implicitHeight: 18; Layout.preferredWidth: 18; Layout.preferredHeight: 18 }
                Caption { objectName: "aiRequestProgressText"; text: editor.ai.busy ? editor.ai.requestProgress : editor.status; Layout.fillWidth: true; color: "#b9e5d2"; font.pixelSize: 11; elide: Text.ElideRight }
            }
        }
        RowLayout { Layout.fillWidth: true; spacing: 6
            SelectBox { id: chatMode; objectName: "chatModeBox"; model: ["智能修图","只给建议","分区预览"]; implicitWidth: 110; implicitHeight: 34; onCurrentIndexChanged: { if (currentIndex >= 0) editor.setConversationDraftMode(["edit", "advice", "regions"][currentIndex]) } }
            Field { id: prompt; objectName: "descriptionInput"; Layout.fillWidth: true; maximumLength: 4000; placeholderText: chatMode.currentIndex===2 ? "例如：人物提亮，背景压暗，分别调整" : "描述修图要求…"; onTextChanged: editor.setConversationDraft(text); onAccepted: sendPrompt() }
            Action { objectName: "applyDescriptionButton"; text: editor.ai.enabled && !editor.ai.ready ? "连接 AI" : chatMode.currentIndex===2 ? "预览分区" : chatMode.currentIndex===1 ? "获取建议" : editor.ai.enabled ? "AI 修图" : "应用规则"; primary: true; enabled: workspace.editingEnabled && (!editor.activeIsGroup || chatMode.currentIndex===2 || chatMode.currentIndex===0 && editor.ai.enabled) && (prompt.text.trim().length>0 || editor.ai.enabled && !editor.ai.ready); onClicked: sendPrompt() }
            Action { objectName: "cancelAiRequest"; text: "取消"; visible: editor.ai.busy || editor.selection.taskKind==="pixel"; onClicked: editor.ai.busy ? editor.ai.cancel() : editor.selection.cancelTask() }
        }
        Caption { text: editor.hasSelectionDraft ? "先完成或取消当前范围，再继续修图。" : editor.hasRegionDraft ? "正在预览分区，确认后建立独立图层。" : chatMode.currentIndex===2 ? "先预览和修边，再创建局部图层。" : chatMode.currentIndex===0 && editor.ai.enabled ? "AI 可自行选择区域并建立图层；结果可撤销、可修边。" : "作用范围："+editor.activeLayerName+" / "+editor.selectionLabel; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 10 }
    }
}
