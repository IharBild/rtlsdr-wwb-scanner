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
    extra_kw = {}               # V20.5 (U25): e.g. gain_affects
    def __init__(self, device_index=0, serial_number=None, device_serial=None, **kw):
        kw.setdefault('air', UIFakeRtlSdr.air or _ui_air())
        for k, v in UIFakeRtlSdr.extra_kw.items():
            kw.setdefault(k, v)
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
    # V20.8 (U29): are these the settings of a first start?
    fresh = not QtCore.QSettings('rtlsdr-wwb-scanner-qa', 'wwb_scanner').contains('Scan Config/windowSize')
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
    first_start = dict(sampleRate=si.sampleRate, gain=si.gain, windowSize=cfg.windowSize,
                       samplesPerSweep=cfg.samplesPerSweep, sweepsPerScan=cfg.sweepsPerScan,
                       sweepOverlapRatio=cfg.sweepOverlapRatio)
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
    v204_checks(app, win, controls, si)
    v205_checks(app, win, si)
    v206_checks(app, win, si)
    v207_checks(app, win, si)
    v210_checks(app, win)
    v211_checks(app, win, si)
    v209_checks(app, win, si)
    v208_checks(app, win, controls, si, fresh, first_start)
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
    want, cleaned = 16, False
    try:
        # V20.9: a scan cleaned of spurs brings its own marks (hidden and kept),
        # any other scan gets the 16 known frequencies
        gr = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfMethod('spurMarkModel()') >= 0]
        if gr:
            want = int(_qml_eval(gr[0], 'spurMarkModel().length'))
            cleaned = bool(_qml_eval(gr[0], 'activeSpectrum ? activeSpectrum.spursCleaned : false'))
    except Exception:
        pass
    check('U11', len(marks) == want and (cleaned or want == 16),
          'spur marks: %d drawn, %d expected (%s), %d visible in the current view %s'
          % (len(marks), want, 'of this scan' if cleaned else 'known frequencies', len(vis), vis[:4]))
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
    """V18.1 (audit): a damaged file gives a message in the window and leaves
    it working; U22 (V21.0): no Smoothing / Scaling, a short scan ends."""
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
    # U22. Until V20.9: a scan too short for smoothing. V21.0: Smoothing and
    # Scaling are gone from the window; a short scan ends and is left as measured.
    cfg = si.scanConfig
    gone = not hasattr(cfg, 'smoothingEnabled') and not hasattr(cfg, 'scalingEnabled') \
        and not hasattr(si, 'smooth_scan') and not hasattr(si, 'scale_scan')
    dlg_sw = win.findChild(QtCore.QObject, 'cleanSpursSwitch')
    in_dialog = None
    if dlg_sw is not None:
        try:
            in_dialog = str(_qml_eval(dlg_sw, "typeof root.smoothingEnabled + '/' + typeof root.scalingEnabled"))
        except Exception as exc:
            in_dialog = 'error: %s' % exc
    if os.environ.get('QA_UI_REAL'):
        check('U22', gone and in_dialog == 'undefined/undefined',
              'Smoothing and Scaling removed: settings %s, dialog %s (short scan skipped with the real dongle)'
              % (gone, in_dialog))
        return
    old = (cfg.startFreq, cfg.endFreq, cfg.scanRanges)
    cfg.scanRanges = ''
    cfg.startFreq, cfg.endFreq = 863.0, 865.0
    del msgs[:]
    t0 = time.time()
    controls[0].scannerState.emit(True)
    pump(app, 0.5)
    while si.running and time.time() - t0 < 120:
        pump(app, 0.1)
    pump(app, 1.0)
    bad = [t for lvl, t in msgs if 'Smoothing' in t or 'Scaling' in t or lvl == 'error']
    n = int(si.spectrum.sample_data.size) if si.spectrum is not None else 0
    check('U22', not si.running and gone and in_dialog == 'undefined/undefined' and not bad and n == 81,
          'Smoothing and Scaling removed: settings %s, dialog %s; scan 863-865 MHz: %d points, window running=%s '
          'after %.0f s, messages %s' % (gone, in_dialog, n, si.running, time.time() - t0, bad[:2]))
    cfg.startFreq, cfg.endFreq, cfg.scanRanges = old


