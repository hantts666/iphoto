import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    required property var workspace
    required property var editor
 Layout.preferredWidth: 54; Layout.fillHeight: true; color: workspace.panel
    ColumnLayout { anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter; anchors.topMargin: 12; spacing: 6
        Action { objectName: "tool_smart"; text: "像素"; hint: "像素点选 S · 点击目标 / Alt 点击排除 · 无需云端 Key"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="smart"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; onClicked: workspace.chooseTool("smart") }
        Action { objectName: "tool_inspect"; hint: "浏览 / 平移 V"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="inspect"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; contentItem: Image { source: "../../../../assets/tools/inspect.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("inspect") }
        Action { objectName: "tool_hand"; hint: "抓手 H · 空格临时拖动 · 双击画布适应"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="hand"; enabled: editor.hasImage; contentItem: Image { source: "../../../../assets/tools/hand.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("hand"); onDoubleClicked: editor.viewport.fit() }
        Action { objectName: "tool_zoom"; hint: "缩放 Z · Alt 单击缩小 · 左右拖动连续缩放"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="zoom"; enabled: editor.hasImage; contentItem: Image { source: "../../../../assets/tools/zoom.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("zoom"); onDoubleClicked: editor.viewport.setZoom(1) }
        Action { objectName: "tool_object"; text: "对象"; hint: "对象点选 O：先分析画面，悬停预览后点击"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="object"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; onClicked: workspace.chooseTool("object") }
        Action { objectName: "tool_rect"; hint: "矩形选框 M"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="rect"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; contentItem: Image { source: "../../../../assets/tools/rect.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("rect") }
        Action { objectName: "tool_ellipse"; hint: "椭圆选框"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="ellipse"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; contentItem: Image { source: "../../../../assets/tools/ellipse.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("ellipse") }
        Action { objectName: "tool_polygon"; hint: "自由套索 L"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="polygon"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; contentItem: Image { source: "../../../../assets/tools/polygon.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("polygon") }
        Action { objectName: "tool_wand"; hint: "颜色魔棒 W：只选相连的相似颜色，不识别物体"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="wand"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; contentItem: Image { source: "../../../../assets/tools/wand.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("wand") }
        Action { objectName: "tool_brush"; hint: "选区画笔 B"; implicitWidth: 40; implicitHeight: 38; primary: workspace.selectionTool==="brush"; enabled: editor.hasImage && !editor.busy && !editor.hasRegionDraft; contentItem: Image { source: "../../../../assets/tools/brush.svg"; sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit } onClicked: workspace.chooseTool("brush") }
        Rectangle { Layout.preferredWidth: 32; height: 1; color: workspace.line }
        Action { text: "AI"; hint: "打开独立 AI 选区面板"; implicitWidth: 40; onClicked: { workspace.inspectorPage=1; workspace.selectionAiOpen=true; workspace.focusSelectionInput() } }
    }
}
