import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Slider {
    id: sliderControl
    background: Rectangle { x: sliderControl.leftPadding; y: sliderControl.height/2-1; width: sliderControl.availableWidth; height: 2; radius: 1; color: "#555e69"; Rectangle { width: parent.width*sliderControl.visualPosition; height: 2; color: "#8caf9e" } }
    handle: Rectangle { x: sliderControl.leftPadding+sliderControl.visualPosition*(sliderControl.availableWidth-width); y: sliderControl.height/2-height/2; width: 11; height: 11; radius: 6; color: sliderControl.pressed ? "#c4e7d6" : "#b5bfc7"; border.color: "#242a30"; opacity: sliderControl.enabled ? 1 : .4 }
}