def v203_checks(app, win):
    """U23: View > Lost sensitivity bands / Removed image marks hide and show the V20.2 marks."""
    acts = {}
    for o in win.findChildren(QtCore.QObject):
        t = str(o.property('text') or '')
        if t in ('&Lost sensitivity bands', 'Removed &image marks', '&Image marks') \
                and 'Action' in o.metaObject().className():
            acts[t] = o
    graphs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('showLossBands') >= 0
              and o.metaObject().indexOfProperty('activeSpectrum') >= 0]
    reps = {n: win.findChild(QtCore.QObject, n) for n in ('lossBandRepeater', 'imageMarkRepeater')}
    image_action = '&Image marks' if '&Image marks' in acts else 'Removed &image marks'   # V20.9: renamed
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
    for t in ('&Lost sensitivity bands', image_action):
        QtCore.QMetaObject.invokeMethod(acts[t], 'trigger')
        pump(app, 0.2)
        seq.append((bool(acts[t].property('checked')), counts()))
    for t in ('&Lost sensitivity bands', image_action):
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


def v204_checks(app, win, controls, si):
    """U24: File > Export while a scan is running asks first (the file would
    hold only the part measured so far); after the scan it does not."""
    if os.environ.get('QA_UI_REAL'):
        check('U24', True, 'INFO: skipped with the real dongle (needs a running scan)')
        return
    dlg = win.findChild(QtCore.QObject, 'exportUnfinishedDialog')
    act = [o for o in win.findChildren(QtCore.QObject)
           if str(o.property('text') or '') == '&Export' and 'Action' in o.metaObject().className()]
    if dlg is None or not act:
        check('U24', False, 'dialog %r, export action %d' % (dlg, len(act)))
        return
    cfg = si.scanConfig
    old = (cfg.startFreq, cfg.endFreq, cfg.scanRanges)
    cfg.scanRanges = ''
    cfg.startFreq, cfg.endFreq = 470.0, 560.0
    t0 = time.time()
    controls[0].scannerState.emit(True)
    pump(app, 0.3)
    while not si.running and time.time() - t0 < 10:
        pump(app, 0.1)
    pump(app, 0.5)
    was_running = bool(si.running)
    QtCore.QMetaObject.invokeMethod(act[0], 'trigger')
    pump(app, 0.3)
    during = bool(dlg.property('opened')) or bool(dlg.property('visible'))
    QtCore.QMetaObject.invokeMethod(dlg, 'reject')
    pump(app, 0.3)
    while si.running and time.time() - t0 < 120:
        pump(app, 0.1)
    pump(app, 1.0)
    fd = [o for o in win.findChildren(QtCore.QObject)
          if o.metaObject().indexOfProperty('graphData') >= 0 and o.metaObject().indexOfProperty('folder') >= 0]
    after_ok = None
    if fd:
        QtCore.QMetaObject.invokeMethod(act[0], 'trigger')
        pump(app, 0.5)
        after_ok = not (bool(dlg.property('opened')) or bool(dlg.property('visible')))
        try:
            QtCore.QMetaObject.invokeMethod(fd[0], 'close')
        except Exception:
            pass
        pump(app, 0.3)
    cfg.startFreq, cfg.endFreq, cfg.scanRanges = old
    check('U24', was_running and during and after_ok is not False,
          'scan running %s; Export during the scan asks first %s; after the scan asks %s'
          % (was_running, during, None if after_ok is None else not after_ok))


