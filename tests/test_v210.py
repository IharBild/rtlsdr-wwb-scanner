"""V21.0: third wave of the code audit of 11.11.2026.

A4  Smoothing and Scaling (Scan settings) changed the levels after the scan
    had been saved: the history held one scan, the graph and the exported
    files another. Scaling stretched the dB axis (19.8 dB between two
    carriers became 36.7 dB), Smoothing averaged over a share of the scan
    (2.3 MHz for 470-700 MHz). Both are gone from the window.
A5  (step 1) The level was labelled "dB" and exported as "dBm". It is the
    spectral density relative to the dongle's full scale at the current gain:
    dBFS/Hz. The window now says so; the numbers and the files do not change."""
import os
import re
import subprocess
import sys
import textwrap

import numpy as np
import pytest

import sim
import wwb_scanner

QML = os.path.join(os.path.dirname(os.path.abspath(wwb_scanner.__file__)), 'ui', 'pyside', 'qml')
PKG = os.path.dirname(os.path.abspath(wwb_scanner.__file__))


def _read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as fh:
        return fh.read()


# ------------------------------------------------------------------------ A4

def test_no_smoothing_or_scaling_in_the_window_files():
    for name in ('ScanConfig.qml', 'ScanControlsDialog.qml'):
        text = _read(QML, name)
        code = '\n'.join(line.split('//')[0] for line in text.splitlines())
        assert not re.search(r'smoothing|scaling(Enabled|MinDB|MaxDB)|scalingSwitch', code), name
    code = _read(PKG, 'ui', 'pyside', 'scanner.py')
    assert 'def smooth_scan' not in code and 'def scale_scan' not in code
    assert 'smoothingEnabled' not in code and 'scalingEnabled' not in code


def test_other_settings_are_still_there():
    text = _read(QML, 'ScanConfig.qml')
    for name in ('startFreq', 'endFreq', 'samplesPerSweep', 'sweepsPerScan', 'sweepOverlapRatio', 'windowType',
                 'windowSize', 'cleanRipple', 'cleanSpurs', 'cleanImages', 'cleanFloor', 'autoGain', 'scanRanges'):
        assert text.count('property alias %s: model.%s' % (name, name)) == 2, name


