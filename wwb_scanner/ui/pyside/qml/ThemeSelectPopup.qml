import QtQuick 2.0
import QtQuick.Layouts 1.11
import QtQuick.Controls 2.12
import Qt.labs.settings 1.0
import QtCharts 2.13


Dialog {
    id: root
    title: 'Theme'
    modal: true
    anchors.centerIn: Overlay.overlay
    property alias theme: settings.chartThemeV15
    property bool darkWindow: true
    property alias tvPlan: settings.tvPlan
    property alias showSpurs: settings.showSpurs

    Settings {
        id: settings
        category: 'User Interface'
        // new key in V15: 100 = Studio (flat, matched to the window)
        property int chartThemeV15: 100
        property string tvPlan: 'EU'
        property bool showSpurs: true
    }

    ColumnLayout {
    Switch {
        id: darkSwitch
        text: 'Dark window'
        checked: root.darkWindow
        onToggled: root.darkWindow = checked
    }
    Label { text: 'TV channels on the graph' }
    ComboBox {
        id: tvCombo
        Layout.preferredWidth: 220
        model: ['Europe (8 MHz, ch 21-69)', 'USA (6 MHz, ch 14-83)', 'Off']
        property var values: ['EU', 'US', 'off']
        currentIndex: Math.max(0, values.indexOf(root.tvPlan))
        onActivated: root.tvPlan = values[index]
    }
    CheckBox {
        text: "Mark the dongle's own spurs (k x 14.4 MHz)"
        checked: root.showSpurs
        onToggled: root.showSpurs = checked
    }
    Label { text: 'Graph' }
    ComboBox {
        id: combo
        Layout.preferredWidth: 220
        textRole: 'text'
        model: ListModel {
            ListElement { text:'Studio (matches window)'; value:100 }
            ListElement { text:'Light'; value:ChartView.ChartThemeLight }
            ListElement { text:'Cerulean Blue'; value:ChartView.ChartThemeBlueCerulean }
            ListElement { text:'Dark'; value:ChartView.ChartThemeDark }
            ListElement { text:'Brown Sand'; value:ChartView.ChartThemeBrownSand }
            ListElement { text:'Natural (NCS) Blue'; value:ChartView.ChartThemeBlueNcs }
            ListElement { text:'High Contrast'; value:ChartView.ChartThemeHighContrast }
            ListElement { text:'Icy Blue'; value:ChartView.ChartThemeBlueIcy }
            ListElement { text:'Qt'; value:ChartView.ChartThemeQt }
        }

        function getSelectedValue(){
            var idx = combo.currentIndex,
                item;
            if (idx == -1){
                return root.theme;
            }
            item = model.get(idx);
            return item.value;
        }
        function setSelectedValue(value){
            var item;
            for (var i=0;i<model.count;i++){
                item = model.get(i);
                if (item.value == value){
                    combo.currentIndex = i;
                }
            }
        }
    }
    }


    standardButtons: Dialog.Ok | Dialog.Cancel

    onAccepted:{
        root.theme = combo.getSelectedValue();
        root.close();
    }
    onRejected:{
        combo.setSelectedValue(root.theme);
        root.close();
    }

    Component.onCompleted: {
        combo.setSelectedValue(root.theme);
    }
}
