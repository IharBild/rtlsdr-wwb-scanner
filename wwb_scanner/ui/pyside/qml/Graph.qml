import QtQuick 2.0
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.11
import Qt.labs.settings 1.0
import QtCharts 2.13
import GraphUtils 1.0

Item {
    id: root
    anchors.fill: parent

    property alias mouseDataPoint: crosshair.dataValue
    // chartTheme: one of ChartView.ChartTheme*, or STUDIO_THEME (flat dark
    // plot matched to the window, default since V15)
    readonly property int studioTheme: 100
    property int chartTheme: studioTheme
    property bool darkUi: true
    property color accent: "#3aa0ff"
    property string tvPlan: 'EU'
    property bool showSpurs: true
    // own spurs of the RTL-SDR Blog V4 (multiples of its 28.8 MHz crystal),
    // measured without antenna: 3 dB or more above its noise (V10-V15)
    readonly property var spurFreqs: [86.4, 115.2, 144.0, 172.8, 201.6, 230.4, 259.2, 288.0,
                                      316.8, 345.6, 374.4, 403.2, 432.0, 460.8, 489.6, 518.4]
    // V18: what the regulator allows (strip at the bottom of the plot)
    property var regRegions: []
    property bool showRegulation: true
    property bool showLossBands: true     // V20.3: View > Lost sensitivity bands
    property bool showImageMarks: true    // V20.3: View > Removed image marks
    property alias theme: chart.theme
    property var spectrumGraphs: []
    property var activeSpectrum
    property alias activeSeries: chart.activeSeries
    signal newLiveScan(var scanner)
    signal loadFromFile(url fileName)
    signal updateAxisExtents()
    signal seriesClicked(int index)

    function setActiveSpectrum(spectrum){
        root.activeSpectrum = spectrum;
        root.activeSeries = spectrum.series;
    }

    onNewLiveScan: {
        var scannerArg = scanner;
        var spectrumGraph
        addModel({'scanner':scannerArg, 'isLive':true});
    }

    onSeriesClicked: {
        var spectrumGraph = root.spectrumGraphs[index];
        if (spectrumGraph)
            setActiveSpectrum(spectrumGraph);
    }

    // V20.7: delete a scan from the graph (not a running one). Its slot in
    // spectrumGraphs stays (null): the other graphs keep their index.
    function removeSpectrum(spectrum){
        if (!spectrum || (spectrum.scanner && spectrum.scanner.running))
            return false;
        var index = spectrum.index;
        if (root.spectrumGraphs[index] !== spectrum)
            return false;
        root.spectrumGraphs[index] = null;
        chartSelect.removeItem(index);
        if (spectrum.series)
            chart.removeSeries(spectrum.series);
        if (root.activeSpectrum === spectrum) {
            var next = null;
            for (var i = root.spectrumGraphs.length - 1; i >= 0; i--) {
                if (root.spectrumGraphs[i]) { next = root.spectrumGraphs[i]; break; }
            }
            root.activeSpectrum = next;
            root.activeSeries = next ? next.series : null;
        }
        spectrum.destroy();
        updateAxisExtents();
        return true;
    }

    onUpdateAxisExtents: {
        var spectrum, minValue = null, maxValue = null;
        for (var i=0;i<root.spectrumGraphs.length;i++){
            spectrum = root.spectrumGraphs[i];
            if (!spectrum || (spectrum.minValue.x == 0 && spectrum.minValue.y == 0)){
                continue;
            }
            if (minValue == null){
                minValue = Qt.point(spectrum.minValue.x, spectrum.minValue.y);
            } else {
                if (spectrum.minValue.x < minValue.x){
                    minValue.x = spectrum.minValue.x;
                }
                if (spectrum.minValue.y < minValue.y){
                    minValue.y = spectrum.minValue.y;
                }
            }
            if (maxValue == null){
                maxValue = Qt.point(spectrum.maxValue.x, spectrum.maxValue.y);
            } else {
                if (spectrum.maxValue.x > maxValue.x){
                    maxValue.x = spectrum.maxValue.x;
                }
                if (spectrum.maxValue.y > maxValue.y){
                    maxValue.y = spectrum.maxValue.y;
                }
            }
        }
        if (minValue == null || maxValue == null){
            return;
        }
        viewController.setDataExtents(minValue, maxValue);
    }

    onLoadFromFile: {
        var spectrum = addModel({}, function(obj){
            obj.load_from_file(fileName);
        });
    }

    function callable(obj){
        if (typeof(obj) == 'function'){
            return true;
        }
        return false;
    }

    function buildComponent(prnt, uri, props, callback){
        var component = Qt.createComponent(uri),
            obj;
        function onComponentReady(){
            if (component.status == Component.Ready){
                obj = component.createObject(prnt, props);
                if (callable(callback)){
                    callback(obj);
                }
            } else {
                console.error('Error creating object: ', component.errorString());
            }
        }
        if (component.status == Component.Ready){
            obj = component.createObject(prnt, props);
            if (callable(callback)){
                callback(obj);
            }
        } else if (component.status == Component.Error){
            console.error('Error creating object: ', component.errorString());
        } else {
            component.statusChanged.connect(onComponentReady);
        }
    }

    function addModel(props, callback){
        var series = chart.addMappedSeries(),
            qmlFile = 'SpectrumGraph.qml';

        if (props.isLive){
            qmlFile = 'LiveSpectrumGraph.qml';
        }
        props['series'] = series;
        props['graphParent'] = root;
        buildComponent(root, qmlFile, props, function(obj){
            obj.index = root.spectrumGraphs.length;
            root.spectrumGraphs.push(obj);
            root.activeSpectrum = obj;
            updateAxisExtents();
            obj.axisExtentsUpdate.connect(updateAxisExtents);
            obj.seriesClicked.connect(root.seriesClicked);
            chartSelect.addItem(obj);
            if (callable(callback)){
                callback(obj);
            }
        });
    }

    GraphViewController {
        id: viewController
        axisX: axisX
        axisY: axisY
    }

    GraphHViewControls {
        id: hViewControls
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height * .1
        controller: viewController
    }

    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: hViewControls.bottom
        anchors.bottom: parent.bottom
        spacing: 1
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            ChartView {
                id: chart
                anchors.fill: parent
                antialiasing: true
                legend.visible: false
                // No series animation: the graph data is replaced as a whole
                // (fast update, V10), and an animation then replays the
                // whole line from the left edge on every update (flicker).
                animationOptions: ChartView.NoAnimation
                property var activeSeries
                theme: root.chartTheme == root.studioTheme
                       ? (root.darkUi ? ChartView.ChartThemeDark : ChartView.ChartThemeLight)
                       : root.chartTheme
                margins { top: 4; bottom: 4; left: 4; right: 8 }

                // Studio look: applied after the Qt chart theme (a theme
                // change resets all colours)
                function applyStudio() {
                    if (root.chartTheme != root.studioTheme)
                        return;
                    var d = root.darkUi;
                    chart.backgroundColor = d ? "#16181b" : "#efefef";
                    chart.backgroundRoundness = 0;
                    chart.dropShadowEnabled = false;
                    chart.plotAreaColor = d ? "#0d0f11" : "#ffffff";
                    var axes = [axisX, axisY];
                    for (var i = 0; i < axes.length; i++) {
                        var a = axes[i];
                        a.gridLineColor = d ? "#24282d" : "#e2e2e2";
                        a.minorGridLineColor = d ? "#1a1d21" : "#f0f0f0";
                        a.color = d ? "#3a4047" : "#9a9a9a";
                        a.labelsColor = d ? "#8a919b" : "#555555";
                        a.labelsFont = Qt.font({pointSize: 8});
                    }
                }
                Component.onCompleted: Qt.callLater(applyStudio)
                Connections {
                    target: root
                    function onDarkUiChanged() { Qt.callLater(chart.applyStudio); }
                    function onChartThemeChanged() { Qt.callLater(chart.applyStudio); }
                }

                // Round tick values (1/2/5 x 10^n) at any zoom, like Smaart/OSM
                function niceStep(span, n) {
                    var raw = span / n;
                    if (!(raw > 0)) return 1;
                    var p = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
                    var m = raw / p;
                    return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
                }
                ValueAxis {
                    id: axisX
                    min: 100
                    max: 900
                    tickType: ValueAxis.TicksDynamic
                    tickAnchor: 0
                    tickInterval: chart.niceStep(max - min, 10)
                    labelFormat: tickInterval >= 1 ? "%.0f MHz" : (tickInterval >= 0.1 ? "%.1f MHz" : "%.2f MHz")
                }
                ValueAxis {
                    id: axisY
                    min: -160
                    max: 0
                    tickType: ValueAxis.TicksDynamic
                    tickAnchor: 0
                    tickInterval: chart.niceStep(max - min, 8)
                    labelFormat: "%.0f dB"
                }

                function addMappedSeries(){
                    var series = chart.createSeries(ChartView.SeriesTypeLine, 'foo', axisX, axisY);
                    chart.activeSeries = series;
                    return series;
                }

            }

            UHFChannels {
                id: channelLabels
                axisX: axisX
                chart: chart
                plan: root.tvPlan
                darkUi: root.darkUi
                x: chart.plotArea.x
                y: chart.plotArea.y
                width: chart.plotArea.width
                height: 18
            }

            // V18: regulation of the chosen country: faint colour over the
            // plot and a labelled strip at its bottom
            Item {
                id: regStrip
                x: chart.plotArea.x
                y: chart.plotArea.y
                width: chart.plotArea.width
                height: chart.plotArea.height
                visible: root.showRegulation && root.regRegions.length > 0
                clip: true
                Repeater {
                    model: root.regRegions
                    Item {
                        property real span: axisX.max - axisX.min
                        property real x0: span > 0 ? (modelData.lo - axisX.min) / span * regStrip.width : 0
                        property real x1: span > 0 ? (modelData.hi - axisX.min) / span * regStrip.width : 0
                        visible: x1 > 0 && x0 < regStrip.width && modelData.status != 'unknown'
                        x: x0
                        width: Math.max(1, x1 - x0)
                        height: regStrip.height
                        Rectangle {
                            anchors.fill: parent
                            color: modelData.color
                            opacity: modelData.status == 'no' ? 0.10 : 0.05
                        }
                        Rectangle {
                            id: regBar
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 14
                            color: modelData.color
                            opacity: 0.75
                            border.color: root.darkUi ? '#0d0f11' : '#ffffff'
                            border.width: 1
                        }
                        Text {
                            anchors.centerIn: regBar
                            visible: parent.width > implicitWidth + 8
                            text: modelData.label
                            color: '#ffffff'
                            font.pointSize: 7
                        }
                    }
                }
            }

            // V20.2: bands where a strong signal next to the antenna took
            // sensitivity away (only "not above this level" is known there),
            // and removed images of strong signals
            Item {
                id: lossMarks
                x: chart.plotArea.x
                y: chart.plotArea.y
                width: chart.plotArea.width
                height: chart.plotArea.height
                clip: true
                property var bands: root.showLossBands && root.activeSpectrum && root.activeSpectrum.lossBands ? root.activeSpectrum.lossBands : []
                property var images: root.showImageMarks && root.activeSpectrum && root.activeSpectrum.imageMarks ? root.activeSpectrum.imageMarks : []
                property var unmeasurable: root.showLossBands && root.activeSpectrum && root.activeSpectrum.unmeasurableBands ? root.activeSpectrum.unmeasurableBands : []
                // V20.4: labels below the TV channel numbers
                property real labelTop: channelLabels.visible ? channelLabels.height + 4 : 4
                Repeater {
                    objectName: 'lossBandRepeater'
                    model: lossMarks.bands
                    Item {
                        property real span: axisX.max - axisX.min
                        property real x0: span > 0 ? (modelData.lo - axisX.min) / span * lossMarks.width : 0
                        property real x1: span > 0 ? (modelData.hi - axisX.min) / span * lossMarks.width : 0
                        visible: x1 > 0 && x0 < lossMarks.width
                        x: x0
                        width: Math.max(2, x1 - x0)
                        height: lossMarks.height
                        Rectangle {
                            anchors.fill: parent
                            color: '#e0a030'
                            opacity: 0.13
                        }
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            height: 2
                            color: '#e0a030'
                            opacity: 0.8
                        }
                        Text {
                            anchors.top: parent.top
                            anchors.topMargin: lossMarks.labelTop
                            anchors.horizontalCenter: parent.horizontalCenter
                            visible: parent.width > implicitWidth + 4
                            text: '-' + modelData.db.toFixed(0) + ' dB sens.'
                            color: '#e0a030'
                            font.pointSize: 7
                        }
                    }
                }
                // V20.4: the dongle clipped here even at the lowest gain
                Repeater {
                    objectName: 'unmeasurableRepeater'
                    model: lossMarks.unmeasurable
                    Item {
                        property real span: axisX.max - axisX.min
                        property real x0: span > 0 ? (modelData.lo - axisX.min) / span * lossMarks.width : 0
                        property real x1: span > 0 ? (modelData.hi - axisX.min) / span * lossMarks.width : 0
                        visible: x1 > 0 && x0 < lossMarks.width
                        x: x0
                        width: Math.max(2, x1 - x0)
                        height: lossMarks.height
                        Rectangle {
                            anchors.fill: parent
                            color: '#d84040'
                            opacity: 0.12
                        }
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.topMargin: 2
                            height: 2
                            color: '#d84040'
                            opacity: 0.9
                        }
                        Text {
                            anchors.top: parent.top
                            anchors.topMargin: lossMarks.labelTop + 12
                            anchors.horizontalCenter: parent.horizontalCenter
                            visible: parent.width > implicitWidth + 4
                            text: 'not measurable'
                            color: '#e06060'
                            font.pointSize: 7
                        }
                    }
                }
                Repeater {
                    objectName: 'imageMarkRepeater'
                    model: lossMarks.images
                    Item {
                        property real span: axisX.max - axisX.min
                        visible: span > 0 && modelData.f >= axisX.min && modelData.f <= axisX.max
                        x: span > 0 ? (modelData.f - axisX.min) / span * lossMarks.width : 0
                        width: 1
                        height: lossMarks.height
                        Rectangle {
                            width: 1
                            height: parent.height
                            color: '#9a7cd8'
                            opacity: 0.45
                        }
                        Text {
                            visible: parent.span < 100
                            text: 'image of ' + modelData.of.toFixed(3)
                            color: '#9a7cd8'
                            opacity: 0.9
                            font.pointSize: 7
                            rotation: -90
                            transformOrigin: Item.TopLeft
                            x: 2
                            y: parent.height - 4
                        }
                    }
                }
            }

            // V16: marks of the dongle's own spurs (not in the data, only drawn)
            Item {
                id: spurMarks
                x: chart.plotArea.x
                y: chart.plotArea.y
                width: chart.plotArea.width
                height: chart.plotArea.height
                visible: root.showSpurs
                clip: true
                Repeater {
                    model: root.spurFreqs
                    Item {
                        property real f: modelData
                        property real span: axisX.max - axisX.min
                        visible: span > 0 && f >= axisX.min && f <= axisX.max
                        x: span > 0 ? (f - axisX.min) / span * spurMarks.width : 0
                        width: 1
                        height: spurMarks.height
                        Rectangle {
                            width: 1
                            height: parent.height
                            color: '#c8913a'
                            opacity: 0.35
                        }
                        Text {
                            visible: parent.span < 600
                            text: 'spur ' + parent.f.toFixed(1)
                            color: '#c8913a'
                            opacity: 0.8
                            font.pointSize: 7
                            rotation: -90
                            transformOrigin: Item.TopLeft
                            x: 2
                            y: parent.height - 4
                        }
                    }
                }
            }

            Crosshair {
                id: crosshair
                x: chart.plotArea.x
                y: chart.plotArea.y
                width: chart.plotArea.width
                height: chart.plotArea.height
                enabled: false
            }


            MouseArea {
                id: mouseArea
                anchors.fill: parent
                hoverEnabled: true
                propagateComposedEvents: true

                property point dataPoint: Qt.point(0, 0)
                property point dataPos: Qt.point(0, 0)

                onPositionChanged: {
                    mouse.accepted = false;
                    if (!mouseArea.containsMouse){
                        return;
                    }
                    var pt = mouseArea.mapToGlobal(mouse.x, mouse.y);
                    timedMouse.setPoint(pt);
                }

                onClicked: { mouse.accepted = false }
                onPressed: { mouse.accepted = false }
                onReleased: { mouse.accepted = false }

                Timer {
                    id: timedMouse
                    interval: 1
                    repeat: false
                    property var point

                    function setPoint(pt) {
                        timedMouse.stop();
                        timedMouse.point = pt;
                        timedMouse.start();
                    }

                    onTriggered: {
                        var series = chart.activeSeries,
                            spectrum = root.activeSpectrum;
                        if (!series || !spectrum){
                            return;
                        }
                        var pos = chart.mapFromGlobal(timedMouse.point.x, timedMouse.point.y),
                            mouseDataPoint = chart.mapToValue(pos, series),
                            dataPoint = spectrum.get_nearest_by_x(mouseDataPoint.x),
                            dataPos;
                        if (dataPoint.x < 0) {
                            dataPos = dataPoint;
                        } else {
                            dataPos = chart.mapToPosition(dataPoint, series);
                            dataPos = chart.mapToItem(crosshair, dataPos.x, dataPos.y);
                        }
                        crosshair.setData(dataPos, dataPoint);
                    }
                }
            }
        }

        ChartSelect {
            id: chartSelect
            Layout.minimumWidth: 240
            Layout.fillHeight: true
            Layout.leftMargin: 0
            Layout.rightMargin: 5
            Layout.topMargin: 5
            activeSpectrum: root.activeSpectrum

            onSelected: {
                setActiveSpectrum(spectrum);
            }
            onRemoveRequested: root.removeSpectrum(spectrum)
        }
    }
}
