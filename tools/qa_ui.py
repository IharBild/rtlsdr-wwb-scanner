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


def _ui_air():
    a = sim.Air(noise=0.03).tone(480.0e6, 0.02)
    for k in (17, 18, 19):                # own spurs 489.6 / 518.4 / 547.2 MHz (U15)
        a.tone(28.8e6 * k, 0.01)
    return a


class UIFakeRtlSdr(sim.FakeRtlSdr):
    """pyrtlsdr RtlSdr as the window uses it (device list, tuner type)."""
    gain_values = sim.R828D_GAINS
    air = None
    def __init__(self, device_index=0, serial_number=None, device_serial=None, **kw):
        kw.setdefault('air', UIFakeRtlSdr.air or _ui_air())
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


# QA_UI_REAL=1: the real dongle through the installed pyrtlsdr (system test
# on the user's PC); otherwise the simulator
REAL = os.environ.get('QA_UI_REAL') == '1'
if not REAL:
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
    if 'Implicitly defined onFoo' in message or 'QFontDatabase' in message:
        return      # QFontDatabase: no font folder for the offscreen platform on Windows (harmless)
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
    if os.environ.get('QA_UI_ONLY_U1') == '1':        # smoke: the window loads
        return 0 if all(RESULTS) else 1
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
    if REAL:
        si.gain = float(os.environ.get('QA_UI_GAIN', '33.8'))
        print('real dongle, gain %.1f dB' % si.gain, flush=True)
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
    seen_left = ''
    while time.time() - t_start < 900:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        now = time.time()
        gaps.append(now - last)
        last = now
        if not si.running and now - t_start > 1:
            finished_at = now
            break
        if not seen_left and now - t_start > 5:
            labels = find(win, lambda o: o.metaObject().indexOfProperty('text') >= 0
                          and str(o.property('text') or '').endswith(' left'))
            if labels:
                seen_left = str(labels[0].property('text'))
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
        for n, filt in enumerate(list(d.property('nameFilters'))):
            ext = re.search(r'\*\.(\w+)', filt).group(1)
            base = os.path.join(TMP, 'ui_dialog_export_%d_%s' % (n, ext))
            js = 'doExport(%s, %s)' % (json.dumps(QtCore.QUrl.fromLocalFile(base).toString()), json.dumps(filt))
            e = QtQml.QQmlExpression(ctx, d, js)
            v = e.evaluate()
            err = e.error().toString() if e.hasError() else ''
            p = base + '.' + ext
            if os.path.exists(p) and ext == 'csv':
                # V19: two CSV layouts, the chosen filter decides
                head = open(p, newline='').read(9)
                want_wsm = 'WSM' in filt
                if want_wsm != head.startswith('Receiver;'):
                    ok9.append('csv WRONG LAYOUT for %r: starts %r' % (filt, head))
                    continue
                ext = 'csv-wsm' if want_wsm else 'csv-wwb'
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
    v16_checks(app, win, controls, si, seen_left)
    v17_checks(app, win, controls, si)
    v18_checks(app, win, controls, si)
    v181_checks(app, win, controls, si)
    v203_checks(app, win)
    win.close()
    pump(app, 0.3)
    return 0 if all(RESULTS) else 1


