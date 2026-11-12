import QtQuick 2.0
import QtQuick.Layouts 1.11
import QtQuick.Controls 2.12
import Qt.labs.settings 1.0
import DeviceConfig 1.0

Dialog {
    id: root
    objectName: 'deviceConfigPopup'
    title: 'Device Settings'
    anchors.centerIn: Overlay.overlay
    property var device
    property real deviceIndex
    property string deviceSerial
    // V20.8: the tested configuration (see FACTORY in scanner.py);
    // before: 2048 and 0 dB (30-37 dB less sensitive in 470-700 MHz)
    property real sampleRate: 2400
    property real gain: 33.8

    onDeviceChanged:{
        var device = device_list.device;
        gainSelect.updateChoices();
        if (device) {
            root.deviceIndex = device.device_index;
            root.deviceSerial = device.device_serial;
        }
    }

    Settings {
        category: 'Device Config'
        property alias deviceIndex: root.deviceIndex
        property alias deviceSerial: root.deviceSerial
        property alias sampleRate: root.sampleRate
        property alias gain: root.gain
    }

    DeviceInfoList {
        id: device_list
        onDevicesChanged: {
            updateDeviceList();
        }
    }

    // V21.1 (audit D2): what the field accepts. kHz as before; a value below
    // 100 is taken as MHz ("2.4" was read as 2 kHz). The RTL2832U runs at
    // 225-300 and 900-3200 kHz; anything else leaves the old value.
    function parseSampleRate(text){
        var v = parseFloat(String(text).replace(',', '.'));
        if (!(v > 0))
            return NaN;
        if (v < 100)
            v = v * 1000;
        v = Math.round(v);
        if ((v >= 225 && v <= 300) || (v >= 900 && v <= 3200))
            return v;
        return NaN;
    }

    // V21.1 (audit D2): Cancel puts back what was set when the dialog opened
    property var atOpen: ({})
    onAboutToShow: {
        root.atOpen = {'gain': root.gain, 'sampleRate': root.sampleRate, 'device': root.device};
        sampleRateField.text = root.sampleRate.toString();
        sampleRateNote.text = '';
    }
    function restoreAtOpen(){
        var o = root.atOpen;
        if (o.sampleRate !== undefined)
            root.sampleRate = o.sampleRate;
        if (o.device !== undefined && o.device !== root.device){
            root.device = o.device;
            if (root.device)
                device_select.currentIndex = root.device.device_index;
        }
        if (o.gain !== undefined)
            root.setGain(o.gain);
        sampleRateField.text = root.sampleRate.toString();
    }

    // V20.8: the dongles were listed only once, when the program started: a
    // dongle plugged in later was not found until a restart. Start looks
    // again when it has no dongle (main.qml).
    function refreshDevices(){
        device_list.update_devices();
    }

    // V20.6: "Suggest gain" in Scan settings
    function setGain(g){
        root.gain = g;
        var items = gainSelect.items;
        for (var i=0;i<items.length;i++){
            if (Math.abs(items[i] - g) < 0.05){
                gainSelect.value = i;
            }
        }
    }

    function updateDeviceList(){
        var devices = device_list.devices,
            device,
            data;
        device_model.clear();
        for (var i=0;i<devices.length;i++){
            device = devices[i];
            data = {
                'index':i,
                'device_index':device.device_index,
                'device_serial':device.device_serial,
                'text':device.text,
            };
            device_model.append(data);
            if (!root.device) {
                if (root.deviceSerial) {
                    if (device.device_serial == root.deviceSerial){
                        root.device = device;
                    }
                } else {
                    if (root.deviceIndex !== undefined) {
                        if (device.device_index == root.deviceIndex){
                            root.device = device;
                        }
                    } else {
                        if (device.device_index == 0){
                            root.device = device;
                        }
                    }
                }
            }
        }
        if (root.device){
            device_select.currentIndex = root.device.device_index;
        }
    }

    ColumnLayout {
        anchors.fill: parent
        GroupBox {
            title: 'Device Select'
            Layout.fillWidth: true
            // V21.1 (audit D2): the list showed "R828D - 0 (" only
            implicitWidth: 340
            ComboBox {
                id: device_select
                objectName: 'deviceSelect'
                editable: false
                textRole: 'text'
                anchors.fill: parent

                model: ListModel{
                    id: device_model
                }
                onActivated: {
                    var idx = device_select.currentIndex,
                        item;
                    if (idx == -1){
                        // root.deviceIndex = -1
                        return;
                    } else {
                        item = device_model.get(idx);
                        root.device = device_list.devices[item.device_index];
                    }
                }
            }
        }

        GroupBox {
            // V21.1 (audit D2): the value is kHz (the title said Msps)
            title: "Sample Rate (kHz)"
            RowLayout {
                TextField {
                    id: sampleRateField
                    objectName: 'sampleRateField'
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                    maximumLength: 6
                    text: root.sampleRate.toString()
                    onEditingFinished: {
                        var v = root.parseSampleRate(sampleRateField.text);
                        if (isNaN(v)) {
                            sampleRateNote.text = '225-300 or 900-3200 kHz';
                        } else {
                            root.sampleRate = v;
                            sampleRateNote.text = '';
                        }
                        sampleRateField.text = root.sampleRate.toString();
                    }
                }
                Label {
                    id: sampleRateNote
                    objectName: 'sampleRateNote'
                    color: '#f0b44c'
                    font.pointSize: 9
                }
            }
        }

        GroupBox {
            title: "Gain"
            RowLayout {
            SpinBox {
                id: gainSelect
                property var items: [0]
                from: 0
                to: items.length-1
                signal updateChoices()

                onUpdateChoices:{
                    if (root.device){
                        gainSelect.items = root.device.gains;
                        // V20.8: the step of this tuner nearest to the wanted
                        // gain. An exact match was needed before: without it
                        // the box showed the first step and the scan used
                        // another value.
                        var best = -1, bestDiff = 1e9;
                        for (var i=0;i<gainSelect.items.length;i++){
                            var d = Math.abs(gainSelect.items[i] - root.gain);
                            if (d < bestDiff){
                                bestDiff = d;
                                best = i;
                            }
                        }
                        if (best >= 0){
                            gainSelect.value = best;
                            if (bestDiff > 0.05)
                                root.gain = gainSelect.items[best];
                        }
                    }
                }

                textFromValue: function(value) {
                    return items[value].toString();
                }
                valueFromText: function(text) {
                    var items = gainSelect.items;
                    for (var i=0;i<items.length;i++){
                        if (items[i].toString() == text){
                            return i;
                        }
                    }
                    return gainSelect.value;
                }
                onValueModified: {
                    root.gain = gainSelect.items[gainSelect.value];
                }
            }
            InfoTip {
                // V20.7
                text: 'Tuner gain (dB). Higher is more sensitive, but strong signals hit the limit ' +
                      'of the 8-bit converter sooner. Scan settings > Suggest gain picks a step for ' +
                      'the scan range; the floor calibration is valid only for the gain it was made at.'
            }
            }
        }
    }

    standardButtons: Dialog.Ok | Dialog.Cancel

    onAccepted:{
        root.close();
    }
    onRejected:{
        root.restoreAtOpen();       // V21.1
        root.close();
    }

    Component.onCompleted: {
        device_list.update_devices();
    }
}
