import QtQuick 2.0
import QtQuick.Dialogs 1.0
import Qt.labs.settings 1.0

FileDialog {
    id: root
    title: "Please choose a file"
    folder: shortcuts.home
    selectMultiple: false
    selectExisting: false
    // The extension is added from the chosen filter (withExtension below),
    // so no defaultSuffix here.
    nameFilters: [
        'Shure WWB CSV (*.csv)',
        'Sennheiser WSM CSV (*.csv)',
        'Shure WWB native (*.sdb2)',
        'Shure WWB 7 (*.sdb3)',
        'Numpy Files (*.npz)',
    ]
    property var graphData

    Item {
        Settings {
            category: 'Folder Preferences'
            property alias exportFolder: root.folder
        }
    }

    // Name typed without our extension -> add the one of the chosen format
    // ("Shure WWB native (*.sdb2)" -> .sdb2). The file format is then chosen
    // by the extension in Python (save_to_file), as before V11.
    function withExtension(url, filt) {
        var s = String(url);
        var low = s.toLowerCase();
        var exts = ['.csv', '.sdb2', '.sdb3', '.npz'];
        for (var i = 0; i < exts.length; i++) {
            if (low.slice(-exts[i].length) === exts[i])
                return s;
        }
        var m = /\*\.(\w+)/.exec(filt);
        var ext = m ? '.' + m[1].toLowerCase() : '.csv';
        if (exts.indexOf(ext) < 0)
            ext = '.csv';
        return s + ext;
    }

    // Called on OK. Prints what it does to the console (lines "qml: export ...").
    // Uses only save_to_file(url): the two-argument slot of V11 is not
    // visible from QML on the user's PySide2 (V11.1 console, 04.11.2026).
    function doExport(url, filt) {
        var f = (filt === undefined || filt === null) ? '' : String(filt);
        console.log('export dialog: file ' + url + ', format "' + f + '"');
        if (!root.graphData) {
            console.log('export: nothing to export (no scan in the graph)');
            return false;
        }
        var target = withExtension(url, f);
        if (target !== String(url))
            console.log('export: extension added -> ' + target);
        try {
            // V19.1: both CSV layouts end in .csv - the chosen filter decides.
            // The format travels in the address ("#wsm"): save_to_file(url)
            // is the one call that surely works from QML on the user's PC
            // (a separate set_export_format call was not seen there).
            var wsm = f.indexOf('WSM') >= 0;
            console.log('export: layout ' + (wsm ? 'Sennheiser WSM' : 'by extension'));
            root.graphData.save_to_file(Qt.resolvedUrl(target + (wsm ? '#wsm' : '')));
            return true;
        } catch (e) {
            console.log('export FAILED in QML: ' + e);
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
