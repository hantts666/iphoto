import QtQuick

// An opaque, viewport-bounded background for a photo texture. A native RGBA
// tile replaces the proxy beneath it; its alpha must not reveal that proxy.
// Match the main canvas grid in surface coordinates, including while panning.
Canvas {
    required property real originX
    required property real originY
    required property real viewportWidth
    required property real viewportHeight
    x: Math.max(0, -originX)
    y: Math.max(0, -originY)
    width: Math.max(0, Math.min(parent.width-x, viewportWidth-originX-x))
    height: Math.max(0, Math.min(parent.height-y, viewportHeight-originY-y))
    z: -1
    onOriginXChanged: requestPaint()
    onOriginYChanged: requestPaint()
    onXChanged: requestPaint()
    onYChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d"); c.reset()
        var ox = originX+x, oy = originY+y
        var left = Math.floor(ox/12)*12, top = Math.floor(oy/12)*12
        for (var py = top; py < oy+height; py += 12)
            for (var px = left; px < ox+width; px += 12) {
                c.fillStyle = ((px/12+py/12)%2) ? "#4a4d52" : "#383c41"
                c.fillRect(px-ox, py-oy, 12, 12)
            }
    }
}