def v205_checks(app, win, si):
    """U25: Scan settings > Calibrate gain steps: survey + steps, table saved,
    status and the calibration label say so (simulator: 470-640 MHz)."""
    if REAL:
        check('U25', True, 'INFO: skipped with the real dongle (writes the table of your dongle)')
        return
    from wwb_scanner.scanner import gain_cal
    btn = win.findChild(QtCore.QObject, 'calibrateGainsButton')
    lbl = win.findChild(QtCore.QObject, 'calibrationLabel')
    hubs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('logFile') >= 0]
    old = (gain_cal.GAIN_CAL_RANGE_MHZ, UIFakeRtlSdr.air, UIFakeRtlSdr.extra_kw)
    gain_cal.GAIN_CAL_RANGE_MHZ = (470.0, 640.0)
    UIFakeRtlSdr.extra_kw = {'gain_affects': True}
    UIFakeRtlSdr.air = (sim.Air(noise=0.002).band(474e6, 482e6, 0.3).band(530e6, 538e6, 0.06)
                        .band(602e6, 610e6, 0.2).band(626e6, 634e6, 0.05))
    try:
        t0 = time.time()
        si.calibrateGains()
        pump(app, 0.5)
        while si.running and time.time() - t0 < 300:
            pump(app, 0.1)
        pump(app, 0.5)
        dt = time.time() - t0
        path = gain_cal.table_path(si.sampleRate * 1e3)
        saved = os.path.exists(path)
        text = str(hubs[0].property('text')) if hubs else ''
        lbl_text = ''
        if lbl is not None:
            try:
                lbl_text = str(si.calibrationStatus())
            except Exception:
                lbl_text = str(lbl.property('text'))
        check('U25', btn is not None and saved and 'Gain steps calibrated' in text
              and 'Gain steps calibrated' in lbl_text and not si.running,
              'gain %.1f dB; button %s; %.0f s; table saved %s; status %r; label %r'
              % (si.gain, btn is not None, dt, saved, text[:90], lbl_text[-90:]))
    finally:
        gain_cal.GAIN_CAL_RANGE_MHZ, UIFakeRtlSdr.air, UIFakeRtlSdr.extra_kw = old
        try:
            os.remove(gain_cal.table_path(si.sampleRate * 1e3))     # later checks: nominal steps
        except OSError:
            pass


def _qml_eval(obj, js):
    from PySide2 import QtQml
    ctx = QtQml.QQmlEngine.contextForObject(obj)
    expr = QtQml.QQmlExpression(ctx, obj, js)
    val = expr.evaluate()
    if isinstance(val, tuple):          # PySide2: (value, valueIsUndefined)
        val = val[0]
    if expr.hasError():
        raise RuntimeError(expr.error().toString())
    return val


