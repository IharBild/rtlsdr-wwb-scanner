"""QA plan group U: run the real Qt window offscreen with the dongle simulator.

Run with Python 3.8 + PySide2 5.15:
    QT_QPA_PLATFORM=offscreen python tools/qa_ui.py [start end]
Prints one line per check; exit code 1 if a check failed.
"""
import os
import json
import re
import sys
import time
import types
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tests')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
TMP = tempfile.mkdtemp(prefix='qa_ui_')
os.environ['HOME'] = TMP

import numpy as np
import sim


class UIFakeRtlSdr(sim.FakeRtlSdr):
    """pyrtlsdr RtlSdr as the window uses it (device list, tuner type)."""
    gain_values = sim.R828D_GAINS
    air = None
    def __init__(self, device_index=0, serial_number=None, device_serial=None, **kw):
        kw.setdefault('air', UIFakeRtlSdr.air or sim.Air(noise=0.03).tone(480.0e6, 0.02))
        # QA_USB_DELAY=1: like the real V4 on Windows (V7 report: tune 0.053 s, data 0.042 s)
        if os.environ.get('QA_USB_DELAY') == '1':
            kw.setdefault('tune_delay', 0.053)
            kw.setdefault('read_delay', 0.021)
        sim.FakeRtlSdr.__init__(self, serial_number=serial_number, **kw)
    @staticmethod
    def get_device_serial_addresses():
        return ['00000001']
    @staticmethod
    def get_device_index_by_serial(serial):
        return 0
    def get_tuner_type(self):
        return 6


fake_mod = types.ModuleType('rtlsdr')
fake_mod.RtlSdr = UIFakeRtlSdr
sys.modules['rtlsdr'] = fake_mod

from PySide2 import QtCore, QtQml           # noqa: E402
from PySide2.QtWidgets import QApplication  # noqa: E402

from wwb_scanner.ui.pyside import main as ui_main   # noqa: E402
from wwb_scanner.ui.pyside import scanner as ui_scanner, graph as ui_graph  # noqa: E402
from wwb_scanner.utils import dbstore      # noqa: E402

dbstore.DBStore.DB_PATH = os.path.join(TMP, 'db.json')
dbstore.DBStore.SCAN_DB_PATH = os.path.join(TMP, 'scan_db.json')

RESULTS = []
QML_WARNINGS = []


def check(name, ok, detail):
    RESULTS.append(ok)
    print('%s %-4s %s' % (name, 'OK' if ok else 'FAIL', detail), flush=True)


def handler(mode, context, message):
    if 'Implicitly defined onFoo' in message:
        return
    if mode == QtCore.QtFatalMsg:
        sys.stderr.write('QT FATAL: %s\n' % message)
    if mode in (QtCore.QtWarningMsg, QtCore.QtCriticalMsg, QtCore.QtFatalMsg):
        QML_WARNINGS.append(message)


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        time.sleep(0.005)


def find(root, pred):
    out = []
    for o in root.findChildren(QtCore.QObject):
        try:
            if pred(o):
                out.append(o)
        except Exception:
            pass
    return out


def has_method(sig):
    return lambda o: o.metaObject().indexOfMethod(QtCore.QMetaObject.normalizedSignature(sig).data().decode()) >= 0


def portable_qml_copy():
    """Copy of the QML folder where the file dialogs keep their Settings in a
    property instead of a child Item. On Linux (no native dialog) Qt's
    QML FileDialog has no default property and main.qml does not load;
    on Windows the native dialog accepts it. Finding for the Linux/macOS port."""
    import shutil, re
    from pathlib import Path
    dst = Path(TMP) / 'qml'
    shutil.copytree(str(ui_main.QML_PATH), str(dst))
    for name in ('ImportDialog.qml', 'ExportDialog.qml'):
        p = dst / name
        s = p.read_text()
        s = re.sub(r'\n    Item \{\n        Settings \{(.*?)\n        \}\n    \}',
                   lambda m: '\n    property Settings folderSettings: Settings {' + m.group(1).replace('\n    ', '\n') + '\n    }',
                   s, flags=re.S)
        p.write_text(s)
    return dst


