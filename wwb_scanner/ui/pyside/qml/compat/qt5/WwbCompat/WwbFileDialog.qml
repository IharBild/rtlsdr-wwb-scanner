// V21.4: file dialog with one API for Qt 5 (this file, QtQuick.Dialogs 1.x)
// and Qt 6 (compat/qt6). Used by ExportDialog.qml and ImportDialog.qml:
//   saveMode      true = choose a new file name (export)
//   folder        the folder shown (saved in Settings by the callers)
//   fileUrl       the chosen file
//   filterText    the chosen name filter, e.g. "Shure WWB native (*.sdb2)"
import QtQuick 2.0
import QtQuick.Dialogs 1.0

FileDialog {
    property bool saveMode: false
    readonly property string filterText: selectedNameFilter
    selectMultiple: false
    selectExisting: !saveMode
    folder: shortcuts.home
}