def v207_checks(app, win, si):
    """U27: Clean scan block - short calibration lines with a state dot and
    (i) help; U28: a scan in the list is renamed and deleted."""
    box = win.findChild(QtCore.QObject, 'cleanScanBox')
    names = ['calibrationLabel', 'gainStepsLabel', 'floorCalDot', 'gainStepsDot', 'suggestGainButton']
    objs = {n: win.findChild(QtCore.QObject, n) for n in names}
    tips = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().className().startswith('InfoTip')]
    info = {}
    try:
        info = dict(si.calibrationInfo())
    except Exception as exc:
        info = {'error': str(exc)}
    texts = [str(objs[n].property('text')) for n in ('calibrationLabel', 'gainStepsLabel') if objs[n] is not None]
    ok = (box is not None and str(box.property('title')) == 'Clean scan' and all(objs.values())
          and len(tips) >= 8 and info.get('floorState') in ('ok', 'warn', 'none')
          and info.get('stepsState') in ('ok', 'warn', 'none')
          and all(t and len(t) <= 120 and '\n' not in t for t in texts))
    check('U27', ok, 'title %r; %d info tips; states %s/%s; lines %r'
          % (box.property('title') if box is not None else None, len(tips), info.get('floorState'),
             info.get('stepsState'), texts))
    # U28
    cs = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfMethod('renameItem(QVariant,QVariant)') >= 0]
    gr = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfMethod('removeSpectrum(QVariant)') >= 0]
    if not cs or not gr:
        check('U28', False, 'scan list %s, graph %s' % (bool(cs), bool(gr)))
        return
    cs, gr = cs[0], gr[0]
    try:
        n0 = int(_qml_eval(gr, 'spectrumGraphs.filter(function(s){ return !!s; }).length'))
        if n0 < 2:      # one more graph to delete
            _qml_eval(gr, "addModel({})")
            pump(app, 0.5)
            n0 = int(_qml_eval(gr, 'spectrumGraphs.filter(function(s){ return !!s; }).length'))
        idx = int(_qml_eval(gr, 'spectrumGraphs.filter(function(s){ return !!s && !(s.scanner && s.scanner.running); })[0].index'))
        renamed = bool(_qml_eval(cs, "renameItem(%d, '  mics off, 17:20  ')" % idx))
        pump(app, 0.2)
        name = str(_qml_eval(gr, 'spectrumGraphs[%d].name' % idx))
        row_name = str(_qml_eval(cs, 'itemName(%d)' % idx))
        empty_refused = not bool(_qml_eval(cs, "renameItem(%d, '   ')" % idx))
        removed = bool(_qml_eval(gr, 'removeSpectrum(spectrumGraphs[%d])' % idx))
        pump(app, 0.3)
        n1 = int(_qml_eval(gr, 'spectrumGraphs.filter(function(s){ return !!s; }).length'))
        rows = int(_qml_eval(cs, 'itemCount()'))
        active_ok = bool(_qml_eval(gr, '!!activeSpectrum && activeSpectrum.index != %d' % idx))
        ok = (renamed and name == 'mics off, 17:20' and row_name == name and empty_refused and removed
              and n1 == n0 - 1 and rows == n1 and active_ok)
        check('U28', ok, 'renamed %s -> %r (list %r), empty name refused %s; deleted %s: graphs %d -> %d, '
              'list rows %d, another graph active %s' % (renamed, name, row_name, empty_refused, removed,
                                                        n0, n1, rows, active_ok))
    except Exception as exc:
        check('U28', False, 'error: %s' % exc)


