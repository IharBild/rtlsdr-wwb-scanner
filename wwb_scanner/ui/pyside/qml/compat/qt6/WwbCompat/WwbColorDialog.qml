// V21.4: colour dialog with one API for Qt 5 and Qt 6: property "color"
// (Qt 6 calls it selectedColor).
import QtQuick
import QtQuick.Dialogs as D

D.ColorDialog {
    id: dlg
    property alias color: dlg.selectedColor
}
