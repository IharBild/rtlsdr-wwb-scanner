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

    // Called on OK. Prints what it does to the console (lines "qml: export ...")
    // and falls back to the old CSV export if anything goes wrong.
    function doExport(url, filt) {
        var f = (filt === undefined || filt === null) ? '' : String(filt);
        console.log('export dialog: file ' + url + ', format "' + f + '"');
        if (!root.graphData) {
            console.log('export: nothing to export (no scan in the graph)');
            return false;
        }
        try {
            if (typeof root.graphData.save_to_file_as === 'function') {
                root.graphData.save_to_file_as(url, f);
            } else {
                console.log('export: graph.py without save_to_file_as (V10), CSV only');
                root.graphData.save_to_file(url);
            }
            return true;
        } catch (e) {
            console.log('export FAILED in QML: ' + e + ' - trying the old CSV export');
            try {
                root.graphData.save_to_file(url);
                return true;
            } catch (e2) {
                console.log('export FAILED again: ' + e2);
            }
        }
        return false;
    }

    onAccepted: {
        doExport(root.fileUrl, root.selectedNameFilter);
        root.close();
    }

    onRejected: {
        root.close();
    }

}
