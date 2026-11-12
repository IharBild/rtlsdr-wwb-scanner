"""V21.1: the small items left from the audit of 11.11.2026 and from the
user's test series (B4, B5, C3, D2, D3, D5, E, F; the raw error text on
unplug; the cut status line)."""
import io
import logging
import os
import threading
import time


import sim
import wwb_scanner
from test_v209 import _isolated

PKG = os.path.dirname(os.path.abspath(wwb_scanner.__file__))
QML = os.path.join(PKG, 'ui', 'pyside', 'qml')


def _read(*parts):
    with io.open(os.path.join(*parts), encoding='utf-8') as fh:
        return fh.read()


def _start(monkeypatch, tmpdir, start, end, sdr_kwargs, **cfg):
    from wwb_scanner.scanner import sdrwrapper, main
    from wwb_scanner import scan_dll
    from wwb_scanner.utils import dbstore
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', lambda serial_number=None: sim.FakeRtlSdr(**sdr_kwargs))
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    sc = main.Scanner(config=sim.make_config(start, end, **cfg))
    th = threading.Thread(target=sc.run_scan, daemon=True)
    th.start()
    return sc, th


# ------------------------------------------------------------------ B4, B5

def test_request_stop_returns_at_once_even_when_usb_hangs(monkeypatch, tmpdir):
    # audit B4: Stop waited for the scan thread inside the window thread
    hang = sim.Fault('read_samples', 'hang', lambda n, f: n >= 3, hang_s=3.0)
    sc, th = _start(monkeypatch, tmpdir, 470, 520, {'faults': [hang], 'read_delay': 0.02})
    time.sleep(0.6)
    t0 = time.time()
    assert sc.request_stop() is True
    took = time.time() - t0
    assert took < 0.2, took
    th.join(30)
    assert not th.is_alive()
    assert sc.request_stop() is False               # nothing runs any more


def test_stopped_scan_is_cropped_cleaned_and_saved(monkeypatch, tmpdir, caplog):
    # audit B5: it was left raw, reached below the start frequency, had no
    # entry in the history and an empty status line
    caplog.set_level(logging.INFO)
    sc, th = _start(monkeypatch, tmpdir, 470, 560, {'read_delay': 0.03}, clean={'clean_ripple': True})
    time.sleep(1.0)
    sc.stop_scan()
    th.join(30)
    assert not th.is_alive() and sc.stop_requested is True
    f, y = sim.spectrum_arrays(sc)
    assert f.size > 40 and f.min() >= 470.0 - 1e-6 and f.max() < 560.0
    assert sc.spectrum.eid is not None                               # in the history
    assert isinstance(getattr(sc.spectrum, 'cleanup_info', None), dict)
    status = [r.getMessage() for r in caplog.records if r.name == 'wwb_scanner.status']
    assert status and 'stopped after' in status[-1] and 'measured part kept' in status[-1], status
    assert ' tiles)' in status[-1]


def test_stop_scan_still_waits_for_scripts(monkeypatch, tmpdir):
    sc, th = _start(monkeypatch, tmpdir, 470, 520, {'read_delay': 0.03})
    time.sleep(0.5)
    sc.stop_scan()
    assert sc._stopped.is_set()
    th.join(10)
    assert not th.is_alive()


# ------------------------------------------------------- raw text on unplug

def test_a_failed_retune_attempt_is_a_warning_not_an_error(monkeypatch, tmpdir, caplog):
    # the raw text opened the "Problem" dialog before the useful message
    caplog.set_level(logging.DEBUG)
    bad = sim.Fault('set_center_freq', 'raise', lambda n, f: n == 3)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 480, sdr_kwargs={'faults': [bad]})
    hits = [r for r in caplog.records if 'failed (attempt' in r.getMessage()]
    assert hits and all(r.levelno == logging.WARNING for r in hits), [(r.levelname, r.getMessage()) for r in hits]
    assert sc.error is None
    src = _read(PKG, 'scanner', 'sample_processing.py')
    assert "logger.error('set_center_freq(%s) failed (attempt" not in src
    assert "logger.error('PLL not locked" not in src


# ----------------------------------------------------------------------- F