def v16_checks(app, win, controls, si, seen_left):
    """V16: status line / error dialog, spur marks, presets, time left."""
    import logging
    import threading
    try:
        from wwb_scanner.ui.pyside import status as ui_status
    except ImportError:
        check('U10', False, 'no wwb_scanner.ui.pyside.status (V16 not installed)')
        return
    hubs = find(win, lambda o: isinstance(o, ui_status.StatusHub))
    if not hubs:
        check('U10', False, 'no StatusHub in the window')
        return
    hub = hubs[0]
    th = threading.Thread(target=lambda: logging.getLogger('wwb_scanner.qa').warning('qa warning from scan thread'))
    th.start(); th.join()
    pump(app, 0.3)
    ok_warn = hub.text == 'qa warning from scan thread' and hub.level == 'warning'
    logging.getLogger('wwb_scanner.qa').error('qa error message')
    pump(app, 0.3)
    dlg = [o for o in win.findChildren(QtCore.QObject) if o.property('title') == 'Problem']
    opened = bool(dlg) and bool(dlg[0].property('opened') or dlg[0].property('visible'))
    if dlg:
        QtCore.QMetaObject.invokeMethod(dlg[0], 'close')
    pump(app, 0.2)
    check('U10', ok_warn and opened and hub.level == 'error',
          'status line %r (%s), error dialog opened %s, log %s' % (hub.text, hub.level, opened, hub.logFile))
    # U11: spur marks drawn inside the visible range
    def walk(item):
        for c in item.childItems():
            yield c
            for g in walk(c):
                yield g
    marks = [o for o in walk(win.contentItem())
             if o.metaObject().indexOfProperty('f') >= 0 and o.metaObject().indexOfProperty('span') >= 0]
    vis = [round(float(m.property('f')), 1) for m in marks if m.property('visible')]
    check('U11', len(marks) == 16, 'spur marks: %d defined, %d visible in the current view %s' % (len(marks), len(vis), vis[:4]))
    # U12: preset sets the range
    combos = find(win, lambda o: o.metaObject().indexOfSignal('activated(int)') >= 0
                  and str(o.property('displayText') or '').startswith('Preset'))
    cfg = si.scanConfig
    if combos:
        combos[0].activated.emit(2)
        pump(app, 0.3)
    check('U12', bool(combos) and cfg.startFreq == 470 and cfg.endFreq == 800,
          'preset "UHF 470-800" -> %.1f-%.1f MHz (%d presets)' % (cfg.startFreq, cfg.endFreq,
                                                             combos[0].property('count') if combos else 0))
    check('U13', bool(seen_left), 'time left during the scan: %r' % (seen_left,))
    if QML_WARNINGS:
        print('QML warnings so far:', QML_WARNINGS[:5])


def v17_checks(app, win, controls, si):
    """V17: clean scan options, calibration, cleaned scan."""
    try:
        from wwb_scanner.scanner import cleanup
    except ImportError:
        check('U14', False, 'no wwb_scanner.scanner.cleanup (V17 not installed)')
        return
    names = ['cleanSpursSwitch', 'cleanRippleSwitch', 'cleanFloorSwitch', 'autoGainSwitch']
    def by_name(n):
        objs = win.findChildren(QtCore.QObject, n)
        return objs[0] if objs else None
    dlg = [o for o in win.findChildren(QtCore.QObject) if o.property('title') == 'Scan settings']
    if dlg:
        QtCore.QMetaObject.invokeMethod(dlg[0], 'open')
        pump(app, 0.5)
    sw = {n: by_name(n) for n in names}
    lbl = by_name('calibrationLabel')
    states = {n: (bool(o.property('checked')) if o is not None else None) for n, o in sw.items()}
    check('U14', bool(dlg) and all(states.values()) and lbl is not None and by_name('calibrateButton') is not None,
          'scan settings: %s; calibration: %r' % (states, lbl.property('text') if lbl is not None else None))
    if dlg:
        QtCore.QMetaObject.invokeMethod(dlg[0], 'close')
        pump(app, 0.3)
    # U15 (kit-8.1): a scan long enough to hold own spurs (480-560 MHz:
    # 489.6, 518.4, 547.2) is cleaned, the values as measured are kept
    t0 = time.time()
    si.scanConfig.startFreq, si.scanConfig.endFreq = 480.0, 560.0
    controls[0].scannerState.emit(True)
    pump(app, 0.5)
    while si.running and time.time() - t0 < 300:
        pump(app, 0.1)
    sp = si.spectrum
    info = getattr(sp, 'cleanup_info', None) if sp is not None else None
    raw = getattr(sp, 'raw_dbFS', None) if sp is not None else None
    gone = (info or {}).get('spurs_removed') or []
    check('U15', bool(info) and info.get('spurs') and raw is not None and raw.size == sp.sample_data.size
          and (len(gone) >= 1 or REAL) and info.get('ripple') == 'scan',   # on air a spur can sit under a signal
          'scan 480-560 in %.0f s: spurs removed %s, ripple %s (%s dB), floor levelled %s%s'
          % (time.time() - t0, gone, (info or {}).get('ripple'), (info or {}).get('ripple_pp_db'),
             (info or {}).get('flatten'), ('; ' + info['floor_skipped']) if (info or {}).get('floor_skipped') else ''))
    # U16: calibration (shortened range in the simulator), then a levelled scan
    if REAL:
        check('U16', True, 'skipped with the real dongle (calibration needs the antenna removed)')
        return
    old = cleanup.CALIBRATION_RANGE_MHZ
    cleanup.CALIBRATION_RANGE_MHZ = (400.0, 720.0)
    hubs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('logFile') >= 0]
    try:
        t0 = time.time()
        si.calibrate()
        pump(app, 0.5)
        while si.running and time.time() - t0 < 300:
            pump(app, 0.1)
        pump(app, 0.5)
        text = hubs[0].property('text') if hubs else ''
        saved = os.path.exists(cleanup.profile_path(si.gain, si.sampleRate * 1e3))
        lbl_text = lbl.property('text') if lbl is not None else ''
        si.scanConfig.startFreq, si.scanConfig.endFreq = 470.0, 490.0
        controls[0].scannerState.emit(True)
        pump(app, 0.5)
        while si.running and time.time() - t0 < 400:
            pump(app, 0.1)
        info = getattr(si.spectrum, 'cleanup_info', None) or {}
        check('U16', saved and 'Calibration saved' in str(text) and info.get('flatten'),
              'calibration %.0f s: %r; label %r; next scan levelled: %s'
              % (time.time() - t0, text, lbl_text[:60], info.get('flatten')))
    finally:
        cleanup.CALIBRATION_RANGE_MHZ = old
    if QML_WARNINGS:
        print('QML warnings so far:', QML_WARNINGS[:5])