def v208_checks(app, win, controls, si, fresh, first_start):
    """V20.8 (audit of 11.11.2026). U29: a first start has the tested settings;
    U30: untouched old factory settings are replaced once, touched ones never;
    U31: Scan settings fits the window and scrolls to "Suggest gain";
    U32: Start and Calibrate without a dongle say so and leave nothing behind."""
    factory = getattr(ui_scanner, 'FACTORY', None)
    if factory is None:
        check('U29', False, 'no FACTORY in ui/pyside/scanner.py (V20.8 not installed)')
        return
    # U29
    if fresh:
        same = all(abs(float(first_start[k]) - float(v)) < 1e-6 for k, v in factory.items())
        check('U29', same, 'settings of a first start: %s' % first_start)
    else:
        check('U29', True, 'INFO: settings of earlier QA runs are stored on this PC, first start not checked')
    # U30
    try:
        def state():
            return [float(_qml_eval(win, e)) for e in (
                'device_config.sampleRate', 'device_config.gain', 'scanConfig.windowSize',
                'scanConfig.samplesPerSweep', 'scanConfig.sweepsPerScan', 'scanConfig.sweepOverlapRatio')]
        def put(rate, gain, win_size, per_sweep, sweeps, overlap):
            _qml_eval(win, 'device_config.sampleRate = %r' % rate)
            _qml_eval(win, 'device_config.setGain(%r)' % gain)
            _qml_eval(win, 'scanConfig.windowSize = %r' % win_size)
            _qml_eval(win, 'scanConfig.samplesPerSweep = %r' % per_sweep)
            _qml_eval(win, 'scanConfig.sweepsPerScan = %r' % sweeps)
            _qml_eval(win, 'scanConfig.sweepOverlapRatio = %r' % overlap)
            _qml_eval(win, 'defaultsSettings.version = 0')
            pump(app, 0.1)
        before = state()
        new = [2400., 33.8, 96., 2048., 40., 0.65]
        put(2048, 0.0, 128, 8192, 20, 0.5)
        replaced = bool(_qml_eval(win, 'replaceOldFactorySettings()'))
        pump(app, 0.2)
        after_old = state()
        again = bool(_qml_eval(win, 'replaceOldFactorySettings()'))
        put(2048, 20.7, 128, 8192, 20, 0.5)         # the user chose another gain: hands off
        touched = bool(_qml_eval(win, 'replaceOldFactorySettings()'))
        after_touched = state()
        put(*before)
        _qml_eval(win, 'defaultsSettings.version = 208')
        pump(app, 0.2)
        ok = (replaced and all(abs(a - b) < 1e-6 for a, b in zip(after_old, new)) and not again
              and not touched and after_touched == [2048., 20.7, 128., 8192., 20., 0.5]
              and abs(float(si.gain) - before[1]) < 1e-6)
        check('U30', ok, 'old factory settings -> %s (replaced %s, second call %s); gain changed by the user: '
              'replaced %s, stays %s' % (after_old, replaced, again, touched, after_touched))
    except Exception as exc:
        check('U30', False, 'error: %s' % exc)
    # U31
    scroller = win.findChild(QtCore.QObject, 'scanSettingsScroller')
    if scroller is None:
        check('U31', False, 'no scroll area in Scan settings')
    else:
        try:
            sizes = []
            for h in (768, 600):
                win.setHeight(h)
                pump(app, 0.3)
                _qml_eval(scroller, 'root.open()')
                pump(app, 0.5)
                dlg_h = float(_qml_eval(scroller, 'root.height'))
                over_h = float(_qml_eval(win, 'window.height'))
                _qml_eval(scroller, 'scroller.contentY = Math.max(0, scroller.contentHeight - scroller.height)')
                pump(app, 0.2)
                y = float(_qml_eval(scroller, 'suggestGainBtn.mapToItem(scroller, 0, 0).y'))
                view_h = float(_qml_eval(scroller, 'scroller.height'))
                btn_h = float(_qml_eval(scroller, 'suggestGainBtn.height'))
                top = float(_qml_eval(scroller, 'root.y'))
                sizes.append((h, dlg_h, over_h, top, 0 <= y and y + btn_h <= view_h + 1))
                _qml_eval(scroller, 'root.close()')
                pump(app, 0.3)
            ok = all(d <= o and t >= 0 and vis for _h, d, o, t, vis in sizes)
            check('U31', ok, '; '.join('window %d: dialog %.0f of %.0f px, top %.0f, "Suggest gain" reachable %s'
                                       % x for x in sizes))
        except Exception as exc:
            check('U31', False, 'error: %s' % exc)
    # U32 (last: it takes the dongle away from the window)
    try:
        from wwb_scanner.ui.pyside import status as ui_status
        hub = (find(win, lambda o: isinstance(o, ui_status.StatusHub)) or [None])[0]
        gr = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfMethod('removeSpectrum(QVariant)') >= 0][0]
        n0 = int(_qml_eval(gr, 'spectrumGraphs.filter(function(s){ return !!s; }).length'))
        dev = si.deviceInfo
        si.deviceInfo = None
        pump(app, 0.2)
        controls[0].scannerState.emit(True)
        pump(app, 0.6)
        text = str(hub.text) if hub is not None else ''
        n1 = int(_qml_eval(gr, 'spectrumGraphs.filter(function(s){ return !!s; }).length'))
        started = bool(si.running)
        si.calibrate()
        pump(app, 0.2)
        cal = bool(si.calibrating)
        si.calibrateGains()
        pump(app, 0.2)
        gcal = bool(si._gain_calibrating)
        si.deviceInfo = dev
        # Start looks for a dongle plugged in after the program started
        looks = bool(_qml_eval(win, "typeof device_config.refreshDevices === 'function'"))
        ok = ('No RTL-SDR dongle found' in text and not started and n1 == n0 and not cal and not gcal
              and getattr(si, 'scan_init_thread', None) is None and looks)
        check('U32', ok, 'Start without a dongle: %r; running %s; graphs %d -> %d; calibration flags left %s/%s; '
              'Start looks for a new dongle %s' % (text[:60], started, n0, n1, cal, gcal, looks))
    except Exception as exc:
        check('U32', False, 'error: %s' % exc)