def test_window_does_not_need_setuptools_and_the_package_has_a_version():
    assert 'pkg_resources' not in _read(PKG, 'ui', 'pyside', '__init__.py').replace('no pkg_resources', '')
    from wwb_scanner.ui import pyside
    assert pyside.get_resource_filename('qml').is_dir()
    assert (pyside.get_resource_filename('qml') / 'main.qml').is_file()
    cfg = os.path.join(os.path.dirname(PKG), 'setup.cfg')
    if os.path.isfile(cfg):                       # repository layout only
        text = _read(cfg)
        assert 'version = 0.0.1' not in text and 'version = 21.' in text


# ----------------------------------------------------------------------- E

def test_band_tables():
    from wwb_scanner import bands
    src = _read(PKG, 'bands.py')
    assert '2032' not in src.replace('was "until 31.12.2032"', '')
    assert 'until 31.12.2030' in src and 'Vfg. 37/2026' in src
    assert "('Shure', 'SLX-D', 'S50', [(823, 832), (863, 865)], '')" in src
    assert "('Shure', 'SLX-D', 'P53', [(710, 754)], '')" in src
    assert bands is not None


# -------------------------------------------------------------- window, QML

def test_qml_texts():
    dev = _read(QML, 'DeviceConfigPopup.qml')
    assert 'Sample Rate (kHz)' in dev and 'Msps' not in dev.replace('(the title said Msps)', '')
    assert 'function parseSampleRate(text)' in dev and 'parseInt' not in dev
    assert 'function restoreAtOpen()' in dev
    assert os.path.isfile(os.path.join(QML, 'BoxCheck.qml'))
    for name in ('BandsDialog.qml', 'ChartSelectDelegate.qml', 'ThemeSelectPopup.qml'):
        text = _read(QML, name)
        assert 'BoxCheck {' in text and 'CheckBox {' not in text, name
    main = _read(QML, 'main.qml')
    assert "objectName: 'statusLine'" in main and 'maximumLineCount: 2' in main


def test_cursor_takes_the_nearest_point_and_rename_reaches_the_scan():
    _isolated("""
    import numpy as np, types
    from PySide2.QtWidgets import QApplication
    from wwb_scanner.ui.pyside import graph as G
    from wwb_scanner.utils import dbstore
    app = QApplication([])
    g = G.SpectrumGraphData()
    xy = np.zeros(5, dtype=[('x', float), ('y', float)])
    xy['x'] = [470.0, 470.025, 470.05, 470.075, 470.1]
    xy['y'] = [-1., -2., -3., -4., -5.]
    g.xy_data = xy
    for value, want in ((470.026, 470.025), (470.0374, 470.025), (470.0376, 470.05), (469.0, 470.0),
                        (470.099, 470.1), (471.0, 470.1), (470.05, 470.05)):
        got = g.get_nearest_by_x(value).x()
        assert abs(got - want) < 1e-9, (value, got, want)       # audit D5: was the next point to the right
    # audit C3
    calls = []
    dbstore.db_store.update_scan = lambda eid, **kw: calls.append((eid, kw))
    sp = types.SimpleNamespace(name='470-700 MHz, 12.11 19:39', eid=7)
    g._spectrum = sp
    g.name = 'Hall A, stage left'
    assert sp.name == 'Hall A, stage left' and calls == [(7, {'name': 'Hall A, stage left'})], (sp.name, calls)
    g.name = 'Hall A, stage left'
    assert len(calls) == 1
    print('ALL_OK')
    """)


def test_renamed_scan_goes_into_the_wsm_file(monkeypatch, tmpdir):
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 476)
    sp = sc.spectrum
    sp.name = 'Hall A, stage left'
    from wwb_scanner.utils.dbstore import db_store
    db_store.update_scan(sp.eid, name=sp.name)
    from wwb_scanner.scan_objects import Spectrum
    assert Spectrum.from_dbstore(eid=sp.eid).name == 'Hall A, stage left'
    out = str(tmpdir.join('wsm.csv'))
    sp.export_to_file(filename=out, frequency_format='wsm')
    assert 'Receiver;RTL-SDR Hall A, stage left' in _read(out)