def v18_checks(app, win, controls, si):
    """V18: Bands dialog (systems + country), scan of several ranges in the
    window, regulation strip, back to one range."""
    try:
        from wwb_scanner import bands
    except ImportError:
        check('U17', False, 'no wwb_scanner.bands (V18 not installed)')
        return
    dlg = [o for o in win.findChildren(QtCore.QObject) if o.property('title') == 'Bands: your systems and the country']
    sets = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('selected') >= 0
            and o.metaObject().indexOfProperty('country') >= 0]
    if not dlg or not sets:
        check('U17', False, 'Bands dialog %d, settings %d' % (len(dlg), len(sets)))
        return
    d, st = dlg[0], sets[0]
    if os.environ.get('QA_SHOT_BANDS'):
        win.setWidth(1500)
        win.setHeight(900)
    st.setProperty('country', 'PL')
    st.setProperty('selected', '[]')
    n_warn = len(QML_WARNINGS)
    QtCore.QMetaObject.invokeMethod(d, 'open')
    pump(app, 0.8)
    ctx = QtQml.QQmlEngine.contextForObject(d)
    def js(code):
        e = QtQml.QQmlExpression(ctx, d, code)
        v = e.evaluate()
        return (v[0] if isinstance(v, tuple) else v), (e.error().toString() if e.hasError() else '')
    errs = []
    for sid in ('Shure|BLX|K3E', 'Shure|PSM 300|S8'):
        errs.append(js('setChosen(%s, true)' % json.dumps(sid))[1])
    pump(app, 0.3)
    count, e1 = js('info.count')
    rows, e2 = js('rowCount')
    text, e3 = js('info.text')
    shot = os.environ.get('QA_SHOT_BANDS')
    if shot:
        img = win.grabWindow()
        print('screenshot', shot, img.save(shot), img.width(), img.height())
    check('U17', count == 3 and rows == len(bands.SYSTEMS) and not any(errs + [e1, e2, e3])
          and len(QML_WARNINGS) == n_warn,
          'dialog: %s rows, %s ranges chosen; plan: %r; errors %s; new QML warnings %s'
          % (rows, count, str(text)[:120], [x for x in errs + [e1, e2, e3] if x], QML_WARNINGS[n_warn:][:3]))
    QtCore.QMetaObject.invokeMethod(d, 'accept')
    pump(app, 0.5)
    cfg = si.scanConfig
    graphs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('regRegions') >= 0]
    regs = graphs[0].property('regRegions') if graphs else None
    if hasattr(regs, 'toVariant'):
        regs = regs.toVariant()
    btn = [o for o in win.findChildren(QtCore.QObject) if str(o.property('text') or '').startswith('Bands: ')
           and str(o.property('text') or '').endswith(' ranges')]
    check('U18', cfg.scanRanges.count('[') == 4 and cfg.startFreq == 606 and cfg.endFreq == 865
          and bool(regs) and bool(btn),
          'after "Scan these ranges": scanRanges %r, range %.0f-%.0f, regulation regions %d, button %r'
          % (cfg.scanRanges, cfg.startFreq, cfg.endFreq, len(regs or []),
             btn[0].property('text') if btn else None))
    t0 = time.time()
    controls[0].scannerState.emit(True)
    pump(app, 0.5)
    while si.running and time.time() - t0 < 300:
        pump(app, 0.1)
    pump(app, 1.0)
    sp = si.spectrum
    f = np.asarray(sp.sample_data.frequency, float) if sp is not None else np.zeros(0)
    in_gap = int(((f > 630.01) & (f < 822.99)).sum() + ((f > 832.01) & (f < 862.99)).sum())
    gds = [g for g in find(win, lambda o: isinstance(o, ui_graph.SpectrumGraphData))
           if getattr(g, 'spectrum', None) is sp and getattr(g, 'xy_data', None) is not None]
    drops = 0
    if gds:
        drops = int((ui_graph.with_gaps(ui_graph.decimate_max(gds[0].xy_data, gds[0]._decim_k))['y'] < -200).sum())
    hubs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('logFile') >= 0]
    status = str(hubs[0].property('text')) if hubs else ''
    shot = os.environ.get('QA_SHOT_MULTI')
    if shot:
        img = win.grabWindow()
        print('screenshot', shot, img.save(shot), img.width(), img.height())
    check('U19', f.size > 0 and in_gap == 0 and f.min() >= 605.9 and f.max() <= 865.1 and drops == 4
          and 'ranges' in status,
          'scan of 3 ranges in %.0f s: %d points, %d in the gaps, %d gap points on the chart; status %r'
          % (time.time() - t0, f.size, in_gap, drops, status[:100]))
    acts = [o for o in win.findChildren(QtCore.QObject) if str(o.property('text') or '') == 'Scan &one range again']
    if acts:
        QtCore.QMetaObject.invokeMethod(acts[0], 'trigger')
        pump(app, 0.3)
    check('U20', bool(acts) and cfg.scanRanges == '', 'Bands > Scan one range again: scanRanges %r' % cfg.scanRanges)


