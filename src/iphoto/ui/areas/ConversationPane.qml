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
    readonly property bool canUseDraft: editor.hasSelectionDraft && chatMode.currentIndex===0 && editor.ai.enabled
    readonly property bool canSend: editor.hasImage && !editor.busy && !editor.hasRegionDraft && (!editor.hasSelectionDraft || canUseDraft)
    function sendPrompt() {
        if (prompt.text.trim() === "") return
        if (editor.ai.enabled && !editor.ai.ready) { workspace.openAISettings(); return }
        var mode = chatMode.currentIndex===0 && editor.ai.enabled ? "auto" : ["edit", "advice", "regions"][chatMode.currentIndex]
        if (editor.sendMessage(prompt.text, mode)) {
            chatList.jumpToEnd()
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
 visible: workspace.chatOpen; Layout.fillWidth: true; Layout.preferredHeight: (workspace.height<800 ? 176 : 224) + promptScroll.implicitHeight - 34; color: "#292e34"
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
            function updateScrollIntent() {
                followEnd = atYEnd
                if (followEnd) unreadCount = 0
            }
            function jumpToEnd() {
                followEnd = true
                unreadCount = 0
                if (jumpScheduled) return
                jumpScheduled = true
                Qt.callLater(function() {
                    chatList.jumpScheduled = false
                    if (!chatList.followEnd) return
                    chatList.programmaticScroll = true
                    chatList.forceLayout()
                    chatList.positionViewAtEnd()
                    chatList.followEnd = true
                    chatList.unreadCount = 0
                    chatList.programmaticScroll = false
                })
            }
            ScrollBar.vertical: ScrollBar {
                id: chatScrollBar
                onPressedChanged: {
                    if (pressed) chatList.followEnd = false
                    else chatList.updateScrollIntent()
                }
            }
            WheelHandler {
                target: null
                onWheel: function(event) {
                    var delta = event.pixelDelta.y !== 0 ? event.pixelDelta.y : event.angleDelta.y / 120 * 40
                    if (delta === 0) { event.accepted = false; return }
                    chatList.followEnd = false
                    var end = chatList.originY + Math.max(0, chatList.contentHeight - chatList.height)
                    chatList.contentY = Math.max(chatList.originY, Math.min(end, chatList.contentY - delta))
                    chatList.updateScrollIntent()
                    event.accepted = true
                }
            }
            onDraggingChanged: {
                if (dragging) followEnd = false
                else if (!flicking) updateScrollIntent()
            }
            onMovementEnded: updateScrollIntent()
            // Row wrapping and request-progress layout changes are not user scrolling.
            onContentYChanged: {
                if (!programmaticScroll && (dragging || flicking || chatScrollBar.pressed))
                    updateScrollIntent()
            }
            onContentHeightChanged: if (followEnd) jumpToEnd()
            onHeightChanged: if (followEnd) jumpToEnd()
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
            delegate: Rectangle {
                id: messageDelegate
                required property var messageRow
                property var repairLayers: []
                property var adjustmentLayers: []
                property var failedInfo: ({})
                function refreshRepairLayers() {
                    repairLayers = messageRow.role==="assistant" && messageRow.state==="applied" ? editor.conversationRepairLayers(messageRow.id) : []
                    adjustmentLayers = messageRow.role==="assistant" && messageRow.state==="applied" ? editor.conversationAdjustmentLayers(messageRow.id) : []
                    failedInfo = messageRow.role==="error" && messageRow.state==="failed" ? editor.failedPromptInfo(messageRow.id) : ({})
                }
                Component.onCompleted: refreshRepairLayers()
                onMessageRowChanged: refreshRepairLayers()
                Connections { target: editor; function onChanged() { messageDelegate.refreshRepairLayers() } }
                objectName: "msg_"+messageRow.id; width: chatList.width-10; height: messageColumn.implicitHeight+16; radius: 4; color: messageRow.role==="error" ? "#503b37" : messageRow.role==="user" ? "#34423f" : "#323840"
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
                        Caption { text: messageRow.model+" · "+(({catalog:"画面清单",applied:"已应用",answered:"已回复",proposed:"建议未应用",stale:"建议已过期",draft:"待检查选区",region_draft:"待检查分区",confirmed:"选区已确认",discarded:"已取消",unsupported:"未执行，查看说明"})[messageRow.state] || ""); Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 9 }
                        Action { text: "应用建议"; visible: messageRow.state==="proposed"; implicitHeight: 24; primary: true; enabled: workspace.editingEnabled; onClicked: editor.applyAdvice(messageRow.id) }
                        Action {
                            objectName: "reviewMessageAdjustments_"+messageRow.id; text: "查看局部效果"
                            visible: messageDelegate.adjustmentLayers.length > 0; implicitHeight: 24; primary: true
                            enabled: workspace.editingEnabled
                            hint: "以100%逐处查看这次调整的实际范围；可按住看原图比较"
                            onClicked: workspace.reviewAdjustments(messageDelegate.adjustmentLayers)
                        }
                        Action {
                            objectName: "reviewMessageRepairs_"+messageRow.id; text: "查看修复"
                            visible: messageDelegate.repairLayers.length > 0; implicitHeight: 24; primary: true
                            enabled: workspace.editingEnabled
                            hint: "逐笔查看这次修复的实际位置；可按住看原图比较"
                            onClicked: workspace.reviewRepairs(messageDelegate.repairLayers)
                        }
                    }
                    RowLayout {
                        visible: messageRow.role==="error" && (messageDelegate.failedInfo.available || messageDelegate.failedInfo.settings)
                        Layout.fillWidth: true; spacing: 6
                        Action {
                            objectName: "retryFailedPrompt_"+messageRow.id; text: "重试"
                            visible: !!messageDelegate.failedInfo.retry; implicitHeight: 24; primary: true
                            enabled: !editor.busy && !editor.hasRegionDraft && editor.ai.ready && editor.ai.enabled && prompt.text.trim()===""
                            hint: "使用原要求重新发送；照片、图层或范围已变化时先取回文字供检查"
                            onClicked: if(editor.retryFailedPrompt(messageRow.id)) chatList.jumpToEnd()
                        }
                        Action {
                            objectName: "restoreFailedPrompt_"+messageRow.id
                            text: prompt.text.trim()!=="" && prompt.text!==messageDelegate.failedInfo.text ? "复制要求" : "取回要求"
                            visible: !!messageDelegate.failedInfo.available; implicitHeight: 24
                            enabled: !editor.busy
                            onClicked: if(editor.restoreFailedPrompt(messageRow.id)) prompt.forceActiveFocus()
                        }
                        Action {
                            objectName: "failureAISettings_"+messageRow.id; text: "AI 设置"
                            visible: !!messageDelegate.failedInfo.settings; implicitHeight: 24
                            enabled: !editor.busy; onClicked: workspace.openAISettings()
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
        }
        RowLayout { Layout.fillWidth: true; spacing: 6
            SelectBox { id: chatMode; objectName: "chatModeBox"; model: ["智能修图","只给建议","分区预览"]; implicitWidth: 110; implicitHeight: 34; onCurrentIndexChanged: { if (currentIndex >= 0) editor.setConversationDraftMode(["edit", "advice", "regions"][currentIndex]) } }
            ScrollView {
                id: promptScroll; objectName: "conversationInputScroll"
                Layout.fillWidth: true; Layout.preferredHeight: implicitHeight
                implicitHeight: Math.max(34, Math.min(78, prompt.contentHeight + prompt.topPadding + prompt.bottomPadding))
                contentWidth: availableWidth; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                background: Rectangle { radius: 4; color: "#22262b"; border.color: prompt.activeFocus ? "#73ac98" : Theme.line }
                TextArea {
                    id: prompt; objectName: "descriptionInput"
                    property int maximumLength: 4000
                    property string previousText: ""
                    color: Theme.ink; placeholderTextColor: "#909aa6"; selectByMouse: true
                    wrapMode: TextEdit.Wrap; leftPadding: 9; rightPadding: 9; topPadding: 7; bottomPadding: 7
                    background: null
                    placeholderText: chatMode.currentIndex===2 ? "例如：人物提亮，背景压暗，分别调整" : "描述修图要求…"
                    onTextChanged: {
                        if (text.length > maximumLength) {
                            // Limit the inserted span; preserve existing text after it.
                            var start = 0, end = 0, next = text, prior = previousText
                            while (start < prior.length && start < next.length && prior[start] === next[start]) ++start
                            while (end < prior.length-start && end < next.length-start
                                   && prior[prior.length-1-end] === next[next.length-1-end]) ++end
                            var inserted = next.slice(start, next.length-end).slice(0, maximumLength-start-end)
                            if (inserted.length && inserted.charCodeAt(inserted.length-1) >= 0xd800
                                    && inserted.charCodeAt(inserted.length-1) <= 0xdbff)
                                inserted = inserted.slice(0, -1)
                            text = prior.slice(0, start) + inserted + prior.slice(prior.length-end)
                            cursorPosition = start + inserted.length
                            return
                        }
                        previousText = text
                        editor.setConversationDraft(text)
                    }
                    Keys.onPressed: function(event) {
                        if (!inputMethodComposing && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                && !(event.modifiers & Qt.ShiftModifier)) {
                            event.accepted = true
                            chatRoot.sendPrompt()
                        }
                    }
                }
            }
            Action { objectName: "applyDescriptionButton"; text: editor.ai.enabled && !editor.ai.ready ? "连接 AI" : chatRoot.canUseDraft ? "AI 修此范围" : chatMode.currentIndex===2 ? "预览分区" : chatMode.currentIndex===1 ? "获取建议" : editor.ai.enabled ? "AI 修图" : "应用规则"; primary: true; enabled: chatRoot.canSend && (!editor.activeIsGroup || chatMode.currentIndex===2 || chatMode.currentIndex===0 && editor.ai.enabled) && (prompt.text.trim().length>0 || editor.ai.enabled && !editor.ai.ready); onClicked: sendPrompt() }
        }
        RowLayout { Layout.fillWidth: true
            Caption { objectName: "conversationScopeHint"; text: chatRoot.canUseDraft ? (editor.selection.editingLayerMask ? "AI 使用当前范围保存并调整此层；可一步撤销。" : "AI 使用当前范围自动建层调整，无需再点开始调整。") : editor.hasSelectionDraft ? (editor.ai.enabled ? "选择“智能修图”即可直接调整当前范围。" : "连接 AI 可直接修此范围，或点“开始调整”使用本地规则。") : editor.hasRegionDraft ? "正在预览分区，确认后建立独立图层。" : chatMode.currentIndex===2 ? "先预览和修边，再创建局部图层。" : chatMode.currentIndex===0 && editor.ai.enabled ? "AI 可分层、编组、修复小瑕疵或修改已有层；可一步撤销。" : "作用范围："+editor.activeLayerName+" / "+editor.selectionLabel; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 10 }
            Caption { objectName: "conversationInputKeyHint"; text: "Enter 发送 · Shift+Enter 换行"; font.pixelSize: 10 }
        }
    }
}