def v210_checks(app, win):
    """V21.0 (audit A5, step 1). U36: the level is named dBFS/Hz on the axis
    and in the readout, with an explanation; U37: an export is confirmed in
    the status line with what the numbers in the file are."""
    try:
        gr = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfMethod('spurMarkModel()') >= 0]
        readout = win.findChild(QtCore.QObject, 'levelReadout')
        tip = win.findChild(QtCore.QObject, 'levelUnitTip')
        if not gr or readout is None or tip is None:
            check('U36', False, 'graph %s, readout %s, tip %s (V21.0 not installed)'
                  % (bool(gr), readout is not None, tip is not None))
            return
        axis = gr[0].property('levelAxis')
        title = str(axis.property('titleText'))
        fmt_wide = str(axis.property('labelFormat'))
        keep = (float(axis.property('min')), float(axis.property('max')))
        axis.setProperty('min', -99.5)
        axis.setProperty('max', -97.5)
        pump(app, 0.2)
        step = float(axis.property('tickInterval'))
        fmt_narrow = str(axis.property('labelFormat'))
        axis.setProperty('min', keep[0])
        axis.setProperty('max', keep[1])
        pump(app, 0.2)
        text = str(readout.property('text'))
        tip_text = str(tip.property('text'))
        ok = (title == 'dBFS/Hz' and text.endswith('dBFS/Hz') and 'NOT dBm' in tip_text
              and fmt_wide == '%.0f' and (step >= 1 or fmt_narrow == '%.1f'))
        check('U36', ok, 'axis title %r, readout %r, labels %r, zoomed to 2 dB: tick %.2f dB, labels %r; '
              'explanation %d characters' % (title, text, fmt_wide, step, fmt_narrow, len(tip_text)))
    except Exception as exc:
        check('U36', False, 'error: %s' % exc)
    try:
        from wwb_scanner.ui.pyside import status as ui_status
        from wwb_scanner.ui.pyside.graph import SpectrumGraphData
        hub = (find(win, lambda o: isinstance(o, ui_status.StatusHub)) or [None])[0]
        sgs = [g for g in win.findChildren(SpectrumGraphData) if g.spectrum is not None]
        if hub is None or not sgs:
            check('U37', False, 'status hub %s, scans %d' % (hub is not None, len(sgs)))
            return
        import tempfile
        d = tempfile.mkdtemp(prefix='qa_ui_')
        got = []
        for name, frag in (('u37.csv', ''), ('u37.sdb2', ''), ('u37wsm.csv', 'wsm')):
            url = QtCore.QUrl.fromLocalFile(os.path.join(d, name))
            if frag:
                url.setFragment(frag)
            sgs[-1].save_to_file(url)
            pump(app, 0.3)
            got.append((name, os.path.exists(os.path.join(d, name)), str(hub.text)))
        ok = (all(e for _, e, _ in got) and all(t.startswith('Exported ' + n) for n, _, t in got)
              and 'dBFS/Hz' in got[0][2] and 'not dBm' in got[1][2] and 'above the floor' in got[2][2])
        check('U37', ok, '; '.join('%s: %r' % (n, t[:95]) for n, _, t in got))
    except Exception as exc:
        check('U37', False, 'error: %s' % exc)


