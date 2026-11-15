import QtQuick 2.0
import WwbCompat 1.0

// V21.4: WwbFileDialog = the same dialog on Qt 5 and Qt 6 (qml/compat)
WwbFileDialog {
    id: root
    title: "Please choose a file"
    nameFilters: [
        'Scan Files (*.csv *.sdb2 *.sdb3)',
        'Sennheiser WSM scan (*.csv)',
        'Numpy Files (*.npz)',
    ]
    signal importFile(var fileName)

    // V21.4: a property, not a child item - the wrapped Qt 5 FileDialog has no
    // default property for children
    property var folderSettings: WwbSettings {
        category: 'Folder Preferences'
        property alias importFolder: root.folder
    }

    onAccepted: {
        root.importFile(root.fileUrl);
        root.close();
    }

    onRejected: {
        root.close();
    }

}