def v181_checks(app, win, controls, si):
    """V18.1 (audit): a damaged file and a too short scan for smoothing give a
    message in the window and leave it working."""
    hubs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('logFile') >= 0]
    if not hubs:
        check('U21', False, 'no status hub')
        return
    msgs = []
    hubs[0].message.connect(lambda level, text: msgs.append((level, text)))
    import tempfile
    bad = os.path.join(tempfile.mkdtemp(prefix='qa_ui_'), 'damaged.sdb3')
    with open(bad, 'wb') as fh:
        fh.write(b'//@ShureScan{"BitWidth": 24, "BinarySchema": []}@Binary:' + os.urandom(200))
    gds = [g for g in find(win, lambda o: isinstance(o, ui_graph.SpectrumGraphData))
           if hasattr(g, 'load_from_file')]
    err = None
    try:
        gds[0].load_from_file(QtCore.QUrl.fromLocalFile(bad))
    except Exception as exc:
        err = exc
    pump(app, 0.5)
    got = [t for lvl, t in msgs if lvl == 'error' and 'Could not open' in t]
    check('U21', err is None and bool(got) and bool(gds),
          'damaged .sdb3 opened from the window: exception %r; messages %s' % (err, got[:1] or msgs[-2:]))
    if os.environ.get('QA_UI_REAL'):
        check('U22', True, 'INFO: skipped with the real dongle (would need a short scan with smoothing)')
        return
    cfg = si.scanConfig
    old = (cfg.smoothingEnabled, cfg.smoothingFactor, cfg.startFreq, cfg.endFreq, cfg.scanRanges)
    cfg.scanRanges = ''
    cfg.smoothingEnabled = True
    cfg.smoothingFactor = 1.0
    cfg.startFreq, cfg.endFreq = 863.0, 865.0
    del msgs[:]
    t0 = time.time()
    controls[0].scannerState.emit(True)
    pump(app, 0.5)
    while si.running and time.time() - t0 < 120:
        pump(app, 0.1)
    pump(app, 1.0)
    skipped = [t for lvl, t in msgs if 'Smoothing skipped' in t]
    check('U22', not si.running and bool(skipped),
          'scan 863-865 MHz with smoothing 1 %%: window running=%s after %.0f s; messages %s'
          % (si.running, time.time() - t0, skipped[:1] or msgs[-3:]))
    cfg.smoothingEnabled, cfg.smoothingFactor, cfg.startFreq, cfg.endFreq, cfg.scanRanges = old


