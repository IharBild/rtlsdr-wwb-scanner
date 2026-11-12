import QtQuick 2.12
import QtQuick.Controls 2.12

// V21.1 (audit D3): the Fusion check box draws its outline darker than the
// window, so an empty box could not be seen on the dark window. Same control,
// with a box in a colour of the palette that is visible in both themes.
CheckBox {
    id: control
    indicator: Rectangle {
        objectName: 'boxCheckIndicator'
        implicitWidth: 15
        implicitHeight: 15
        x: control.text ? control.leftPadding : control.leftPadding + (control.availableWidth - width) / 2
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: 2
        color: control.down ? Qt.darker(control.palette.base, 1.2) : control.palette.base
        border.width: 1
        border.color: control.visualFocus ? control.palette.highlight
                    : control.hovered ? control.palette.windowText : control.palette.mid
        Rectangle {
            anchors.centerIn: parent
            width: 9
            height: 9
            radius: 1
            visible: control.checked
            color: control.enabled ? control.palette.highlight : control.palette.mid
        }
    }
}
