"""V18.1 "Reliability": the findings of the independent code audit
(2026-11-07) that were confirmed. One test per finding (A<audit number>)."""
import json
import logging
import os
import sys
import threading
import types

import numpy as np
import pytest

import sim
from wwb_scanner.scan_objects.spectrum import Spectrum
from wwb_scanner.scan_objects.samplearray import SampleArray


def _spectrum(n, level=-90.0, noise=1.0, seed=1):
    sp = Spectrum()
    rng = np.random.RandomState(seed)
    sp.add_sample_set(frequency=470 + np.arange(n) * 0.025, dbFS=level + noise * rng.randn(n))
    return sp


# ------------------------------------------------------------------ 1.1

def test_A1_1_scanner_does_not_open_the_dongle_when_created(monkeypatch, tmpdir):
    from wwb_scanner.scanner import sdrwrapper, main
    opened = []
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', lambda serial_number=None: opened.append(1) or sim.FakeRtlSdr())
    main.Scanner(config=sim.make_config(470, 480, gain=30.0))
    assert opened == [], 'the dongle was opened in the window thread'


def test_A1_1_gain_still_rounded_to_the_dongle_table(monkeypatch, tmpdir):
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 475, gain=30.0)
    assert scanner.gain == 29.7                    # nearest R828D step
    assert sdr.gains_set and sdr.gains_set[0] == 29.7
    assert scanner.spectrum.sample_data.size > 100


# ------------------------------------------------------------------ 1.2

def test_A1_2_hung_usb_call_blocks_the_next_scan(monkeypatch, tmpdir):
    from wwb_scanner.scanner import sample_processing as SP
    release = threading.Event()
    with pytest.raises(SP.SdrTimeout):
        SP.run_with_timeout(release.wait, 0.05)
    assert SP.stuck_usb_calls() == 1
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 475)
    assert sdr is None, 'dongle opened again while a USB call hangs'
    assert isinstance(scanner.error, IOError) and 'Unplug' in str(scanner.error)
    release.set()
    for _ in range(100):
        if SP.stuck_usb_calls() == 0:
            break
        threading.Event().wait(0.01)
    assert SP.stuck_usb_calls() == 0
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir.mkdir('b'), 470, 475)
    assert sdr is not None and scanner.error is None


# ------------------------------------------------------------------ 1.3 / 1.4

def test_A1_3_scale_empty_or_flat():
    assert Spectrum().scale(-120, -20) is False
    flat = _spectrum(50, noise=0.0)
    assert flat.scale(-120, -20) is False
    assert np.all(flat.sample_data['dbFS'] == -90)
    sp = _spectrum(200)
    assert sp.scale(-120, -20) is True
    y = sp.sample_data['dbFS']
    assert np.isfinite(y).all() and abs(y.min() + 120) < 1e-6 and abs(y.max() + 20) < 1e-6


@pytest.mark.parametrize('n,N,ok', [(81, 0, False), (81, 1, False), (81, 2, False), (81, 3, True),
                                    (50, 60, False), (3, 4, False), (200, 4, True), (13200, 132, True)])
def test_A1_4_smooth_short_scans(n, N, ok):
    sp = _spectrum(n)
    before = sp.sample_data['dbFS'].copy()
    assert sp.smooth(N) is ok
    y = sp.sample_data['dbFS']
    assert y.size == n and np.isfinite(y).all()
    if not ok:
        assert np.array_equal(y, before)


