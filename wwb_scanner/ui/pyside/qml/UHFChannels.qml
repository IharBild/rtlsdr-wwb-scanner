import QtQuick 2.0
import QtCharts 2.13

Item {
    id: root

    property ChartView chart
    property ValueAxis axisX
    property real min: axisX.min
    property real max: axisX.max
    property var channels: []
    property var channelsByCenterFreq: ({})
    property bool channelsBuilt: false
    // 'EU': CEPT ch 21-69, 8 MHz (474 MHz = ch 21); 'US': ch 14-83, 6 MHz; 'off'
    property string plan: 'EU'
    property bool darkUi: true
    onPlanChanged: { rebuildChannels() }

    property real freqScalar: 1 / freqRangeSize
    property real freqRangeSize: max - min
    signal geometryUpdate()
    signal rangeUpdate()


    onAxisXChanged: {
        var obj;
        if (axisX){
            for (var i=0;i<channels.length;i++){
                obj = channels[i];
                obj.axisX = axisX;
            }
        }
    }

    onXChanged: { geometryUpdate() }
    onYChanged: { geometryUpdate() }
    onWidthChanged: { geometryUpdate() }
    onHeightChanged: { geometryUpdate() }

    onMinChanged: { Qt.callLater(rangeUpdate) }
    onMaxChanged: { Qt.callLater(rangeUpdate) }
    onFreqScalarChanged: { Qt.callLater(rangeUpdate) }
    onFreqRangeSizeChanged: { Qt.callLater(rangeUpdate) }

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

    function rebuildChannels(){
        for (var i=0;i<root.channels.length;i++){
            root.channels[i].destroy();
        }
        root.channels = [];
        root.channelsByCenterFreq = {};
        root.channelsBuilt = false;
        buildChannels();
    }

    function buildChannels(){
        if (root.channelsBuilt || root.plan == 'off'){
            return;
        }
        var channel, last, bandwidth, centerFreq, props;
        if (root.plan == 'US'){
            channel = 14; last = 83; bandwidth = 6.0; centerFreq = 473.0;
        } else {
            channel = 21; last = 69; bandwidth = 8.0; centerFreq = 474.0;
        }
        while (channel <= last){
            props = {
                'channel':channel, 'bandwidth':bandwidth, 'centerFreq':centerFreq,
                'parentChannels':root, 'axisX':root.axisX,
            };
            buildComponent(root, 'UHFChannel.qml', props, function(obj){
                root.channels.push(obj);
                root.channelsByCenterFreq[obj.centerFreq] = obj;
            });
            channel += 1;
            centerFreq += bandwidth;
        }
        root.channelsBuilt = true;
    }
    Component.onCompleted: {
        buildChannels();
    }
}
