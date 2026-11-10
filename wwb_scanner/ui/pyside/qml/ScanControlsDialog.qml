import QtQuick 2.0
import QtQuick.Layouts 1.11
import QtQuick.Controls 2.12
import ScanTools 1.0

Dialog {
    id: root
    property ScanConfig config
    property ScannerInterface scanner
    property real sampleRate
    property alias samplesPerSweep: samplesPerSweepInput.value
    property alias sweepsPerScan: sweepsPerScanInput.value
    property alias sweepOverlapRatio: sweepOverlapRatioInput.value
    property alias windowType: windowCombo.textValue
    property alias windowSize: windowSizeInput.value
    property alias smoothingEnabled: smoothingSwitch.checked
    property alias smoothingFactor: smoothingFactorInput.value
    property alias scalingEnabled: scalingSwitch.checked
    property alias scalingMinDB: scalingMinDBInput.value
    property alias scalingMaxDB: scalingMaxDBInput.value
    property alias cleanRipple: cleanRippleSwitch.checked
    property alias cleanSpurs: cleanSpursSwitch.checked
    property alias cleanFloor: cleanFloorSwitch.checked
    property alias autoGain: autoGainSwitch.checked
    title: 'Scan settings'

    function stateColor(st){
        return st === 'ok' ? '#3fb950' : (st === 'warn' ? '#d29922' : root.palette.mid);
    }
    readonly property string floorCalHelp: 'Scan without antenna (about 3 minutes) that records the ' +
        "floor shape and the dongle's own tones. Valid only for the gain and sample rate it was made " +
        'at: repeat it after changing the gain, the dongle or the driver.'
    readonly property string stepsCalHelp: 'With the antenna connected (about 2 minutes): strong ' +
        'broadcast signals are read at every gain step, up and down. The real steps of a dongle differ ' +
        'from the nominal ones by up to 3-4 dB; Overload re-measuring uses the real ones.'
    function updateCalibrationText(){
        if (!root.scanner) {
            calibrationLbl.text = '';
            stepsLbl.text = '';
            return;
        }
        var c = root.scanner.calibrationInfo();
        calibrationLbl.text = c.floorText;
        floorDot.state_ = c.floorState;
        floorTip.text = root.floorCalHelp + (c.floorTip ? '\n\nNow: ' + c.floorTip : '');
        stepsLbl.text = c.stepsText;
        stepsDot.state_ = c.stepsState;
        stepsTip.text = root.stepsCalHelp + (c.stepsTip ? '\n\nNow: ' + c.stepsTip : '');
    }

    Connections {
        target: root.scanner
        onCalibrationChanged: root.updateCalibrationText()
    }
    Connections {
        target: root.scanner
        // 'running' has no change signal for bindings: follow the run state
        onScannerRunState: {
            calibrateBtn.enabled = !root.scanner.running;
            calibrateGainsBtn.enabled = !root.scanner.running;
            suggestGainBtn.enabled = !root.scanner.running;
        }
    }

    onWindowTypeChanged: {
        if (!root.windowType){
            return;
        }
        windowCombo.setFromString(root.windowType);
    }

    function getFreqResolution(){
        if (!root.scanner){
            return -1;
        }
        var fs = root.sampleRate,
            nfft = root.windowSize;
        if (!fs || !nfft){
            return -1;
        }
        var r = root.scanner.getFreqResolution(nfft, fs);
        freqResolutionLbl.value = r;
    }

    onScannerChanged: {
        root.sampleRate = Qt.binding(function(){ return root.scanner.sampleRate });
        getFreqResolution();
    }

    onSampleRateChanged: { getFreqResolution() }
    onWindowSizeChanged: { getFreqResolution() }

    ColumnLayout {
        anchors.fill: parent

        GroupBox {
            title: 'Sweep Parameters'

            RowLayout {
                anchors.fill: parent
                NumberInput {
                    id: samplesPerSweepInput
                    name: 'Samples Per Sweep'
                    isFloat: false
                    showFrame: true
                }
                NumberInput {
                    id: sweepsPerScanInput
                    name: 'Sweeps Per Scan'
                    isFloat: false
                    showFrame: true
                }
                NumberInput {
                    id: sweepOverlapRatioInput
                    name: 'Sweep Overlap Ratio'
                    showFrame: true
                }
            }
        }

        GroupBox {
            title: 'Window Parameters'

            RowLayout {
                anchors.fill: parent

                ComboBox {
                    id: windowCombo
                    property string textValue
                    model: [
                        'barthann', 'bartlett', 'blackman', 'blackmanharris',
                        'bohman', 'boxcar', 'cosine', 'flattop', 'general_cosine',
                        'general_hamming', 'hamming', 'hann', 'hanning',
                        'nuttall', 'parzen', 'triang',
                    ]
                    onTextValueChanged: { windowCombo.setFromString(windowCombo.textValue) }
                    function setFromString(wname){
                        var idx = windowCombo.find(wname);
                        windowCombo.currentIndex = idx;
                        return idx;
                    }
                }

                NumberInput {
                    id: windowSizeInput
                    name: 'Window Size'
                    isFloat: false
                    showFrame: true
                }
                Label {
                    id: freqResolutionLbl
                    property real value
                    property bool isEvenFreq: freqResolutionLbl.value*1e6 == parseInt(freqResolutionLbl.value*1e6)
                    text: "Resolution: \n" + freqResolutionLbl.value.toString() + " MHz"
                    font.pointSize: 9
                    states: [
                        State {
                            name: 'normal'
                            when: freqResolutionLbl.isEvenFreq
                            PropertyChanges { target: freqResolutionLbl; color: '#008000' }
                        },
                        State {
                            name: 'critical'
                            when: !freqResolutionLbl.isEvenFreq
                            PropertyChanges { target: freqResolutionLbl; color: '#800000'}
                        }
                    ]
                }
            }
        }

        GroupBox {
            title: 'Smoothing'

            RowLayout {
                anchors.fill: parent
                Switch {
                    id: smoothingSwitch
                    text: 'Enabled'
                }
                NumberInput {
                    id: smoothingFactorInput
                    name: 'Factor'
                    showFrame: true
                }
            }
        }

        GroupBox {
            title: 'Scaling'

            RowLayout {
                anchors.fill: parent
                Switch {
                    id: scalingSwitch
                    text: 'Enabled'
                }
                NumberInput {
                    id: scalingMinDBInput
                    name: 'Minimum (dB)'
                    showFrame: true
                }
                NumberInput {
                    id: scalingMaxDBInput
                    name: 'Maximum (dB)'
                    showFrame: true
                }
            }
        }

        GroupBox {
            id: cleanBox
            objectName: 'cleanScanBox'
            title: 'Clean scan'
            Layout.fillWidth: true

            ColumnLayout {
                anchors.fill: parent
                spacing: 6

                GridLayout {
                    columns: 4
                    columnSpacing: 6
                    rowSpacing: 0
                    Switch {
                        id: cleanSpursSwitch
                        objectName: 'cleanSpursSwitch'
                        text: "Remove dongle's own spurs"
                    }
                    InfoTip {
                        Layout.rightMargin: 18
                        text: "The dongle makes its own weak tones at multiples of 28.8 MHz (and at " +
                              "480 MHz). A tone exactly there and only 75 kHz wide is replaced by the " +
                              "level around it. A real transmitter on that frequency is wider and stays."
                    }
                    Switch {
                        id: cleanRippleSwitch
                        objectName: 'cleanRippleSwitch'
                        text: 'Remove tile ripple'
                    }
                    InfoTip {
                        text: 'Every 2.4 MHz tile has a slightly uneven floor (about \u00b10.2 dB) that ' +
                              'repeats every 0.775 MHz. It is measured from the scan itself and removed. ' +
                              'No calibration needed.'
                    }
                    Switch {
                        id: cleanFloorSwitch
                        objectName: 'cleanFloorSwitch'
                        text: 'Level the floor'
                    }
                    InfoTip {
                        Layout.rightMargin: 18
                        text: "Without antenna the dongle's floor is not flat (a hump below 300 MHz, about " +
                              "2 dB lower above 1 GHz). With a floor calibration made at the same gain the " +
                              "scan is levelled, so '3 dB over the floor' means the same in every band."
                    }
                    Switch {
                        id: autoGainSwitch
                        objectName: 'autoGainSwitch'
                        text: 'Overload: re-measure with less gain'
                    }
                    InfoTip {
                        text: 'Tiles where a strong signal hits the limit of the 8-bit converter are read ' +
                              'again at lower gain and scaled back by the real gain difference (Gain steps ' +
                              'below). Levels near strong transmitters stay right instead of compressed; ' +
                              'the scan takes a little longer.'
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: root.palette.mid; opacity: 0.5 }

                GridLayout {
                    columns: 4
                    columnSpacing: 8
                    rowSpacing: 4
                    Label { text: 'Floor calibration'; font.pointSize: 9 }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Rectangle {
                            id: floorDot
                            objectName: 'floorCalDot'
                            property string state_: 'none'
                            width: 8; height: 8; radius: 4
                            color: root.stateColor(state_)
                        }
                        Label {
                            id: calibrationLbl
                            objectName: 'calibrationLabel'
                            property string tip
                            Layout.fillWidth: true
                            Layout.maximumWidth: 330
                            elide: Text.ElideRight
                            font.pointSize: 9
                        }
                    }
                    InfoTip {
                        id: floorTip
                        text: ''
                    }
                    Button {
                        id: calibrateBtn
                        objectName: 'calibrateButton'
                        text: 'Calibrate...'
                        onClicked: calibrateConfirm.open()
                    }

                    Label { text: 'Gain steps'; font.pointSize: 9 }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Rectangle {
                            id: stepsDot
                            objectName: 'gainStepsDot'
                            property string state_: 'none'
                            width: 8; height: 8; radius: 4
                            color: root.stateColor(state_)
                        }
                        Label {
                            id: stepsLbl
                            objectName: 'gainStepsLabel'
                            Layout.fillWidth: true
                            Layout.maximumWidth: 330
                            elide: Text.ElideRight
                            font.pointSize: 9
                        }
                    }
                    InfoTip {
                        id: stepsTip
                        text: ''
                    }
                    Button {
                        // V20.5
                        id: calibrateGainsBtn
                        objectName: 'calibrateGainsButton'
                        text: 'Calibrate...'
                        onClicked: calibrateGainsConfirm.open()
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: root.palette.mid; opacity: 0.5 }

                RowLayout {
                    spacing: 8
                    Button {
                        // V20.6
                        id: suggestGainBtn
                        objectName: 'suggestGainButton'
                        text: 'Suggest gain'
                        onClicked: root.suggestGain()
                    }
                    InfoTip {
                        text: 'Sets the gain in Device Settings for the scan range and the Overload switch, ' +
                              'from a gain profile: for every band and gain step, how much sensitivity is ' +
                              'lost and how many tiles clip. Overload on: the most sensitive step. Overload ' +
                              'off: the most sensitive step at which at most 5 % of the tiles clip. The ' +
                              'built-in profile was measured at one site (Warsaw); gain_survey.py --analyze ' +
                              'makes one for yours.'
                    }
                    Label {
                        id: suggestGainLbl
                        objectName: 'suggestGainLabel'
                        Layout.fillWidth: true
                        Layout.maximumWidth: 420
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.pointSize: 9
                        opacity: 0.85
                        text: 'Gain now: ' + (root.scanner ? root.scanner.gain.toFixed(1) + ' dB' : '?')
                    }
                }
            }
        }
    }

    Dialog {
        id: calibrateGainsConfirm
        objectName: 'calibrateGainsConfirm'
        title: 'Calibrate gain steps'
        modal: true
        anchors.centerIn: Overlay.overlay
        width: 520
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label {
            width: calibrateGainsConfirm.availableWidth
            wrapMode: Text.Wrap
            text: '1. Keep the antenna CONNECTED: the real gain steps are measured on ' +
                  'signals of the air (FM, DAB, TV, mobile).\n' +
                  '2. Your own transmitters may stay on, but not right next to the antenna.\n' +
                  '3. OK starts a short survey 30-1200 MHz and then reads the strongest ' +
                  'tiles at every gain, up and down (about 2 minutes).\n\n' +
                  'Auto gain then scales re-measured tiles by the real steps of this dongle ' +
                  'instead of the nominal ones. Repeat it after changing the dongle or the ' +
                  'sample rate.'
        }
        onAccepted: {
            if (root.scanner.running)
                return;
            root.commitSettings();
            root.close();
            root.scanner.calibrateGains();
        }
    }

    Dialog {
        id: calibrateConfirm
        objectName: 'calibrateConfirm'
        title: 'Calibrate dongle'
        modal: true
        anchors.centerIn: Overlay.overlay
        width: 520
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label {
            width: calibrateConfirm.availableWidth
            wrapMode: Text.Wrap
            text: '1. Unscrew the antenna (leave the connector empty).\n' +
                  '2. If you can, switch off your own transmitters (IEM, mics).\n' +
                  '3. OK starts a scan 31-1766 MHz (about 3 minutes) with the ' +
                  'current gain. It records the floor shape of this dongle.\n\n' +
                  'Repeat it when you change the gain or the dongle, or after a ' +
                  'driver (DLL) update.'
        }
        onAccepted: {
            if (root.scanner.running)
                return;
            root.commitSettings();
            root.close();
            root.scanner.calibrate();
        }
    }

    standardButtons: Dialog.Ok | Dialog.Cancel

    // V20.6: gain step from the gain survey profile for the scan range and
    // the auto gain switch; main.qml puts it into Device Settings
    signal gainSuggested(real gain)
    function suggestGain(){
        if (!root.scanner)
            return;
        var r = root.scanner.suggestGain(root.autoGain);
        if (r[0] >= 0) {
            root.gainSuggested(r[0]);
            suggestGainLbl.text = 'Set to ' + r[1];
            root.updateCalibrationText();       // its calibration (per gain)
        } else {
            suggestGainLbl.text = r[1];
        }
    }

    function commitSettings(){
        config.samplesPerSweep = root.samplesPerSweep;
        config.sweepsPerScan = root.sweepsPerScan;
        config.sweepOverlapRatio = root.sweepOverlapRatio;
        config.windowType = root.windowType;
        config.windowSize = root.windowSize;
        config.smoothingEnabled = root.smoothingEnabled;
        config.smoothingFactor = root.smoothingFactor;
        config.scalingEnabled = root.scalingEnabled;
        config.scalingMinDB = root.scalingMinDB;
        config.scalingMaxDB = root.scalingMaxDB;
        config.cleanRipple = root.cleanRipple;
        config.cleanSpurs = root.cleanSpurs;
        config.cleanFloor = root.cleanFloor;
        config.autoGain = root.autoGain;
    }

    function reloadSettings(){
        if (!config){
            return;
        }
        root.samplesPerSweep = config.samplesPerSweep;
        root.sweepsPerScan = config.sweepsPerScan;
        root.sweepOverlapRatio = config.sweepOverlapRatio;
        root.windowType = config.windowType;
        root.windowSize = config.windowSize;
        root.smoothingEnabled = config.smoothingEnabled;
        root.smoothingFactor = config.smoothingFactor;
        root.scalingEnabled = config.scalingEnabled;
        root.scalingMinDB = config.scalingMinDB;
        root.scalingMaxDB = config.scalingMaxDB;
        root.cleanRipple = config.cleanRipple;
        root.cleanSpurs = config.cleanSpurs;
        root.cleanFloor = config.cleanFloor;
        root.autoGain = config.autoGain;
        root.updateCalibrationText();
    }

    onAccepted: {
        commitSettings();
        root.close();
    }

    onRejected: {
        reloadSettings();
        root.close();
    }
    onAboutToShow: {
        reloadSettings();
    }
    Component.onCompleted: {
        reloadSettings();
    }
}