def v211_checks(app, win, si):
    """V21.1. U38: Device Settings - the sample rate field takes kHz or MHz,
    refuses nonsense, Cancel puts gain and sample rate back; U39: the status
    line shows two lines; Stop only asks the scan to end."""
    dlg = win.findChild(QtCore.QObject, 'deviceConfigPopup')
    if dlg is None or dlg.metaObject().indexOfMethod('parseSampleRate(QVariant)') < 0:
        check('U38', False, 'Device Settings without parseSampleRate (V21.1 not installed)')
        return
    try:
        got = [str(_qml_eval(dlg, 'String(parseSampleRate(%r))' % t)) for t in ('2400', '2.4', '2,048', '250', '5', 'abc', '400')]
        want = ['2400', '2400', '2048', '250', 'NaN', 'NaN', 'NaN']
        before = (float(dlg.property('gain')), float(dlg.property('sampleRate')))
        _qml_eval(dlg, 'open()')
        pump(app, 0.3)
        dlg.setProperty('sampleRate', 2048.)
        _qml_eval(dlg, 'setGain(12.5)')
        pump(app, 0.1)
        changed = (float(dlg.property('gain')), float(dlg.property('sampleRate')))
        _qml_eval(dlg, 'reject()')
        pump(app, 0.3)
        after = (float(dlg.property('gain')), float(dlg.property('sampleRate')))
        check('U38', got == want and after == before and changed != before,
              'field: %s; Cancel: %s -> %s -> %s' % (got, before, changed, after))
    except Exception as exc:
        check('U38', False, 'error: %s' % exc)
    try:
        line = win.findChild(QtCore.QObject, 'statusLine')
        from wwb_scanner.scanner import main as scan_main
        ok = line is not None and int(line.property('maximumLineCount')) == 2 and \
            hasattr(scan_main.Scanner, 'request_stop')
        check('U39', ok, 'status line: %s lines; Stop asks without waiting: %s' % (
            line.property('maximumLineCount') if line is not None else None, hasattr(scan_main.Scanner, 'request_stop')))
    except Exception as exc:
        check('U39', False, 'error: %s' % exc)


def v209_checks(app, win, si):
    """V20.9. U33: "Hide dongle's own spurs" and "Hide images of strong
    signals" (off unless switched on) reach the scan settings; U34: the graph
    marks what the spur cleaning hid and what it kept; U35: an image mark
    says whether the image was left in the scan."""
    from wwb_scanner.ui.pyside.graph import SpectrumGraphData
    sw_s = win.findChild(QtCore.QObject, 'cleanSpursSwitch')
    sw_i = win.findChild(QtCore.QObject, 'cleanImagesSwitch')
    if sw_i is None or not hasattr(SpectrumGraphData, 'spur_marks_of'):
        check('U33', False, 'no "Hide images of strong signals" switch (V20.9 not installed)')
        return
    # U33
    try:
        sc = si.scanConfig
        old = (bool(sc.property('cleanSpurs')), bool(sc.property('cleanImages')))
        first = bool(si.build_scan_config().processing.clean_images)
        got = []
        for spurs, images in ((True, True), (False, False), (True, False)):
            _qml_eval(sw_i, 'root.open()')
            pump(app, 0.3)
            sw_s.setProperty('checked', spurs)
            sw_i.setProperty('checked', images)
            pump(app, 0.1)
            _qml_eval(sw_i, 'root.accept()')
            pump(app, 0.3)
            pc = si.build_scan_config().processing
            got.append((spurs, images) == (bool(pc.clean_spurs), bool(pc.clean_images)))
        sc.setProperty('cleanSpurs', old[0])
        sc.setProperty('cleanImages', old[1])
        text = str(sw_s.property('text'))
        check('U33', all(got) and text.startswith('Hide') and not (first and not old[1]),
              'spur switch %r; images hidden at start %s; three settings reach the scan %s' % (text, first, got))
    except Exception as exc:
        check('U33', False, 'error: %s' % exc)
    # U34 / U35
    try:
        rep = win.findChild(QtCore.QObject, 'spurMarkRepeater')
        irep = win.findChild(QtCore.QObject, 'imageMarkRepeater')
        gr = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfMethod('spurMarkModel()') >= 0][0]
        active = _qml_eval(gr, 'activeSpectrum')
        sg = active.findChild(SpectrumGraphData) if active is not None else None
        if sg is None or sg.spectrum is None:
            sgs = [g for g in win.findChildren(SpectrumGraphData) if g.spectrum is not None]
            sg = sgs[-1] if sgs else None
        if rep is None or sg is None:
            check('U34', False, 'repeater %s, spectrum %s' % (rep is not None, sg is not None))
            return
        sp = sg.spectrum
        keep = (getattr(sp, 'cleanup_info', None), getattr(sp, 'image_marks', None))
        n_known = len(list(_qml_eval(gr, 'spurFreqs').toVariant()))
        sp.cleanup_info = {'spurs': True, 'spurs_removed': [489.6, 504.0], 'spurs_kept': [[518.4, 32.0]]}
        sp.image_marks = [{'f': 607.4, 'of': 605., 'hidden': False}]
        sg._update_marks()
        pump(app, 0.3)
        is_active = bool(_qml_eval(gr, 'activeSpectrum ? activeSpectrum.spursCleaned : false'))
        n_scan = int(rep.property('count'))
        model = _qml_eval(gr, 'JSON.stringify(spurMarkModel())')
        def texts(start):
            def walk(item):
                for c in item.childItems():
                    yield c
                    for g in walk(c):
                        yield g
            return [str(o.property('text')) for o in walk(win.contentItem())
                    if o.metaObject().indexOfProperty('text') >= 0
                    and str(o.property('text') or '').startswith(start)]
        labels = texts(('on spur grid', 'image'))
        sp.cleanup_info = {'spurs': False}
        sp.image_marks = [{'f': 607.4, 'of': 605., 'hidden': True}]
        sg._update_marks()
        pump(app, 0.3)
        n_raw = int(rep.property('count'))
        labels2 = texts('image')
        if keep[0] is None:
            del sp.cleanup_info
        else:
            sp.cleanup_info = keep[0]
        sp.image_marks = keep[1] or []
        sg._update_marks()
        pump(app, 0.2)
        if not is_active:
            check('U34', True, 'INFO: the changed spectrum is not the active one, marks not checked')
            return
        ok = n_scan == 3 and n_raw == n_known and any(t == 'on spur grid +32 dB: kept' for t in labels)
        check('U34', ok, 'cleaned scan: %d marks %s, labels %s; scan without spur cleaning: %d known frequencies'
              % (n_scan, model, [t for t in labels if t.startswith('on spur')], n_raw))
        if irep is not None and int(irep.property('count') or 0) >= 0:
            ok = 'image? of 605.000' in labels and 'image of 605.000 (hidden)' in labels2
            check('U35', ok, 'left in the scan: %s; hidden: %s' % (
                [t for t in labels if t.startswith('image')], labels2))
    except Exception as exc:
        check('U34', False, 'error: %s' % exc)


