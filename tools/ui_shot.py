"""Screenshot of the real window (for UI work): python tools/ui_shot.py out.png [scan.csv]
Run under Xvfb (xvfb-run). Uses the installed/importable wwb_scanner and its QML."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QT_QUICK_CONTROLS_STYLE', 'Fusion')
import qa_ui                                   # portable QML copy + fake dongle
from PySide2 import QtCore, QtQml
from PySide2.QtWidgets import QApplication
from wwb_scanner.ui.pyside import main as ui_main


def main():
    out = sys.argv[1]
    csv = sys.argv[2] if len(sys.argv) > 2 else None
    if hasattr(ui_main, 'apply_window_theme'):
        ui_main.apply_window_theme(None)
    app = QApplication(sys.argv[:1])
    app.setOrganizationName('rtlsdr-wwb-scanner-shot')
    app.setApplicationName('wwb_scanner')
    if hasattr(ui_main, 'apply_window_theme'):
        ui_main.apply_window_theme(app)
    qml_dir = qa_ui.portable_qml_copy()
    engine = QtQml.QQmlApplicationEngine()
    engine.setBaseUrl(str(qml_dir)); engine.addImportPath(str(qml_dir))
    ui_main.register_qml_types()
    engine.load(str(qml_dir / 'main.qml'))
    win = engine.rootObjects()[0]
    win.setWidth(1600); win.setHeight(900); win.show()
    if os.environ.get('SHOT_LIGHT'):
        win.setProperty('darkUi', False)
    if os.environ.get('SHOT_THEME_DIALOG'):
        d = [o for o in win.findChildren(QtCore.QObject) if o.property('title') == 'Theme']
        if d:
            QtCore.QMetaObject.invokeMethod(d[0], 'open')
    qa_ui.pump(app, 1.0)
    if csv:
        g = qa_ui.find(win, lambda o: o.metaObject().indexOfSignal('loadFromFile(QUrl)') >= 0)
        g[0].loadFromFile.emit(QtCore.QUrl.fromLocalFile(csv))
        qa_ui.pump(app, 3.0)
    zoom = os.environ.get('SHOT_ZOOM')       # e.g. "470,800"
    if zoom:
        lo, hi = [float(x) for x in zoom.split(',')]
        objs = win.findChildren(QtCore.QObject)
        ax = [o for o in objs if o.metaObject().indexOfProperty('labelFormat') >= 0
              and 'MHz' in str(o.property('labelFormat'))]
        print('axes', len(ax))
        for a in ax:
            a.setProperty('min', lo); a.setProperty('max', hi)
        qa_ui.pump(app, 1.5)
    if os.environ.get('SHOT_STATUS'):
        import logging
        logging.getLogger('wwb_scanner.status').info(os.environ['SHOT_STATUS'])
        qa_ui.pump(app, 0.5)
    img = win.grabWindow()
    print('saved', out, img.save(out), img.width(), img.height())


if __name__ == '__main__':
    main()
