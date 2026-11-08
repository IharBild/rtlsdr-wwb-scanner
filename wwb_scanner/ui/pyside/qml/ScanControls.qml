import QtQuick 2.0
import QtQuick.Layouts 1.11
import QtQuick.Controls 2.12
import Qt.labs.settings 1.0

RowLayout {
    id: root
    property ScanConfig config
    property alias startFreq: startFreqInput.value
    property alias endFreq: endFreqInput.value
    property bool configReady: false
    property bool scanRunning: false
    property bool scanReady: !root.scanRunning
    property alias progress: progressBar.value

    signal scannerState(bool state)
    signal openBands()
    // V18: several ranges chosen in Bands (JSON), '' = one range
    property string scanRanges: config ? config.scanRanges : ''
    property int rangeCount: {
        try { var r = JSON.parse(scanRanges); return r.length || 0; } catch (e) { return 0; }
    }

    onConfigChanged: {
        if (!config){
            return;
        }
        configUpdateCallback();
        root.configReady = true;
        config.configUpdate.connect(configUpdateCallback);
    }

    function configUpdateCallback(){
        if (root.startFreq != config.startFreq){
            root.startFreq = config.startFreq;
        }
        if (root.endFreq != config.endFreq){
            root.endFreq = config.endFreq;
        }
    }

    NumberInput {
        id: startFreqInput
        name: 'Start Freq'
        Layout.leftMargin: 6
        labelFontSize: 10
        onSubmit: {
            root.config.scanRanges = '';
            root.config.startFreq = startFreqInput.value;
        }
    }
    NumberInput {
        id: endFreqInput
        name: 'End Freq'
        labelFontSize: 10
        onSubmit: {
            root.config.scanRanges = '';
            root.config.endFreq = endFreqInput.value;
        }
    }

    // V16: range presets (built-in + your own, remembered between runs)
    Settings {
        id: presetSettings
        category: 'Scan Presets'
        property string custom: '[]'
        property int lastIndex: -1
    }
    property var builtinPresets: [
        [470, 700, 'UHF 470-700'], [470, 800, 'UHF 470-800'], [470, 1000, '470-1000'],
        [30, 1766, 'All 30-1766'], [174, 240, 'VHF III 174-240'], [823, 865, 'Mics 823-865'],
        [1350, 1525, 'L-band 1350-1525']
    ]
    property var customPresets: []
    function loadCustom() {
        try { customPresets = JSON.parse(presetSettings.custom); } catch (e) { customPresets = []; }
        rebuildPresetModel();
        if (presetCombo.currentIndex < 0)
            presetCombo.currentIndex = 0;
    }
    function rebuildPresetModel() {
        presetModel.clear();
        presetModel.append({'text': 'Preset...', 'lo': 0, 'hi': 0, 'custom': false});
        for (var i = 0; i < builtinPresets.length; i++)
            presetModel.append({'text': builtinPresets[i][2], 'lo': builtinPresets[i][0],
                                'hi': builtinPresets[i][1], 'custom': false});
        for (var j = 0; j < customPresets.length; j++)
            presetModel.append({'text': '* ' + customPresets[j][0] + '-' + customPresets[j][1],
                                'lo': customPresets[j][0], 'hi': customPresets[j][1], 'custom': true});
    }
    ListModel { id: presetModel }
    Component.onCompleted: loadCustom()

    ComboBox {
        id: presetCombo
        Layout.preferredWidth: 170
        model: presetModel
        textRole: 'text'
        enabled: !root.scanRunning
        onActivated: {
            var it = presetModel.get(index);
            if (it.hi > it.lo && root.config) {
                root.config.scanRanges = '';
                root.config.startFreq = it.lo;
                root.config.endFreq = it.hi;
            }
            presetSettings.lastIndex = index;
        }
    }
    ToolButton {
        text: '+'
        enabled: !root.scanRunning
        ToolTip.visible: hovered
        ToolTip.text: 'Save the current range as a preset'
        onClicked: {
            var lo = root.startFreq, hi = root.endFreq;
            for (var i = 0; i < customPresets.length; i++)
                if (customPresets[i][0] == lo && customPresets[i][1] == hi) return;
            var c = customPresets.slice();
            c.push([lo, hi]);
            presetSettings.custom = JSON.stringify(c);
            loadCustom();
            presetCombo.currentIndex = presetModel.count - 1;
        }
    }
    ToolButton {
        text: '\u2212'
        enabled: !root.scanRunning && presetCombo.currentIndex > 0
                 && presetModel.count > presetCombo.currentIndex
                 && presetModel.get(presetCombo.currentIndex).custom
        ToolTip.visible: hovered
        ToolTip.text: 'Delete the selected own preset'
        onClicked: {
            var it = presetModel.get(presetCombo.currentIndex), c = [];
            for (var i = 0; i < customPresets.length; i++)
                if (!(customPresets[i][0] == it.lo && customPresets[i][1] == it.hi)) c.push(customPresets[i]);
            presetSettings.custom = JSON.stringify(c);
            loadCustom();
            presetCombo.currentIndex = 0;
        }
    }

    ToolSeparator { }

    // V18: bands of the systems + country rules
    ToolButton {
        id: bandsBtn
        text: root.rangeCount > 1 ? 'Bands: ' + root.rangeCount + ' ranges' : 'Bands...'
        enabled: !root.scanRunning
        highlighted: root.rangeCount > 1
        ToolTip.visible: hovered
        ToolTip.text: root.rangeCount > 1
                      ? 'Next scan covers only the chosen system bands. Type a range or pick a preset to scan one range again.'
                      : 'Choose your systems (Shure, Sennheiser) and the country: scan only their bands'
        onClicked: root.openBands()
    }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true
    }

    // V16: time left, from the progress so far
    property real scanStartMs: 0
    property string timeLeft: ''
    onScanRunningChanged: {
        scanStartMs = scanRunning ? Date.now() : 0;
        timeLeft = '';
    }
    Timer {
        interval: 1000
        repeat: true
        running: root.scanRunning
        onTriggered: {
            var p = root.progress, el = (Date.now() - root.scanStartMs) / 1000;
            if (p > 0.02 && p < 1 && el > 3) {
                var left = Math.max(0, Math.round(el * (1 - p) / p));
                root.timeLeft = Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2) + ' left';
            }
        }
    }
    Label {
        visible: root.scanRunning
        text: root.timeLeft
        font.pointSize: 9
        Layout.minimumWidth: 70
        horizontalAlignment: Text.AlignRight
    }

    ProgressBar {
        id: progressBar
        visible: root.scanRunning
        // colours come from the window palette (accent); the hard-coded
        // green/grey did not fit the dark theme and failed in other styles
    }

    ToolSeparator { }

    ToolButton {
        id: scanStartBtn
        text: "Start"
        icon.name: 'media-playback-start'
        enabled: root.scanReady
        onClicked: root.scannerState(true)
    }
    ToolButton {
        id: scanStopBtn
        text: "Stop"
        icon.name: 'media-playback-stop'
        Layout.rightMargin: 6
        // enabled: !root.scanReady
        onClicked: root.scannerState(false)
    }
}
