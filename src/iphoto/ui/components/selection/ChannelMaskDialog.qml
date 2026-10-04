import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Dialog {
    id: root
    required property var editor
    readonly property var channel: editor.channelMask
    readonly property var config: channel.options
    objectName: "channelMaskDialog"
    anchors.centerIn: parent
    width: Math.min(670, parent.width-40)
    title: "通道抠图"
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
            Image { objectName: "channelAlphaPreview"; anchors.fill: parent; anchors.margins: 4; source: root.channel.previewUrl; fillMode: Image.PreserveAspectFit; cache: false; asynchronous: true }
            Caption { anchors.centerIn: parent; visible: root.channel.loading; text: "正在更新通道预览…" }
        }
        Caption { Layout.fillWidth: true; text: root.channel.note; wrapMode: Text.Wrap }
        RowLayout {
            Caption { text: "通道" }
            ComboBox {
                objectName: "channelChoiceBox"; Layout.fillWidth: true
                model: [{text:"自动",value:"auto"},{text:"红",value:"red"},{text:"绿",value:"green"},{text:"蓝",value:"blue"},{text:"亮度",value:"luminance"}]
                textRole: "text"; valueRole: "value"
                currentIndex: Math.max(0, model.findIndex(function(item) { return item.value===root.config.channel }))
                enabled: !root.editor.busy
                onActivated: root.channel.setOption("channel", currentValue)
            }
            CheckBox { objectName: "channelInvertBox"; text: "反转"; checked: root.config.invert || false; enabled: !root.editor.busy; onClicked: root.channel.setOption("invert",checked) }
            Action { objectName: "channelRecommendButton"; text: "自动推荐"; subtle: true; enabled: !root.editor.busy; onClicked: root.channel.open() }
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
            CheckBox { objectName: "channelUseAiBox"; text: "结合 AI 细化透明边缘"; checked: root.config.ai===true; enabled:!root.editor.busy; onClicked:root.channel.setOption("ai",checked) }
            Item { Layout.fillWidth:true }
            Caption { text: "范围" }
            SpinBox { objectName: "channelRadiusBox"; editable:true; from:1; to:256; value:root.config.radius || 32; enabled:!root.editor.busy; onValueModified:root.channel.setOption("radius",value) }
            Caption { text: "原图 px" }
        }
        CheckBox { objectName:"channelInteriorBox"; text:"处理内部透明与孔洞（薄纱、玻璃、细枝）"; checked:root.config.interior===true; enabled:!root.editor.busy; onClicked:root.channel.setOption("interior",checked) }
        Caption { Layout.fillWidth:true; wrapMode:Text.Wrap; font.pixelSize:10; text:"先用 AI 或画笔选择目标，再用通道保留灰度透明度。预览只供调通道；应用时按原图与 AI 核对细节。颜色接近的区域仍需局部修正。" }
    }
    footer: RowLayout {
        spacing:8
        Action { objectName: "channelCloseButton"; text: root.editor.matteBusy ? "取消计算" : "关闭"; onClicked: { if(root.editor.matteBusy) root.editor.selection.cancelTask(); else root.close() } }
        Item { Layout.fillWidth:true }
        Action { objectName: "channelApplyButton"; text:"应用到当前范围"; primary:true; enabled:!root.editor.busy && !root.channel.loading && root.channel.previewUrl!==""; onClicked:root.channel.apply() }
    }
}