def _isolated(code):
    """Run ``code`` in a fresh Python: an earlier test (test_graph_model) puts
    a stand-in PySide2 into this process."""
    import subprocess
    import textwrap
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p),
               QT_QPA_PLATFORM='offscreen')
    prelude = textwrap.dedent('''
        import sys, types
        try:
            import rtlsdr
        except ImportError:          # no librtlsdr here (sandbox): same stand-in as conftest.py
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
    ''')
    r = subprocess.run([sys.executable, '-c', prelude + textwrap.dedent(code)], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    out = r.stdout.decode('utf-8', 'replace')
    if 'NO_PYSIDE2' in out:
        pytest.skip('PySide2 not installed')
    assert r.returncode == 0 and 'ALL_OK' in out, out[-2000:]


def test_A1_4_window_leaves_scanning_state_even_after_an_error():
    _isolated("""
        import types
        try:
            import PySide2
        except ImportError:
            print('NO_PYSIDE2'); raise SystemExit(0)
        from wwb_scanner.ui.pyside.scanner import ScannerInterface
        def boom():
            raise RuntimeError('smoothing exploded')
        fake = types.SimpleNamespace(scan_thread=None, calibrating=False, scanner=object(), running=True,
                                     scanConfig=types.SimpleNamespace(smoothingEnabled=True, scalingEnabled=False),
                                     smooth_scan=boom)
        ScannerInterface.on_scanner_finished(fake)
        assert fake.running is False and fake.scanner is None, vars(fake)
        print('ALL_OK')
    """)


# ------------------------------------------------------------------ 1.6

def test_A1_6_device_list_survives_pulled_or_busy_dongle():
    _isolated("""
        try:
            import PySide2
        except ImportError:
            print('NO_PYSIDE2'); raise SystemExit(0)
        from wwb_scanner.ui.pyside import device_config as DC

        class R(object):
            serials = ['A', 'B']
            closed = 0
            def __init__(self, index=None, device_serial=None):
                if index == 1:
                    raise IOError('device busy')
                self.gain_values = [0, 297]
            @classmethod
            def get_device_serial_addresses(cls):
                return list(cls.serials)
            def get_tuner_type(self):
                return 6
            def close(self):
                R.closed += 1
        DC.RtlSdr = R
        lst = DC.DeviceInfoList()
        lst._on_update_devices()                         # must not raise
        assert sorted(lst._devices) == [0] and R.closed == 1, (lst._devices, R.closed)
        R.serials = []
        lst._on_update_devices()
        assert lst._devices == {}, 'pulled out dongle still in the list'
        def broken(cls):
            raise IOError('usb error')
        R.get_device_serial_addresses = classmethod(broken)
        lst._on_update_devices()
        print('ALL_OK')
    """)


# ------------------------------------------------------------------ 2.1 / 2.4 / 2.5

def test_A2_1_sample_without_frequency_does_not_touch_the_spectrum():
    from wwb_scanner.scan_objects import Sample
    sp = _spectrum(20)
    before = sp.sample_data.data.copy()
    s = Sample(spectrum=sp, frequency=999.0, init_complete=False)
    s.magnitude = 1.0
    s.dbFS = -10.0
    s.iq = (1.0, 0.0)
    assert np.array_equal(sp.sample_data.data, before)


def test_A2_4_points_file_name_must_be_plain(tmpdir):
    from wwb_scanner.utils.dbstore import DBStore
    for bad in ('../x.npz', '..\\x.npz', 'C:\\x.npz', '/etc/x.npz', 'a/b.npz', '..', '', None, 5):
        assert DBStore._safe_name(bad) is None, bad
    assert DBStore._safe_name('scan_0001_470-800.npz') == 'scan_0001_470-800.npz'


def test_A2_4_scan_index_with_bad_path_is_not_read(monkeypatch, tmpdir, caplog):
    from wwb_scanner.utils import dbstore
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    outside = tmpdir.join('outside.npz')
    np.savez(str(outside), sample_data=np.zeros(3))
    table = dbstore.db_store.scan_db.table('scans_performed')
    eid = table.insert({'name': 'x', 'sample_data_file': '../outside.npz'})
    with caplog.at_level(logging.ERROR):
        rec = dbstore.db_store.get_scan(eid)
    assert 'sample_data' not in rec
    assert 'not a plain file name' in caplog.text


def test_A2_5_interpolate_whole_number_spacing():
    pytest.importorskip('scipy')
    sa = SampleArray.create(frequency=np.arange(470.0, 480.0, 0.5), dbFS=-90 + np.zeros(20))
    sa.interpolate(spacing=1)
    assert sa.frequency.size >= 9


# ------------------------------------------------------------------ 1.5 / 3.2 / 3.3

def test_A3_2_unsupported_or_missing_file_names(tmpdir):
    from wwb_scanner.file_handlers.importers import BaseImporter
    sp = _spectrum(100)
    with pytest.raises(ValueError) as e:
        sp.export_to_file(filename=str(tmpdir.join('x.txt')))
    assert 'not supported' in str(e.value)
    with pytest.raises(ValueError) as e:
        sp.export_to_file(filename=None)
    assert 'no file name' in str(e.value)
    tmpdir.join('y.txt').write('470000,-90\n')
    with pytest.raises(ValueError) as e:
        BaseImporter.import_file(str(tmpdir.join('y.txt')))
    assert 'not supported' in str(e.value)
    from wwb_scanner.scanner.main import scan_and_save
    with pytest.raises(ValueError):
        scan_and_save()


def test_A3_3_damaged_sdb3_gives_a_clear_error(tmpdir):
    from wwb_scanner.file_handlers.importers import BaseImporter
    good = os.path.join(os.path.dirname(__file__), 'reference', 'wwb79_sdb3_from_our_sdb2.sdb3')
    data = open(good, 'rb').read()
    i = data.index(b'@Binary:')
    header = json.loads(data[len(b'//@ShureScan'):i].decode('utf-8'))
    for change in ({'BitWidth': 24}, {'BinarySchema': None}):
        h = dict(header, **change)
        if change.get('BinarySchema', 1) is None:
            del h['BinarySchema']
        p = str(tmpdir.join('bad%d.sdb3' % len(os.listdir(str(tmpdir)))))
        with open(p, 'wb') as fh:
            fh.write(b'//@ShureScan' + json.dumps(h).encode('utf-8') + data[i:])
        with pytest.raises(ValueError) as e:
            BaseImporter.import_file(p)
        assert 'BitWidth' in str(e.value) or 'damaged' in str(e.value)
    assert BaseImporter.import_file(good).sample_data.size > 1000


# ------------------------------------------------------------------ window error hook

def test_A_window_errors_go_to_the_status_line():
    _isolated("""
        import logging, sys
        try:
            import PySide2
        except ImportError:
            print('NO_PYSIDE2'); raise SystemExit(0)
        from wwb_scanner.ui.pyside import main as M
        got = []
        class H(logging.Handler):
            def emit(self, rec):
                got.append(rec.getMessage())
        logging.getLogger('wwb_scanner.ui').addHandler(H(logging.ERROR))
        M.install_error_hooks()
        try:
            raise ZeroDivisionError('test error')
        except ZeroDivisionError:
            sys.excepthook(*sys.exc_info())
        import threading
        t = threading.Thread(target=lambda: 1 / 0)
        t.start(); t.join()
        assert any('ZeroDivisionError: test error' in g for g in got), got
        if hasattr(threading, 'excepthook'):
            assert len(got) == 2, got
        print('ALL_OK')
    """)
