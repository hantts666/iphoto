import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    required property var ai
    modal: true
    anchors.centerIn: parent
    width: Math.min(610, parent.width - 40)
    height: Math.min(840, parent.height - 36)
    padding: 24
    closePolicy: Popup.CloseOnEscape
    property bool keyAvailable: false
    property bool filling: false
    property string providerId: provider.currentIndex >= 0 ? ai.providers[provider.currentIndex].id : "qianwen_token_plan"
    background: Rectangle { color: "#fafaf6"; radius: 14; border.color: "#d9dfd1" }
    Overlay.modal: Rectangle { color: "#650d2119" }

    function refreshKey() { keyAvailable = ai.hasKey(providerId, baseUrl.text) }
    function loadSettings() {
        filling = true
        var config = ai.config
        var index = 0
        for (var i = 0; i < ai.providers.length; i++)
            if (ai.providers[i].id === config.provider) index = i
        provider.currentIndex = index
        baseUrl.text = config.base_url
        modelName.text = config.model
        useCloud.checked = config.enabled
        remember.checked = config.remember && ai.canRemember
        apiKey.text = ""
        showKey.checked = false
        filling = false
        refreshKey()
    }
    onOpened: loadSettings()
    onClosed: { apiKey.text = ""; showKey.checked = false }
    Connections { target: ai; function onChanged() { if (dialog.opened) dialog.refreshKey() } }

    component Field: TextField {
        implicitHeight: 42
        selectByMouse: true
        color: "#26372e"
        placeholderTextColor: "#929b8a"
        leftPadding: 12; rightPadding: 12
        background: Rectangle { color: "#ffffff"; radius: 6; border.color: parent.activeFocus ? "#5b7851" : "#dce1d5"; border.width: parent.activeFocus ? 2 : 1 }
    }
    component LabelText: Text {
        color: "#56664b"; font.pixelSize: 12; textFormat: Text.PlainText
    }
    component SmallButton: Button {
        id: control
        property bool primary: false
        implicitHeight: 38
        leftPadding: 17; rightPadding: 17
        opacity: enabled ? 1 : .45
        background: Rectangle { radius: 6; color: control.primary ? (control.down ? "#243e2f" : "#34523e") : control.hovered ? "#e8eddf" : "#f0f2e9"; border.color: control.primary ? "transparent" : "#dce1d5" }
        contentItem: Text { text: control.text; color: control.primary ? "white" : "#455c3a"; font: control.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    }

    contentItem: ColumnLayout {
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout { spacing: 5
                Text { text: "连接你的 AI"; color: "#26372e"; font.pixelSize: 22; font.bold: true }
                LabelText { text: "看懂照片与要求，让调整更贴近你的想法。"; color: "#929b8a" }
            }
            Item { Layout.fillWidth: true }
            SmallButton { objectName: "closeAiSettings"; text: "关闭"; onClicked: dialog.close() }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#e2e5db" }
        ScrollView {
            objectName: "aiSettingsScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth; clip: true
            ScrollBar.vertical.policy: ScrollBar.AsNeeded
            ColumnLayout {
                width: parent.width
                spacing: 10
                CheckBox {
                    id: useCloud; objectName: "aiEnabled"
                    text: "使用云端 AI 修图"; checked: true; enabled: !ai.busy
                }
                LabelText { text: useCloud.checked ? "点击修图、建议或选区生成时，会发送照片缩略图和对话上下文。" : "已选择本地规则模式，不发送照片；仅支持有限关键词。"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                LabelText { text: "服务商"; Layout.topMargin: 4 }
                ComboBox {
                    id: provider; objectName: "aiProvider"
                    Layout.fillWidth: true; implicitHeight: 40
                    model: ai.providers; textRole: "name"; enabled: !ai.busy && useCloud.checked
                    onActivated: {
                        if (!dialog.filling) {
                            baseUrl.text = ai.providers[currentIndex].base_url
                            modelName.text = ai.providers[currentIndex].model
                            apiKey.text = ""
                            dialog.refreshKey()
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; visible: dialog.providerId === "qianwen_token_plan"
                    LabelText { text: "套餐专属接口 · 使用 sk-sp- 开头的 Key"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    SmallButton { text: "打开套餐页面 ↗"; implicitHeight: 28; font.pixelSize: 10; onClicked: Qt.openUrlExternally("https://platform.qianwenai.com/home/analytics/token-plan/individual") }
                }
                LabelText { text: "接口地址 · Base URL" }
                Field {
                    id: baseUrl; objectName: "aiBaseUrl"
                    Layout.fillWidth: true; enabled: !ai.busy && useCloud.checked
                    placeholderText: "https://服务地址/v1"
                    onTextChanged: { if (!dialog.filling) dialog.refreshKey() }
                }
                LabelText { text: "模型名称" }
                Field {
                    id: modelName; objectName: "aiModel"
                    Layout.fillWidth: true; enabled: !ai.busy && useCloud.checked
                    placeholderText: "填写支持图片输入的模型 ID"
                }
                RowLayout {
                    Layout.fillWidth: true
                    LabelText { text: "API Key" }
                    Item { Layout.fillWidth: true }
                    LabelText { text: dialog.keyAvailable ? "已保存此接口的 Key" : "尚未保存 Key"; font.pixelSize: 10; color: "#8b9780" }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Field {
                        id: apiKey; objectName: "aiApiKey"
                        Layout.fillWidth: true; enabled: !ai.busy && useCloud.checked
                        echoMode: showKey.checked ? TextInput.Normal : TextInput.Password
                        inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhSensitiveData
                        placeholderText: dialog.keyAvailable ? "留空使用已保存的 Key；输入新 Key 可替换" : dialog.providerId === "qianwen_token_plan" ? "在这里粘贴 sk-sp- 开头的套餐 Key" : "在这里粘贴你的 API Key"
                    }
                    CheckBox { id: showKey; text: "显示"; enabled: !ai.busy }
                }
                RowLayout {
                    Layout.fillWidth: true
                    CheckBox { id: remember; objectName: "aiRemember"; text: "记住 Key（Windows 凭据库）"; checked: ai.canRemember; enabled: ai.canRemember && !ai.busy && useCloud.checked }
                    Item { Layout.fillWidth: true }
                    SmallButton { objectName: "forgetAiKey"; text: "删除 Key"; enabled: dialog.keyAvailable && !ai.busy; onClicked: { ai.forgetKey(dialog.providerId, baseUrl.text); apiKey.text = "" } }
                }
                LabelText {
                    visible: dialog.providerId === "qianwen_token_plan"
                    text: "平台文档对自定义应用直连设有限制，需以套餐授权为准。查看说明 ↗"
                    color: "#90764e"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.Wrap
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: Qt.openUrlExternally("https://platform.qianwenai.com/docs/developer-guides/clients-and-developer-tools/other-tools") }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: disclosure.implicitHeight + 22
                    radius: 6; color: "#edf1e5"
                    Text {
                        id: disclosure; x: 11; y: 11; width: parent.width - 22
                        text: "云端 AI 会收到最长边 1280px 的照片缩略图（不含 EXIF）、要求、参数、选区和最近最多 8 条对话。原图在本机处理。测试连接只发送内置色块图，会产生一次少量 API 用量。"
                        color: "#77866a"; font.pixelSize: 11; wrapMode: Text.Wrap; textFormat: Text.PlainText; lineHeight: 1.3
                    }
                }
                LabelText { text: "自定义接口使用 OpenAI Chat Completions 协议，需支持看图和 JSON 输出。"; color: "#929b8a"; font.pixelSize: 10; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: Math.max(44, resultText.implicitHeight + 18)
            radius: 6; color: ai.isError ? "#f5e8e2" : "#eef1e7"
            Text {
                id: resultText; objectName: "aiConnectionResult"
                x: 10; y: 9; width: parent.width - 20
                text: ai.message || "填写 Key 后，可先测试连接，再保存使用。"
                color: ai.isError ? "#9b5742" : "#61774f"
                font.pixelSize: 11; wrapMode: Text.Wrap; textFormat: Text.PlainText
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            SmallButton { objectName: "testAiConnection"; text: ai.busy ? "连接中…" : "测试连接"; enabled: !ai.busy && useCloud.checked; onClicked: ai.testConnection(dialog.providerId, baseUrl.text, modelName.text, apiKey.text) }
            SmallButton { objectName: "cancelAiTest"; text: "取消请求"; visible: ai.busy; onClicked: ai.cancel() }
            Item { Layout.fillWidth: true }
            SmallButton {
                objectName: "saveAiSettings"; text: "保存并使用"; primary: true; enabled: !ai.busy
                onClicked: { if (ai.save(dialog.providerId, baseUrl.text, modelName.text, apiKey.text, remember.checked, useCloud.checked)) dialog.close() }
            }
        }
    }
}
