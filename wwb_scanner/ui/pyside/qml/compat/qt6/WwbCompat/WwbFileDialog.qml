// V21.4: file dialog with one API for Qt 5 (compat/qt5) and Qt 6 (this file).
// See compat/qt5/WwbCompat/WwbFileDialog.qml for the properties.
import QtQuick
import QtCore
import QtQuick.Dialogs as D

D.FileDialog {
    id: dlg
    property bool saveMode: false
    property alias folder: dlg.currentFolder
    readonly property url fileUrl: dlg.selectedFile
    // Qt 6: selectedNameFilter is an object (index, name, globs) -> the text
    // of the chosen filter, as Qt 5 gives it
    readonly property string filterText: (dlg.selectedNameFilter && dlg.selectedNameFilter.index >= 0
                                          && dlg.selectedNameFilter.index < dlg.nameFilters.length)
                                         ? dlg.nameFilters[dlg.selectedNameFilter.index] : ''
    fileMode: saveMode ? D.FileDialog.SaveFile : D.FileDialog.OpenFile
    currentFolder: StandardPaths.writableLocation(StandardPaths.HomeLocation)
}
