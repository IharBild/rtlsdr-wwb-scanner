import QtQuick 2.0
import QtQuick.Layouts 1.11
import QtQuick.Controls 2.12
import QtQuick.Dialogs 1.0 as QD
import GraphUtils 1.0

Item {
    id: root
    property var activeSpectrum
    property int activeIndex: -1
    property var spectrumGraphs: ({})

    signal addItem(var spectrum)
    signal selected(var spectrum, bool state)
    signal removeRequested(var spectrum)        // V20.7

    // V20.7: rows are found by the graph's index (rows go away on delete)
    function rowOf(index){
        for (var i = 0; i < listModel.count; i++){
            if (listModel.get(i).index == index)
                return i;
        }
        return -1;
    }
    function itemCount(){ return listModel.count; }
    function itemName(index){
        var r = rowOf(index);
        return r >= 0 ? listModel.get(r).name : '';
    }
    function removeItem(index){
        var r = rowOf(index);
        if (r >= 0)
            listModel.remove(r);
        delete root.spectrumGraphs[index];
    }
    function renameItem(index, name){
        var spectrum = root.spectrumGraphs[index];
        name = (name || '').trim();
        if (!spectrum || !name)
            return false;
        spectrum.name = name;
        return true;
    }

    onActiveSpectrumChanged: {
        if (root.activeSpectrum){
            root.activeIndex = root.activeSpectrum.index;
        }
    }

    onAddItem: {
        var index = spectrum.index;
        root.spectrumGraphs[index] = spectrum;
        var data = {
            'index':index,
            'name':spectrum.name,
            'color':spectrum.color,
        };
        listModel.append(data);
        spectrum.onNameChanged.connect(function(){
            var r = root.rowOf(index);
            if (r >= 0)
                listModel.get(r).name = spectrum.name;
        });
        spectrum.onColorChanged.connect(function(){
            var r = root.rowOf(index);
            if (r >= 0)
                listModel.get(r).color = spectrum.color;
        });
    }

    ListModel {
        id: listModel
        dynamicRoles: true
    }

    ListView {
        id: listView
        anchors.fill: parent
        model: listModel

        spacing: 2
        contentWidth: listView.width

        ButtonGroup {
            id: btnGroup

            onClicked: {
                var spectrum = root.spectrumGraphs[button.itemIndex];
                if (spectrum.index == root.activeIndex){
                    return;
                }
                root.selected(spectrum, true);
            }
        }

        delegate: ChartSelectDelegate {
            ButtonGroup.group: btnGroup
            text: name
            itemIndex: index
            itemName: name
            itemColor: color
            checked: root.activeIndex == index
            graphVisible: root.spectrumGraphs[itemIndex].graphVisible
            width: listView.contentWidth
            onColorButtonPressed: {
                var spectrum = root.spectrumGraphs[itemIndex];
                colorDialog.activate(spectrum);
            }
            onVisibleCheckBoxPressed: {
                root.spectrumGraphs[itemIndex].graphVisible = state;
            }
            onRenameRequested: renameDialog.ask(itemIndex)
            onDeleteRequested: deleteDialog.ask(itemIndex)
        }
    }

    Dialog {
        id: renameDialog
        objectName: 'renameScanDialog'
        property int itemIndex: -1
        title: 'Rename scan'
        modal: true
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 420
        standardButtons: Dialog.Ok | Dialog.Cancel
        function ask(index){
            var spectrum = root.spectrumGraphs[index];
            if (!spectrum)
                return;
            renameDialog.itemIndex = index;
            nameField.text = spectrum.name;
            renameDialog.open();
            nameField.selectAll();
            nameField.forceActiveFocus();
        }
        ColumnLayout {
            width: parent.width
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                font.pointSize: 9
                opacity: 0.85
                text: 'For example the time, the place or "mics on" / "mics off".'
            }
            TextField {
                id: nameField
                objectName: 'renameScanField'
                Layout.fillWidth: true
                selectByMouse: true
                onAccepted: renameDialog.accept()
            }
        }
        onAccepted: root.renameItem(renameDialog.itemIndex, nameField.text)
    }

    Dialog {
        id: deleteDialog
        objectName: 'deleteScanDialog'
        property int itemIndex: -1
        title: 'Delete scan'
        modal: true
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 420
        standardButtons: Dialog.Yes | Dialog.Cancel
        function ask(index){
            var spectrum = root.spectrumGraphs[index];
            if (!spectrum)
                return;
            deleteDialog.itemIndex = index;
            var running = !!(spectrum.scanner && spectrum.scanner.running);
            deleteDialog.standardButtons = running ? Dialog.Cancel : (Dialog.Yes | Dialog.Cancel);
            deleteLbl.text = running ? 'This scan is running: stop it first.' :
                ('Remove "' + spectrum.name + '" from the graph? A scan that was not ' +
                 'exported to a file is lost.');
            deleteDialog.open();
        }
        Label {
            id: deleteLbl
            width: parent.width
            wrapMode: Text.Wrap
        }
        onAccepted: {
            var spectrum = root.spectrumGraphs[deleteDialog.itemIndex];
            if (spectrum)
                root.removeRequested(spectrum);
        }
    }

    Loader {
        id: colorDialog
        sourceComponent: Component { QD.ColorDialog {} }
        property var spectrum
        property color color
        active: false

        function activate(spectrum){
            colorDialog.spectrum = spectrum;
            colorDialog.color = spectrum.color;
            colorDialog.active = true;
        }

        function close(){
            var dlg = colorDialog.item;
            if (dlg){
                dlg.close();
            }
            colorDialog.spectrum = null;
            colorDialog.active = false;
        }

        onItemChanged: {
            var dlg = item;
            if (dlg){
                dlg.color = colorDialog.color;
                dlg.open();
            }
        }

        Connections {
            target: colorDialog.item
            function onAccepted() {
                var dlg = colorDialog.item;
                colorDialog.spectrum.color = dlg.color;
                dlg.close();
                colorDialog.close();
            }
            function onRejected() {
                var dlg = colorDialog.item;
                dlg.close();
                colorDialog.close();
            }
        }
    }
}
