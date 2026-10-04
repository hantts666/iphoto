import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Dialog {
    id: root
    required property var editor
    readonly property var channel: editor.channelMask
    readonly property var config: channel.options
    readonly property bool previewReady: alphaImage.status===Image.Ready
    objectName: "channelMaskDialog"
    anchors.centerIn: parent
    width: Math.min(670, parent.width-40)
    title: root.config.whole===true ? "从照片创建通道选区" : "通道抠图"
    modal: true
    Connections {
        target: root.channel
        function onChanged() {
            if (root.channel.opened && !root.visible) root.open()
            else if (!root.channel.opened && root.visible) root.close()
        }
    }
    closePolicy: Popup.CloseOnEscape
    onClosed: if(channel.opened) channel.close()
    palette.text: "#e4e8ee"; palette.windowText: "#e4e8ee"; palette.buttonText: "#e4e8ee"
    background: Rectangle { color: "#2d3137"; border.color: "#55616b"; radius: 8 }
    contentItem: ColumnLayout {
        spacing: 9
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 270; color: "#131619"
            Image { id: alphaImage; objectName: "channelAlphaPreview"; anchors.fill: parent; anchors.margins: 4; source: root.channel.previewUrl; fillMode: Image.PreserveAspectFit; cache: false; asynchronous: true }
            Caption { anchors.centerIn: parent; visible: root.channel.loading || alphaImage.status===Image.Loading; text: "正在更新通道预览…" }
            Caption { anchors.centerIn: parent; visible: alphaImage.status===Image.Error; text: "通道预览读取失败，请关闭后重新打开" }
        }
        Caption { Layout.fillWidth: true; text: root.channel.note; wrapMode: Text.Wrap }
        RowLayout {
            Caption { text: "通道" }
            ComboBox {
                objectName: "channelChoiceBox"; Layout.fillWidth: true
                model: (root.config.whole===true ? [] : [{text:"自动",value:"auto"}]).concat([{text:"红",value:"red"},{text:"绿",value:"green"},{text:"蓝",value:"blue"},{text:"亮度",value:"luminance"},{text:"红−绿（计算）",value:"red_green"},{text:"红−蓝（计算）",value:"red_blue"},{text:"绿−蓝（计算）",value:"green_blue"}])
                textRole: "text"; valueRole: "value"
                currentIndex: Math.max(0, model.findIndex(function(item) { return item.value===root.config.channel }))
                enabled: !root.editor.busy
                onActivated: root.channel.setOption("channel", currentValue)
            }
            CheckBox { objectName: "channelInvertBox"; text: "反转"; checked: root.config.invert || false; enabled: !root.editor.busy; onClicked: root.channel.setOption("invert",checked) }
            Action { objectName: "channelRecommendButton"; text: "自动推荐"; visible: root.config.whole!==true; subtle: true; enabled: !root.editor.busy; onClicked: root.channel.open() }
        }
        RowLayout {
            Caption { text: "黑场" }
            SpinBox { objectName: "channelBlackBox"; editable:true; from:0; to:254; value:root.config.black || 0; enabled:!root.editor.busy; onValueModified:root.channel.setOption("black",value) }
            Caption { text: "白场" }
            SpinBox { objectName: "channelWhiteBox"; editable:true; from:1; to:255; value:root.config.white || 255; enabled:!root.editor.busy; onValueModified:root.channel.setOption("white",value) }
            Caption { text: "灰度" }
            FineSlider { objectName: "channelGammaSlider"; Layout.fillWidth:true; from:.2; to:5; stepSize:.05; value:root.config.gamma || 1; enabled:!root.editor.busy; onMoved:root.channel.setOption("gamma",value) }
            Caption { text: Number(root.config.gamma || 1).toFixed(2) }
        }
        RowLayout {
            visible: root.config.whole!==true
            CheckBox { objectName: "channelUseAiBox"; text: "结合 AI 细化透明边缘"; checked: root.config.ai===true; enabled:!root.editor.busy; onClicked:root.channel.setOption("ai",checked) }
            Item { Layout.fillWidth:true }
            Caption { text: "范围" }
            SpinBox { objectName: "channelRadiusBox"; editable:true; from:1; to:256; value:root.config.radius || 32; enabled:!root.editor.busy; onValueModified:root.channel.setOption("radius",value) }
            Caption { text: "原图 px" }
        }
        CheckBox { objectName:"channelInteriorBox"; visible:root.config.whole!==true; text:"处理内部透明与孔洞（薄纱、玻璃、细枝）"; checked:root.config.interior===true; enabled:!root.editor.busy; onClicked:root.channel.setOption("interior",checked) }
        CheckBox { objectName:"channelDetailBox"; visible:root.config.whole!==true; text:"按原像素细化发丝纹理"; checked:root.config.detail===true; enabled:!root.editor.busy; onClicked:root.channel.setOption("detail",checked) }
        CheckBox { objectName:"channelColorBox"; visible:root.config.whole!==true; text:"去背景串色（黑白底检查与透明 PNG）"; checked:root.config.color===true; enabled:!root.editor.busy; onClicked:root.channel.setOption("color",checked) }
        Caption { Layout.fillWidth:true; wrapMode:Text.Wrap; font.pixelSize:10; text:root.config.whole===true ? "无需先选目标。整图通道适合主体与背景颜色差异明显的照片，同色背景也会选中；生成范围后可用画笔或 AI 继续修细节。" : "应用时按原像素计算；切换黑白底检查实际效果。颜色接近的区域仍需局部修正。" }
    }
    footer: RowLayout {
        spacing:8
        Action { objectName: "channelCloseButton"; text: root.editor.matteBusy ? "取消计算" : "关闭"; onClicked: { if(root.editor.matteBusy) root.editor.selection.cancelTask(); else root.close() } }
        Item { Layout.fillWidth:true }
        Action { objectName: "channelApplyButton"; text:root.config.whole===true ? "生成选区" : "应用到当前范围"; primary:true; enabled:!root.editor.busy && !root.channel.loading && root.previewReady; onClicked:root.channel.apply() }
    }
}
