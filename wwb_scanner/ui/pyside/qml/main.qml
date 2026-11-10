import QtQuick 2.0
import QtQuick.Layouts 1.11
import QtQuick.Controls 2.12
import Qt.labs.settings 1.0
import QtCharts 2.13
import GraphUtils 1.0
import ScanTools 1.0
import StatusTools 1.0

ApplicationWindow {
    id: window
    visible: true

    width: 800
    height: 600

    Settings {
        property alias x: window.x
        property alias y: window.y
        property alias width: window.width
        property alias height: window.height
    }

    // Window theme (View > Theme). Dark: flat, low-contrast greys and one
    // accent colour, in the spirit of Smaart / Open Sound Meter.
    Settings {
        id: uiSettings
        category: 'User Interface'
        property bool darkWindow: true
        // V20.3: marks of V20.2 on the graph (View menu)
        property bool showLossBands: true
        property bool showImageMarks: true
    }
    property bool darkUi: uiSettings.darkWindow
    property color accent: "#3aa0ff"

    color: palette.window
    palette.window: darkUi ? "#16181b" : "#efefef"
    palette.windowText: darkUi ? "#cfd4da" : "#1e1e1e"
    palette.base: darkUi ? "#0f1113" : "#ffffff"
    palette.alternateBase: darkUi ? "#1c1f23" : "#f5f5f5"
    palette.text: darkUi ? "#e4e7eb" : "#000000"
    palette.button: darkUi ? "#23272c" : "#e4e4e4"
    palette.buttonText: darkUi ? "#cfd4da" : "#1e1e1e"
    palette.brightText: "#ffffff"
    palette.highlight: accent
    palette.highlightedText: "#ffffff"
    palette.light: darkUi ? "#2b3036" : "#ffffff"
    palette.midlight: darkUi ? "#262a2f" : "#e8e8e8"
    palette.mid: darkUi ? "#3a4047" : "#b4b4b4"
    palette.dark: darkUi ? "#0c0d0f" : "#9a9a9a"
    palette.shadow: "#000000"
    palette.link: accent
    palette.toolTipBase: darkUi ? "#23272c" : "#ffffdc"
    palette.toolTipText: darkUi ? "#e4e7eb" : "#000000"

    menuBar: MenuBar {
        Menu {
            title: qsTr("&File")
            Action {
                text: qsTr("&Import")
                onTriggered: importDialog.open()
            }
            Action {
                text: qsTr("&Export")
                onTriggered: {
                    // V20.4: the graph of a running scan holds only its first part
                    if (window.exportOfRunningScan()) {
                        exportUnfinishedDialog.open();
                        return;
                    }
                    exportDialog.graphData = chartWrapper.activeSpectrum;
                    exportDialog.open();
                }
            }
            Action {
                text: qsTr("&Device Settings")
                onTriggered: device_config.open()
            }
            Action {
                text: qsTr("S&can Settings")
                onTriggered: scanControlsDialog.open()
            }
            MenuSeparator { }
            Action {
                text: qsTr("&Quit")
                onTriggered: Qt.quit()
            }
        }
        Menu {
            title: qsTr("&Bands")
            Action {
                text: qsTr("&Systems and country...")
                onTriggered: bandsDialog.open()
            }
            Action {
                text: qsTr("Scan &one range again")
                enabled: scanConfig.scanRanges !== ''
                onTriggered: scanConfig.scanRanges = ''
            }
        }
        Menu {
            title: qsTr("&View")
            Action {
                text: qsTr("&Theme")
                onTriggered: themeSelect.open()
            }
            Action {
                text: qsTr("&Regulation strip") + (bandSettings.country ? ' (' + bandSettings.country + ')' : '')
                checkable: true
                checked: bandSettings.showRegulation
                onTriggered: bandSettings.showRegulation = checked
            }
            MenuSeparator {}
            Action {
                text: qsTr("&Lost sensitivity bands")
                checkable: true
                checked: uiSettings.showLossBands
                onTriggered: uiSettings.showLossBands = checked
            }
            Action {
                text: qsTr("Removed &image marks")
                checkable: true
                checked: uiSettings.showImageMarks
                onTriggered: uiSettings.showImageMarks = checked
            }
        }
    }

    header: ToolBar {
        ScanControls {
            id: scanControls
            anchors.fill: parent
            config: scanConfig
            progress: scanner.progress
            onOpenBands: bandsDialog.open()
            onScannerState: {
                if (state) {
                    scanner.start();
                    chartWrapper.newLiveScan(scanner);
                } else {
                    scanner.stop();
                }
            }
        }
    }

    footer: ToolBar {
        RowLayout {
            anchors.fill: parent

            Label {
                text: chartWrapper.mouseDataPoint.x > 0
                      ? chartWrapper.mouseDataPoint.x.toFixed(3) + ' MHz'
                      : '--- MHz'
                Layout.preferredWidth: 110
                Layout.leftMargin: 12
                font.pointSize: 9
                font.family: 'monospace'
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: Qt.AlignLeft
            }

            Label {
                text: chartWrapper.mouseDataPoint.x > 0
                      ? chartWrapper.mouseDataPoint.y.toFixed(1) + ' dB'
                      : '--- dB'
                Layout.preferredWidth: contentWidth
                font.pointSize: 9
                font.family: 'monospace'
                color: window.accent
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: Qt.AlignLeft
            }

            Item { Layout.preferredWidth: 12 }

            // V18: rule of the chosen country at the cursor
            Label {
                id: regLabel
                property var r: {
                    var rr = chartWrapper.regRegions, f = chartWrapper.mouseDataPoint.x;
                    if (!(f > 0) || !rr) return ({});
                    for (var i = 0; i < rr.length; i++)
                        if (f >= rr[i].lo && f < rr[i].hi) return rr[i];
                    return ({});
                }
                visible: bandSettings.showRegulation && r.label !== undefined
                text: visible ? bandSettings.country + ': ' + r.label : ''
                color: visible ? r.color : palette.windowText
                font.pointSize: 9
                ToolTip.visible: regMouse.containsMouse && visible
                ToolTip.text: visible ? r.lo.toFixed(1) + '-' + r.hi.toFixed(1) + ' MHz: ' + r.label
                                        + (r.note ? '\n' + r.note : '') : ''
                MouseArea { id: regMouse; anchors.fill: parent; hoverEnabled: true }
            }
            Item { Layout.preferredWidth: 12 }

            // V16: last warning / error / info of the scanner (click clears)
            Label {
                id: statusLine
                Layout.fillWidth: true
                text: statusHub.text
                elide: Text.ElideRight
                font.pointSize: 9
                color: statusHub.level == 'error' ? '#ff6b6b'
                     : statusHub.level == 'warning' ? '#f0b44c' : palette.windowText
                verticalAlignment: Text.AlignVCenter
                ToolTip.visible: statusMouse.containsMouse && text.length > 0
                ToolTip.text: text + (statusHub.logFile ? '\nLog: ' + statusHub.logFile : '')
                MouseArea {
                    id: statusMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: statusHub.clear()
                }
            }

        }
    }

    StackView {
        anchors.fill: parent
        Graph {
            id: chartWrapper
            anchors.fill: parent
            chartTheme: themeSelect.theme
            darkUi: window.darkUi
            accent: window.accent
            tvPlan: themeSelect.tvPlan
            showSpurs: themeSelect.showSpurs
            showRegulation: bandSettings.showRegulation
            showLossBands: uiSettings.showLossBands
            showImageMarks: uiSettings.showImageMarks
            regRegions: bandsData.regions(bandSettings.country)
        }
    }

    ImportDialog {
        id: importDialog
        onImportFile: {
            chartWrapper.loadFromFile(fileName);
        }
    }

    ExportDialog {
        id: exportDialog
    }

    DeviceConfigPopup {
        id: device_config
    }

    ScanControlsDialog {
        id: scanControlsDialog
        scanner: scanner
        config: scanConfig
        onGainSuggested: device_config.setGain(gain)     // V20.6
    }

    ThemeSelectPopup {
        id: themeSelect
        darkWindow: uiSettings.darkWindow
        onDarkWindowChanged: uiSettings.darkWindow = darkWindow
    }

    ScanConfig {
        id: scanConfig
    }

    // V18: systems and country rules
    Settings {
        id: bandSettings
        category: 'Bands'
        property string country: 'PL'
        property string selected: '[]'
        property bool showRegulation: true
    }
    BandsData {
        id: bandsData
    }
    BandsDialog {
        id: bandsDialog
        bands: bandsData
        config: scanConfig
        settings: bandSettings
    }

    StatusHub {
        id: statusHub
        onMessage: {
            if (level == 'error') {
                errorText.text = text;
                if (!errorDialog.opened)
                    errorDialog.open();
            }
        }
    }

    // V20.4: true when the selected graph is the scan that is still running
    function exportOfRunningScan() {
        var s = chartWrapper.activeSpectrum;
        return !!(s && s.scanner && s.scanner.running);
    }

    Dialog {
        id: exportUnfinishedDialog
        objectName: 'exportUnfinishedDialog'
        title: 'Scan not finished'
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(560, window.width - 40)
        standardButtons: Dialog.Yes | Dialog.Cancel
        Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: 'The scan is still running: the file would hold only the part measured so far. ' +
                  'Wait until the scan ends, or export this part anyway?'
        }
        onAccepted: {
            console.log('export: unfinished scan exported on request');
            exportDialog.graphData = chartWrapper.activeSpectrum;
            exportDialog.open();
        }
    }

    Dialog {
        id: errorDialog
        title: 'Problem'
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(560, window.width - 40)
        standardButtons: Dialog.Ok
        Label {
            id: errorText
            width: parent.width
            wrapMode: Text.Wrap
        }
    }

    ScannerInterface {
        id: scanner
        scanConfig: scanConfig.model
        deviceInfo: device_config.device ? device_config.device: null
        gain: device_config.gain
        sampleRate: device_config.sampleRate
        onScannerRunState: {
            scanControls.scanRunning = scanner.running;
        }
    }
}