def _isolated(code, timeout=180):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p),
               QT_QPA_PLATFORM='offscreen')
    prelude = textwrap.dedent('''
        import sys, types, os, tempfile
        os.environ['HOME'] = os.environ['USERPROFILE'] = tempfile.mkdtemp(prefix='v210_')
        try:
            import PySide2
        except ImportError:
            print('NO_PYSIDE2'); raise SystemExit(0)
        try:
            import rtlsdr
        except Exception:
            m = types.ModuleType('rtlsdr')
            class RtlSdr(object):
                def __init__(self, *a, **k):
                    raise IOError('No devices found')
                @staticmethod
                def get_device_serial_addresses():
                    return []
            m.RtlSdr = RtlSdr
            m.RtlSdrTcpClient = None
            sys.modules['rtlsdr'] = m
        from wwb_scanner import scan_dll
        scan_dll._cache['lib'] = None
        import logging, time
        import numpy as np
        from PySide2 import QtCore
        from PySide2.QtWidgets import QApplication
        import sim
        from wwb_scanner.scanner import sdrwrapper
        from wwb_scanner.utils import dbstore
        from wwb_scanner.ui.pyside import scanner as S, graph as G
        home = os.environ['HOME']
        dbstore.DBStore.DB_PATH = os.path.join(home, 'db.json')
        dbstore.DBStore.SCAN_DB_PATH = os.path.join(home, 'scan_db.json')
        app = QApplication([])
        status = []
        class H(logging.Handler):
            def emit(self, r):
                status.append((r.levelname, r.getMessage()))
        logging.getLogger('wwb_scanner.status').addHandler(H())
        logging.getLogger('wwb_scanner.status').setLevel(logging.INFO)
        def pump(s):
            end = time.time() + s
            while time.time() < end:
                app.processEvents(QtCore.QEventLoop.AllEvents, 20); time.sleep(0.002)
        def wait_done(si, s=60):
            end = time.time() + s
            while time.time() < end and (si.running or si.scanner is not None):
                pump(0.02)
            pump(0.3)
            assert not si.running and si.scanner is None, 'scan did not end'
        class Dev(QtCore.QObject):
            device_serial = '00000001'
        def make():
            si = S.ScannerInterface()
            si.scanConfig = S.ScanConfigData()
            si.scanConfig.startFreq, si.scanConfig.endFreq = 470., 476.
            si.gain = 33.8
            si.sampleRate = 2400.
            si.deviceInfo = Dev()
            return si
        def factory(serial_number=None):
            air = sim.Air(noise=0.005).tone(472.0e6, 0.5).tone(474.0e6, 0.05)
            return sim.FakeRtlSdr(serial_number=serial_number, air=air)
        sdrwrapper.RtlSdr = factory
    ''')
    r = subprocess.run([sys.executable, '-c', prelude + textwrap.dedent(code)], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = r.stdout.decode('utf-8', 'replace')
    if 'NO_PYSIDE2' in out:
        pytest.skip('PySide2 not installed')
    assert r.returncode == 0 and 'ALL_OK' in out, 'exit code %s\n%s' % (r.returncode, out[-3000:])
    return out


def test_graph_and_history_hold_the_same_scan_whatever_old_settings_say():
    _isolated("""
    from wwb_scanner.scan_objects import Spectrum
    si = make()
    cfg = si.scanConfig
    for name in ('smoothingEnabled', 'smoothingFactor', 'scalingEnabled', 'scalingMinDB', 'scalingMaxDB'):
        assert name not in dir(S.ScanConfigData), name
    assert not hasattr(S.ScannerInterface, 'smooth_scan') and not hasattr(S.ScannerInterface, 'scale_scan')
    # settings a version before V21.0 left behind have no effect
    cfg.scalingEnabled, cfg.scalingMinDB, cfg.scalingMaxDB = True, -140., -55.
    cfg.smoothingEnabled, cfg.smoothingFactor = True, 1.0
    si.start()
    wait_done(si)
    assert not [t for lvl, t in status if 'Smoothing' in t or 'Scaling' in t], status
    shown = np.asarray(si.spectrum.sample_data['dbFS'], float)
    saved = Spectrum.from_dbstore(eid=si.spectrum.eid)
    kept = np.asarray(saved.sample_data['dbFS'], float)
    assert shown.size == kept.size == 241
    assert np.allclose(shown, kept, atol=1e-6), float(np.max(np.abs(shown - kept)))
    # two carriers 20 dB apart stay 20 dB apart (Scaling made 36.7 of 19.8)
    f = np.asarray(si.spectrum.sample_data['frequency'], float)
    a = shown[np.abs(f - 472.0) < 0.03].max()
    b = shown[np.abs(f - 474.0) < 0.03].max()
    assert abs((a - b) - 20.0) < 0.7, (a, b)
    print('ALL_OK')
    """)


def test_library_methods_stay_for_scripts():
    # the window no longer calls them; scripts that do keep working
    from wwb_scanner.scan_objects import Spectrum
    assert callable(Spectrum.smooth) and callable(Spectrum.scale)


# ------------------------------------------------------------------------ A5

def test_unit_is_named_in_the_window_files():
    graph = _read(QML, 'Graph.qml')
    assert "readonly property string levelUnit: 'dBFS/Hz'" in graph
    assert 'titleText: root.levelUnit' in graph
    assert 'labelFormat: "%.0f dB"' not in graph
    assert 'labelFormat: tickInterval >= 1 ? "%.0f" : "%.1f"' in graph      # no "-98, -98, -99, -99"
    main = _read(QML, 'main.qml')
    assert "mouseDataPoint.y.toFixed(1) + ' dBFS/Hz'" in main and "+ ' dB'" not in main
    assert "objectName: 'levelUnitTip'" in main and 'NOT dBm' in main


def test_export_is_confirmed_with_the_unit():
    _isolated("""
    si = make()
    si.start()
    wait_done(si)
    g = G.SpectrumGraphData()
    g.spectrum = si.spectrum
    folder = os.path.join(home, 'out')
    os.makedirs(folder)
    seen = []
    for name, frag in (('a.csv', ''), ('a.sdb2', ''), ('a.sdb3', ''), ('a.npz', ''), ('w.csv', 'wsm')):
        url = QtCore.QUrl.fromLocalFile(os.path.join(folder, name))
        if frag:
            url.setFragment(frag)
        del status[:]
        g.save_to_file(url)
        assert os.path.getsize(os.path.join(folder, name)) > 100, name
        assert len(status) == 1 and status[0][0] == 'INFO', status
        seen.append(status[0][1])
    for text, name in zip(seen[:4], ('a.csv', 'a.sdb2', 'a.sdb3', 'a.npz')):
        assert text == 'Exported %s. Levels in the file are dBFS/Hz at the gain of the scan (relative, ' \\
            'not dBm; WWB shows them as dBm)' % name, text
    assert seen[4] == 'Exported w.csv. Levels in the file are WSM units above the floor of the scan (0-48)'
    # a failed export still gives its error and no confirmation
    del status[:]
    g.save_to_file(QtCore.QUrl.fromLocalFile(os.path.join(folder, 'no_such_folder', 'x.csv')))
    assert not [t for lvl, t in status if t.startswith('Exported')], status
    print('ALL_OK')
    """)


def test_numbers_and_file_formats_do_not_change(monkeypatch, tmpdir):
    """Step 1 is a label only: a -20 dBFS carrier still reads about -65.7
    (density), and WWB files keep the unit name WWB itself writes."""
    air = sim.Air(noise=0.002).tone(474.0e6, 0.1)            # amplitude 0.1 = -20 dBFS
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 478, sdr_kwargs={'air': air, 'quantize': False})
    f, y = sim.spectrum_arrays(sc)
    assert y[np.abs(f - 474.0) < 0.03].max() == pytest.approx(-65.7, abs=0.5)
    out = str(tmpdir.join('scan.sdb2'))
    sc.spectrum.export_to_file(filename=out)
    text = open(out, encoding='utf-8').read()
    assert 'dBm' in text
    from wwb_scanner.file_handlers import exporters
    code = _read(os.path.dirname(exporters.__file__), 'exporters.py')
    assert "ampl_units='dBm'" in code and "'AmplUnits': 'dBm'" in code
