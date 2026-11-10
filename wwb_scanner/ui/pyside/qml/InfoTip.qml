import QtQuick 2.12
import QtQuick.Controls 2.12

// V20.7: a small "i" in a circle; hover or click shows what the element
// next to it does
Control {
    id: root
    property string text
    hoverEnabled: true
    padding: 0
    property int tipWidth: 360
    implicitWidth: 16
    implicitHeight: 16

    Rectangle {
        id: circle
        anchors.fill: parent
        radius: width / 2
        color: 'transparent'
        border.width: 1
        border.color: area.containsMouse || tip.visible ? root.palette.highlight : root.palette.mid
        Label {
            anchors.centerIn: parent
            text: 'i'
            font.pointSize: 8
            font.bold: true
            font.italic: true
            color: circle.border.color
        }
    }
    MouseArea {
        id: area
        anchors.fill: parent
        anchors.margins: -3
        hoverEnabled: true
        cursorShape: Qt.WhatsThisCursor
        onClicked: tip.visible = !tip.visible
        onExited: tip.visible = false
        onEntered: tipTimer.restart()
    }
    Timer {
        id: tipTimer
        interval: 350
        onTriggered: if (area.containsMouse) tip.visible = true
    }
    ToolTip {
        id: tip
        objectName: 'infoTip'
        text: root.text
        delay: 0
        timeout: 20000
        // V20.7.2: right above the "i", centred on it; near a window edge the
        // popup is pushed inside (margins)
        parent: root
        margins: 6
        x: Math.round((root.width - width) / 2)
        y: -height - 6
        contentItem: Label {
            text: tip.text
            wrapMode: Text.Wrap
            width: Math.min(root.tipWidth, implicitWidth)
            font.pointSize: 9
        }
        width: Math.min(root.tipWidth + leftPadding + rightPadding, implicitWidth)
    }
}
