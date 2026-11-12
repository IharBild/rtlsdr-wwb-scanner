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
    property alias cleanRipple: cleanRippleSwitch.checked
    property alias cleanSpurs: cleanSpursSwitch.checked
    property alias cleanImages: cleanImagesSwitch.checked
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
        function onCalibrationChanged() { root.updateCalibrationText(); }
    }
    Connections {
        target: root.scanner
        // 'running' has no change signal for bindings: follow the run state
        function onScannerRunState() {
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

    // V20.8: the dialog is about 890 px tall and had no scrolling: on a
    // 1366x768 laptop (or 1080p with 125 % Windows scaling) the calibration
    // block and "Suggest gain" were cut off and could not be reached. Now it
    // is never taller than the window, the content scrolls, and it opens in
    // the middle of the window (it used to open in the top left corner).
    anchors.centerIn: Overlay.overlay
    height: Math.min(implicitHeight, Overlay.overlay ? Overlay.overlay.height - 16 : implicitHeight)

    Flickable {
        id: scroller
        objectName: 'scanSettingsScroller'
        anchors.fill: parent
        clip: true
        implicitWidth: settingsColumn.implicitWidth + 16
        implicitHeight: settingsColumn.implicitHeight
        contentWidth: width
        contentHeight: settingsColumn.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {
            policy: scroller.contentHeight > scroller.height + 1 ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
        }

    // (the lines of the column keep their old indentation)
    ColumnLayout {
        id: settingsColumn
        width: scroller.width - 16

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

        // V21.0: the Smoothing and Scaling groups were here (audit A4): both
        // changed the levels on the graph and in the exported files after
        // the scan had been saved.

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
                        text: "Hide dongle's own spurs"
                    }
                    InfoTip {
                        Layout.rightMargin: 18
                        text: "The dongle makes its own weak tones at multiples of 14.4 MHz (and at " +
                              "480 MHz). A tone exactly there, only 75 kHz wide and not more than 20 dB " +
                              "above the floor is replaced by the level around it - on the graph and in " +
                              "the exported files. A stronger or wider signal on such a frequency stays " +
                              "and is marked: it may be a transmitter. Off: nothing is hidden."
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
                    Switch {
                        id: cleanImagesSwitch
                        objectName: 'cleanImagesSwitch'
                        text: 'Hide images of strong signals'
                    }
                    InfoTip {
                        Layout.rightMargin: 18
                        text: 'A strong carrier shows weak copies of itself 2.4 MHz and 28.8 MHz away. ' +
                              'Off (default): such a copy stays in the scan and is only marked "image?" - ' +
                              'a real weak signal there looks the same and must not disappear. On: it is ' +
                              'replaced by the floor around it, on the graph and in the exported files.'
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
                            ToolTip.visible: truncated && hoverArea_calibrationLbl.containsMouse
                            ToolTip.text: text
                            MouseArea { id: hoverArea_calibrationLbl; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
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
                            ToolTip.visible: truncated && hoverArea_stepsLbl.containsMouse
                            ToolTip.text: text
                            MouseArea { id: hoverArea_stepsLbl; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
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
    }   // Flickable (V20.8)

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
        config.cleanRipple = root.cleanRipple;
        config.cleanSpurs = root.cleanSpurs;
        config.cleanImages = root.cleanImages;
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
        root.cleanRipple = config.cleanRipple;
        root.cleanSpurs = config.cleanSpurs;
        root.cleanImages = config.cleanImages;
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
