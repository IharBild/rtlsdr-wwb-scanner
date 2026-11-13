import QtQuick 2.0
import Qt.labs.settings 1.0
import ScanTools 1.0

Item {
    id: root

    property ScanConfigData model: model
    property alias startFreq: model.startFreq
    property alias endFreq: model.endFreq
    property alias samplesPerSweep: model.samplesPerSweep
    property alias sweepsPerScan: model.sweepsPerScan
    property alias sweepOverlapRatio: model.sweepOverlapRatio
    property alias windowType: model.windowType
    property alias windowSize: model.windowSize
    property alias cleanRipple: model.cleanRipple
    property alias cleanSpurs: model.cleanSpurs
    property alias cleanImages: model.cleanImages
    property alias cleanFloor: model.cleanFloor
    property alias autoGain: model.autoGain
    property alias verifyPeaks: model.verifyPeaks
    property alias scanRanges: model.scanRanges

    signal configUpdate()

    ScanConfigData {
        id: model
        startFreq: 470.
        endFreq: 536.
        // V20.8: the tested configuration (see FACTORY in scanner.py);
        // before: 8192 x 20, overlap 0.5, window 128
        samplesPerSweep: 2048
        sweepsPerScan: 40
        sweepOverlapRatio: 0.65
        windowType: 'hann'
        windowSize: 96
        cleanRipple: true
        cleanSpurs: true
        cleanImages: false      // V20.9: images are marked, hidden only on request
        cleanFloor: true
        autoGain: true
        verifyPeaks: true       // V21.2: second-gain check of peaks next to strong carriers
        scanRanges: ''

        onConfigUpdate: {
            root.configUpdate();
        }
    }

    Settings {
        id: settings
        category: 'Scan Config'
        property alias startFreq: model.startFreq
        property alias endFreq: model.endFreq
        property alias samplesPerSweep: model.samplesPerSweep
        property alias sweepsPerScan: model.sweepsPerScan
        property alias sweepOverlapRatio: model.sweepOverlapRatio
        property alias windowType: model.windowType
        property alias windowSize: model.windowSize
        property alias cleanRipple: model.cleanRipple
        property alias cleanSpurs: model.cleanSpurs
        property alias cleanImages: model.cleanImages
        property alias cleanFloor: model.cleanFloor
        property alias autoGain: model.autoGain
        property alias verifyPeaks: model.verifyPeaks
        property alias scanRanges: model.scanRanges
    }
}
