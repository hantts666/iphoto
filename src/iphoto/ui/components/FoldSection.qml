import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

GroupBox {
    id: root
    property bool expanded: true
    default property alias sectionData: body.data
    Layout.fillWidth: true
    topPadding: 34; leftPadding: 0; rightPadding: 0; bottomPadding: expanded ? 6 : 0
    implicitHeight: 34 + (expanded ? body.implicitHeight + bottomPadding : 0)
    background: Item {}
    label: Action {
        objectName: root.objectName.length ? root.objectName + "Toggle" : ""
        width: root.width; implicitHeight: 30
        text: (root.expanded ? "▾  " : "▸  ") + root.title
        subtle: true; font.bold: true
        hint: root.expanded ? "收起这一组，不改变设置" : "展开这一组"
        onClicked: root.expanded = !root.expanded
    }
    contentItem: ColumnLayout { id: body; visible: root.expanded; spacing: 8 }
}