def main():
    start = float(sys.argv[1]) if len(sys.argv) > 2 else 0.0
    end = float(sys.argv[2]) if len(sys.argv) > 2 else 1766.0
    app = QApplication(sys.argv[:1])
    app.setOrganizationName('rtlsdr-wwb-scanner-qa')
    app.setApplicationName('wwb_scanner')
    QtCore.qInstallMessageHandler(handler)
    qml_dir = portable_qml_copy()
    engine = QtQml.QQmlApplicationEngine()
    engine.setBaseUrl(str(qml_dir))
    engine.addImportPath(str(qml_dir))
    ui_main.register_qml_types()
    t0 = time.time()
    engine.load(str(qml_dir / 'main.qml'))
    roots = engine.rootObjects()
    check('U1', bool(roots) and not QML_WARNINGS, 'window loaded in %.2f s, QML warnings: %s'
          % (time.time() - t0, QML_WARNINGS[:3]))
    if not roots:
        return 1
    win = roots[0]
    win.show()
    pump(app, 1.0)

    scanners = find(win, lambda o: isinstance(o, ui_scanner.ScannerInterface))
    controls = find(win, has_method('scannerState(bool)'))
    configs = find(win, lambda o: isinstance(o, ui_scanner.ScanConfigData))
    check('U1b', bool(scanners and controls and configs), 'found scanner %d, controls %d, config %d'
          % (len(scanners), len(controls), len(configs)))
    si = scanners[0]
    cfg = si.scanConfig
    cfg.startFreq = start
    cfg.endFreq = end
    cfg.windowType = 'hann'
    cfg.windowSize = 96
    cfg.samplesPerSweep = 2048
    cfg.sweepsPerScan = 40
    cfg.sweepOverlapRatio = 0.65
    si.sampleRate = 2400.
    pump(app, 0.3)

    # U2/U3: press Start (same signal the button emits), measure the GUI
    gaps = []
    t_start = time.time()
    controls[0].scannerState.emit(True)
    last = time.time()
    finished_at = None
    while time.time() - t_start < 900:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        now = time.time()
        gaps.append(now - last)
        last = now
        if not si.running and now - t_start > 1:
            finished_at = now
            break
        time.sleep(0.002)
    dt = (finished_at or time.time()) - t_start
    gaps = np.array(gaps)
    spec = si.spectrum
    npts = spec.sample_data.size if spec is not None else 0
    check('U2', finished_at is not None, 'scan %.0f-%.0f MHz in window: %.1f s, %d points' % (start, end, dt, npts))
    check('U3', gaps.max() < 5.0, 'longest GUI freeze %.2f s (99th pct %.3f s), >0.5 s freezes: %d'
          % (gaps.max(), np.percentile(gaps, 99), (gaps > 0.5).sum()))
    pump(app, 2.0)
    from PySide2.QtCharts import QtCharts
    gds = find(win, lambda o: isinstance(o, ui_graph.SpectrumGraphData))
    counts = [g._series.count() for g in gds if getattr(g, '_series', None) is not None and hasattr(g._series, 'count')]
    expect = ui_graph.decimate_max(np.zeros(npts, ui_graph.GRAPH_DTYPE), ui_graph.group_size_for((end - start) / 0.025)).size
    check('U7', bool(counts) and max(counts) == expect, 'chart series points %s (expected %d)' % (counts, expect))
    # U8: no series animation (with replace() it redrew the line from the left on every update)
    charts = find(win, lambda o: o.metaObject().indexOfProperty('animationOptions') >= 0)
    anim = [int(c.property('animationOptions')) for c in charts]
    check('U8', bool(anim) and not any(anim), 'chart animationOptions %s (0 = none)' % anim)
    shot = os.environ.get('QA_SHOT')
    if shot:
        img = win.grabWindow()
        print('screenshot', shot, img.save(shot), img.width(), img.height())

    # U6: export through the graph object (what File > Export calls)
    live = find(win, lambda o: isinstance(o, ui_graph.SpectrumGraphData) and o.spectrum is not None)
    ok6 = []
    for ext in ('csv', 'sdb2'):
        p = os.path.join(TMP, 'ui_export.' + ext)
        try:
            live[-1].save_to_file(QtCore.QUrl.fromLocalFile(p))
            ok6.append('%s %d bytes' % (ext, os.path.getsize(p)))
        except Exception as exc:
            ok6.append('%s %r' % (ext, exc))
    check('U6', all('bytes' in s for s in ok6), 'export: %s' % ok6)

    # U9: File > Export the way the dialog does it (ExportDialog.doExport in QML,
    # scan of the window as graphData, every format of the dialog, name typed
    # without extension)
    dlg = find(win, lambda o: o.metaObject().indexOfProperty('nameFilters') >= 0
               and 'sdb2' in ' '.join(o.property('nameFilters') or []) and o.metaObject().indexOfMethod('doExport(QVariant,QVariant)') >= 0)
    ok9 = []
    if not dlg or not live:
        ok9.append('no export dialog with doExport (V11.1 QML not installed?)')
    else:
        d = dlg[0]
        d.setProperty('graphData', live[-1])
        ctx = QtQml.QQmlEngine.contextForObject(d)
        for filt in list(d.property('nameFilters')):
            ext = re.search(r'\*\.(\w+)', filt).group(1)
            base = os.path.join(TMP, 'ui_dialog_export_' + ext)
            js = 'doExport(%s, %s)' % (json.dumps(QtCore.QUrl.fromLocalFile(base).toString()), json.dumps(filt))
            e = QtQml.QQmlExpression(ctx, d, js)
            v = e.evaluate()
            err = e.error().toString() if e.hasError() else ''
            p = base + '.' + ext
            ok9.append('%s %s' % (ext, ('%d bytes' % os.path.getsize(p)) if os.path.exists(p) else 'NOT SAVED %r %s' % (v, err)))
    check('U9', bool(ok9) and all('bytes' in s for s in ok9), 'File > Export via dialog code: %s' % ok9)

    # U4: import a 70 000-point CSV into the window
    att = '/mnt/user-data/attachments/050ccbf1-1a7a-5f20-9122-2d859dde1399/387d523b-NEW6-2_SDR_RTL_470-800_test.csv'
    if os.path.exists(att):
        target = find(win, lambda o: isinstance(o, ui_graph.SpectrumGraphData))
        t0 = time.time()
        try:
            g = ui_graph.SpectrumGraphData()
            g.model = ui_graph.GraphTableModel()
            g.load_from_file(QtCore.QUrl.fromLocalFile(att))
            pump(app, 0.5)
            check('U4', True, 'load 69 401-point CSV into a graph model: %.1f s, model %d columns'
                  % (time.time() - t0, g.model.columnCount(None)))
        except Exception as exc:
            check('U4', False, 'load CSV failed: %r' % (exc,))
    # second scan right after the first (Start again)
    t_start = time.time()
    cfg.startFreq, cfg.endFreq = 470.0, 480.0
    controls[0].scannerState.emit(True)
    pump(app, 0.5)
    while si.running and time.time() - t_start < 60:
        pump(app, 0.1)
    check('U5', not si.running, 'second scan after the first: %.1f s' % (time.time() - t_start))
    win.close()
    pump(app, 0.3)
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    code = main()
    os._exit(code)
