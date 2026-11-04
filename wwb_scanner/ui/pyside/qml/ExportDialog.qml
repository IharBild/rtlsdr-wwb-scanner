import QtQuick 2.0
import QtQuick.Dialogs 1.0
import Qt.labs.settings 1.0

FileDialog {
    id: root
    title: "Please choose a file"
    folder: shortcuts.home
    selectMultiple: false
    selectExisting: false
    // The extension is added in Python from the chosen filter
    // (graph.py save_to_file_as), so no defaultSuffix here.
    nameFilters: [
        'Shure WWB CSV (*.csv)',
        'Shure WWB native (*.sdb2)',
        'Numpy Files (*.npz)',
    ]
    property var graphData

    Item {
        Settings {
            category: 'Folder Preferences'
            property alias exportFolder: root.folder
        }
    }

    onAccepted: {
        root.graphData.save_to_file_as(root.fileUrl, root.selectedNameFilter);
        root.close();
    }

    onRejected: {
        root.close();
    }

}
