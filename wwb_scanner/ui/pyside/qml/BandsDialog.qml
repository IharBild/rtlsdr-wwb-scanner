import QtQuick 2.12
import QtQuick.Layouts 1.11
import QtQuick.Controls 2.12

// V18 "Bands": choose your systems (Shure, Sennheiser) and the country;
// the next scan covers only the bands of these systems, and every band is
// marked with what the country allows there.
Dialog {
    id: root
    title: 'Bands: your systems and the country'
    modal: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(940, Overlay.overlay ? Overlay.overlay.width - 40 : 940)
    height: Math.min(660, Overlay.overlay ? Overlay.overlay.height - 40 : 660)
    padding: 10

    property var bands
    property var config
    property var settings
    property var countries: []
    property var legendRows: []
    property var chosen: ({})
    property var info: ({})
    property string filterText: ''
    readonly property int rowCount: sysModel.count

    function chosenIds() {
        var out = [];
        for (var k in chosen)
            if (chosen[k]) out.push(k);
        return out;
    }
    function load() {
        countries = bands.countries();
        legendRows = bands.legend();
        var idx = 0;
        for (var i = 0; i < countries.length; i++)
            if (countries[i].code == settings.country) idx = i;
        countryCombo.currentIndex = idx;
        var c = {};
        try {
            var ids = JSON.parse(settings.selected);
            for (var j = 0; j < ids.length; j++) c[ids[j]] = true;
        } catch (e) { }
        chosen = c;
        fill();
    }
    function fill() {
        var rows = bands.systems(settings.country), f = filterText.toLowerCase();
        sysModel.clear();
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            var hay = (r.section + ' ' + r.band + ' ' + r.ranges).toLowerCase();
            if (f && hay.indexOf(f) < 0 && !chosen[r.id]) continue;
            r.on = chosen[r.id] ? true : false;
            sysModel.append(r);
        }
        refresh();
    }
    function refresh() {
        info = bands.summary(settings.country, JSON.stringify(chosenIds()));
    }
    function setChosen(id, on) {
        var c = chosen;
        c[id] = on;
        chosen = c;
        for (var i = 0; i < sysModel.count; i++)
            if (sysModel.get(i).id == id) sysModel.setProperty(i, 'on', on);
        settings.selected = JSON.stringify(chosenIds());
        refresh();
    }
    function timeText(s) {
        s = Math.round(s || 0);
        return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
    }

    onOpened: load()

    ListModel { id: sysModel }

    contentItem: ColumnLayout {
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            Label { text: 'Country:' }
            ComboBox {
                id: countryCombo
                Layout.preferredWidth: 230
                model: root.countries
                textRole: 'text'
                onActivated: {
                    root.settings.country = root.countries[index].code;
                    root.fill();
                }
            }
            Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                font.pointSize: 8
                opacity: 0.8
                text: (root.info.regulator ? 'Regulator: ' + root.info.regulator + '.  ' : '')
                      + 'Source: ' + (root.info.source || '')
                      + (root.info.note ? '.  ' + root.info.note : '')
            }
        }

        RowLayout {
            Layout.fillWidth: true
            TextField {
                id: filterField
                Layout.preferredWidth: 260
                placeholderText: 'Filter: QLX, PSM 1000, G51, ew 300...'
                selectByMouse: true
                onTextChanged: { root.filterText = text; root.fill(); }
            }
            Label {
                text: root.chosenIds().length + ' band(s) chosen'
                Layout.leftMargin: 8
            }
            Item { Layout.fillWidth: true }
            Button {
                text: 'Clear'
                onClicked: {
                    root.chosen = ({});
                    root.settings.selected = '[]';
                    root.fill();
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            Frame {
                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 2
                ListView {
                    id: sysList
                    anchors.fill: parent
                    clip: true
                    model: sysModel
                    ScrollBar.vertical: ScrollBar { }
                    section.property: 'section'
                    section.delegate: Label {
                        text: section
                        font.bold: true
                        topPadding: 6
                        leftPadding: 4
                        bottomPadding: 2
                    }
                    delegate: ItemDelegate {
                        width: sysList.width - 12
                        height: 26
                        padding: 0
                        onClicked: root.setChosen(model.id, !model.on)
                        ToolTip.visible: hovered && model.detail.length > 0
                        ToolTip.delay: 400
                        ToolTip.text: model.detail
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            spacing: 6
                            BoxCheck {
                                checked: model.on
                                onClicked: root.setChosen(model.id, checked)
                            }
                            Label {
                                text: model.band
                                Layout.preferredWidth: 70
                                font.bold: model.on
                            }
                            Label {
                                text: model.ranges + ' MHz'
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Rectangle {
                                Layout.preferredWidth: 92
                                Layout.preferredHeight: 16
                                radius: 3
                                color: model.color
                                Label {
                                    anchors.centerIn: parent
                                    text: model.label
                                    color: '#ffffff'
                                    font.pointSize: 7
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.preferredWidth: 360
                Layout.maximumWidth: 360
                Layout.fillHeight: true
                Label {
                    text: root.info.count > 0
                          ? 'Scan plan: ' + root.info.count + ' range(s), about ' + root.timeText(root.info.seconds)
                            + (root.info.count > 1 ? ' (one range ' + root.timeText(root.info.fullSeconds) + ')' : '')
                          : 'Tick the bands of the systems you bring.'
                    font.bold: true
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        readOnly: true
                        selectByMouse: true
                        wrapMode: Text.Wrap
                        font.pointSize: 8
                        text: root.info.text || ''
                    }
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    font.pointSize: 8
                    opacity: 0.8
                    text: 'A guide for planning (data ' + '2026-11' + '), not a legal document: '
                          + 'check with the regulator before the show.'
                }
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 10
            Repeater {
                model: root.legendRows
                Row {
                    spacing: 4
                    Rectangle { width: 12; height: 12; radius: 2; color: modelData.color; anchors.verticalCenter: parent.verticalCenter }
                    Label {
                        text: modelData.label
                        font.pointSize: 8
                        ToolTip.visible: legendMouse.containsMouse
                        ToolTip.text: modelData.text
                        MouseArea { id: legendMouse; anchors.fill: parent; hoverEnabled: true }
                    }
                }
            }
        }
    }

    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        Button {
            id: scanBandsButton
            objectName: 'scanBandsButton'
            text: root.info.count > 1 ? 'Scan these ' + root.info.count + ' ranges' : 'Scan this range'
            enabled: root.info.count > 0
            highlighted: true
            Layout.bottomMargin: 8
            onClicked: root.accept()
        }
        Button {
            text: 'Close'
            Layout.rightMargin: 10
            Layout.bottomMargin: 8
            onClicked: root.reject()
        }
    }

    onAccepted: {
        if (!(root.info.count > 0) || !root.config)
            return;
        root.config.scanRanges = root.info.count > 1 ? root.info.segments : '';
        root.config.startFreq = root.info.lo;
        root.config.endFreq = root.info.hi;
    }
}