def v203_checks(app, win):
    """U23: View > Lost sensitivity bands / Removed image marks hide and show the V20.2 marks."""
    acts = {}
    for o in win.findChildren(QtCore.QObject):
        t = str(o.property('text') or '')
        if t in ('&Lost sensitivity bands', 'Removed &image marks') \
                and 'Action' in o.metaObject().className():
            acts[t] = o
    graphs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('showLossBands') >= 0
              and o.metaObject().indexOfProperty('activeSpectrum') >= 0]
    reps = {n: win.findChild(QtCore.QObject, n) for n in ('lossBandRepeater', 'imageMarkRepeater')}
    if len(acts) != 2 or not graphs or None in reps.values():
        check('U23', False, 'View actions %s, graphs %d, repeaters %s' % (sorted(acts), len(graphs), reps))
        return
    from wwb_scanner.ui.pyside.graph import SpectrumGraphData
    marked = 0
    for sg in win.findChildren(SpectrumGraphData):
        if sg.spectrum is not None:
            sg.spectrum.loss_bands = [{'lo': 600., 'hi': 610., 'db': 20., 'signal': 605.}]
            sg.spectrum.image_marks = [{'f': 607.4, 'of': 605.}]
            sg._update_marks()
            marked += 1
    pump(app, 0.3)

    def counts():
        return tuple(int(reps[n].property('count')) for n in ('lossBandRepeater', 'imageMarkRepeater'))
    shown = counts()
    seq = []
    for t in ('&Lost sensitivity bands', 'Removed &image marks'):
        QtCore.QMetaObject.invokeMethod(acts[t], 'trigger')
        pump(app, 0.2)
        seq.append((bool(acts[t].property('checked')), counts()))
    for t in ('&Lost sensitivity bands', 'Removed &image marks'):
        QtCore.QMetaObject.invokeMethod(acts[t], 'trigger')
        pump(app, 0.2)
    back = counts()
    pump(app, 1.0)    # Qt.labs.settings writes after a short delay
    s = QtCore.QSettings()
    s.beginGroup('User Interface')
    stored = (s.value('showLossBands'), s.value('showImageMarks'))
    s.endGroup()
    ok = (marked > 0 and shown[0] >= 1 and shown[1] >= 1 and seq[0] == (False, (0, shown[1]))
          and seq[1] == (False, (0, 0)) and back == shown
          and [str(v).lower() for v in stored] == ['true', 'true'])
    check('U23', ok, 'marks on %d spectra; shown %s, bands off %s, images off %s, both on %s; saved %s'
          % (marked, shown, seq[0], seq[1], back, stored))


def _save_coverage():
    """os._exit skips coverage's own exit hook: save it here when measured."""
    try:
        import coverage
        cov = coverage.Coverage.current()
        if cov is not None:
            cov.stop()
            cov.save()
    except Exception:
        pass


if __name__ == '__main__':
    code = main()
    _save_coverage()
    os._exit(code)
