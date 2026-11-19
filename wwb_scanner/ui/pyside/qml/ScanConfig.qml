import QtQuick 2.0
import WwbCompat 1.0
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
    property alias bandGain: model.bandGain
    property alias scanRanges: model.scanRanges

    signal configUpdate()

    ScanConfigData {
        id: model
        // V22.0: the starting values (the tested configuration) are set by
        // ScanConfigData itself from the core (scanner/config.py TESTED);
        // the settings below replace them with what the user has saved.

        onConfigUpdate: {
            root.configUpdate();
        }
    }

    WwbSettings {
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
        property alias bandGain: model.bandGain
        property alias scanRanges: model.scanRanges
    }
}