def v206_checks(app, win, si):
    """U26: Scan settings > Suggest gain sets the gain of Device Settings from
    the gain profile, with and without auto gain, and says why."""
    btn = win.findChild(QtCore.QObject, 'suggestGainButton')
    lbl = win.findChild(QtCore.QObject, 'suggestGainLabel')
    sw = win.findChild(QtCore.QObject, 'autoGainSwitch')
    if btn is None or lbl is None or sw is None:
        check('U26', False, 'button %s, label %s, switch %s' % (btn is not None, lbl is not None, sw is not None))
        return
    old_gain, old_auto = si.gain, sw.property('checked')
    got = []
    try:
        for auto in (True, False):
            sw.setProperty('checked', auto)
            want = si.suggestGain(auto)
            QtCore.QMetaObject.invokeMethod(btn, 'clicked')
            pump(app, 0.3)
            got.append((auto, want[0], si.gain, str(lbl.property('text'))))
        ok = all(w >= 0 and abs(g - w) < 0.05 and t.startswith('Set to') for _, w, g, t in got)
        check('U26', ok, '; '.join('auto %s: suggested %.1f, set %.1f, %r' % (a, w, g, t[:70])
                                   for a, w, g, t in got))
    finally:
        sw.setProperty('checked', old_auto)
        dc = [o for o in win.findChildren(QtCore.QObject) if o.metaObject().indexOfProperty('deviceSerial') >= 0]
        if dc and old_gain is not None:
            dc[0].setProperty('gain', old_gain)
        pump(app, 0.2)


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
