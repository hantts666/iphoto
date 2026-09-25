import QtQuick

// Screen-sized overlays: zoom never allocates an image-sized Canvas texture.
Item {
    id: root
    required property var workspace
    required property var editor
    required property var photo
    required property var input
    property string hoverId: ""
    property string hoverPreview: ""
    function repaint() { outlines.requestPaint() }
    Connections { target: editor; function onChanged() { root.syncHover() } }
    Connections { target: editor.viewport; function onChanged() { root.repaint() } }
    function syncHover() { var row=editor.sceneObjects.find(function(o) { return o.id===root.hoverId }); hoverPreview=row ? row.maskPreview : ""; repaint() }
    onHoverIdChanged: syncHover()
    Image { x: root.photo.x; y: root.photo.y; width: root.photo.width; height: root.photo.height; source: root.hoverPreview; visible: root.workspace.selectionTool==="object" && !root.editor.viewport.spaceHeld; fillMode: Image.Stretch }
    Canvas {
        id: outlines
        objectName: "canvasOverlays"
        anchors.fill: parent
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var c = getContext("2d"); c.reset()
            var px = root.photo.x, py = root.photo.y, pw = root.photo.width, ph = root.photo.height
            c.save(); c.beginPath(); c.rect(px, py, pw, ph); c.clip()
            if (root.workspace.selectionTool === "object" && root.hoverId && !root.hoverPreview && !root.editor.viewport.spaceHeld) {
                var objects = root.editor.sceneObjects
                c.strokeStyle = "#ffda78"; c.lineWidth = 2; c.fillStyle = "#3066bba0"
                for (var i = 0; i < objects.length; i++) {
                    var o = objects[i]; if (o.id !== root.hoverId) continue
                    for (var j = 0; j < o.polygons.length; j++) {
                        var p = o.polygons[j]; c.beginPath(); c.moveTo(px + p[0][0]*pw, py + p[0][1]*ph)
                        for (var k = 1; k < p.length; k++) c.lineTo(px + p[k][0]*pw, py + p[k][1]*ph)
                        c.closePath(); c.fill(); c.stroke()
                    }
                }
            }
            var points = root.input.points, tool = root.workspace.selectionTool
            if (points.length && tool !== "object" && tool !== "wand" && tool !== "smart") {
                c.strokeStyle = root.input.strokeMode === "subtract" ? "#ffad8d" : "#d7fff1"; c.lineWidth = 2
                var a = points[0], b = points[points.length - 1]; c.beginPath()
                if (tool === "rect") c.rect(px+a[0]*pw, py+a[1]*ph, (b[0]-a[0])*pw, (b[1]-a[1])*ph)
                else if (tool === "ellipse") c.ellipse(px+Math.min(a[0],b[0])*pw, py+Math.min(a[1],b[1])*ph, Math.abs(b[0]-a[0])*pw, Math.abs(b[1]-a[1])*ph)
                else {
                    c.moveTo(px+a[0]*pw, py+a[1]*ph)
                    for (var n = 1; n < points.length; n++) c.lineTo(px+points[n][0]*pw, py+points[n][1]*ph)
                    if (tool === "brush") { c.lineWidth = root.workspace.brushRadius*2*Math.min(pw,ph); c.lineCap = "round"; c.lineJoin = "round" }
                }
                c.stroke()
            }
            c.restore()
            if (tool === "smart") {
                var prompts=root.editor.pixelPoints
                for (var q=0;q<prompts.length;q++) {
                    var dot=prompts[q], dx=px+dot[0]*pw, dy=py+dot[1]*ph
                    c.beginPath(); c.arc(dx,dy,6,0,Math.PI*2); c.fillStyle=dot[2] ? "#4fdbac" : "#fc8778"; c.fill(); c.lineWidth=1.5; c.strokeStyle="#20282c"; c.stroke()
                    c.beginPath(); c.moveTo(dx-3,dy); c.lineTo(dx+3,dy); if(dot[2]) {c.moveTo(dx,dy-3);c.lineTo(dx,dy+3)};c.stroke()
                }
            }
        }
    }
}
