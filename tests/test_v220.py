"""V22.0: foundation (audit of the architecture 16.11.2026, items 1, 2, 5, 6).

- Python 3.11+ and PySide6 only;
- one data folder (wwb_scanner/paths.py) for history, calibrations, logs,
  the time report and the window settings (INI instead of the registry);
- one schema of the scan settings, defaults = the tested configuration;
- the core works without the history database and without librtlsdr;
- no tinydb / json-object-factory; the files keep their layout;
- dead code removed (Sample objects, matplotlib plot, rtl_power, TCP dongle,
  async reader, scale / smooth / interpolate).
"""
import base64
import datetime
import io
import json
import os
import subprocess
import sys
import textwrap

import numpy as np
import pytest

import sim

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pkg_dir():
    import wwb_scanner
    return os.path.dirname(os.path.abspath(wwb_scanner.__file__))


def _run(code, **env):
    e = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p), QT_QPA_PLATFORM='offscreen')
    e.update(env)
    r = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], env=e, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=180)
    return r.returncode, r.stdout.decode('utf-8', 'replace')


# ------------------------------------------------------------------ data folder

def test_data_folder_order(monkeypatch, tmpdir):
    from wwb_scanner import paths
    monkeypatch.setattr(paths, '_override', None)
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    assert paths.data_dir() == os.path.join(str(tmpdir), 'wwb_scanner_data')
    monkeypatch.setenv(paths.ENV_VAR, str(tmpdir.join('env')))
    assert paths.data_dir() == str(tmpdir.join('env'))
    paths.set_data_dir(str(tmpdir.join('given')))
    try:
        assert paths.data_dir() == str(tmpdir.join('given'))
        assert paths.calibration_dir() == str(tmpdir.join('given', 'calibration'))
        assert paths.scans_dir() == str(tmpdir.join('given', 'scans'))
        assert paths.log_file() == str(tmpdir.join('given', 'logs', 'wwb_scanner.log'))
        assert paths.timing_report_file() == str(tmpdir.join('given', 'wwb_scan_timing.txt'))
        assert paths.settings_file() == str(tmpdir.join('given', 'rtlsdr-wwb-scanner', 'wwb_scanner.ini'))
        assert not tmpdir.join('given').exists()               # nothing is created unless asked
        paths.calibration_dir(create=True)
        assert tmpdir.join('given', 'calibration').isdir()
    finally:
        paths.set_data_dir(None)


def test_every_module_uses_the_data_folder(monkeypatch, tmpdir):
    from wwb_scanner import paths
    from wwb_scanner.scanner import cleanup, gain_cal, gain_profile
    monkeypatch.setattr(paths, '_override', str(tmpdir))
    assert cleanup.calibration_dir() == str(tmpdir.join('calibration'))
    assert os.path.dirname(gain_profile.profile_path()) == str(tmpdir.join('calibration'))
    assert os.path.dirname(gain_cal.table_path(2.4e6)) == str(tmpdir.join('calibration'))
    # no module builds its own path from the home folder any more
    bad = []
    for dp, _, files in os.walk(_pkg_dir()):
        for f in files:
            if f.endswith('.py') and f != 'paths.py':
                t = io.open(os.path.join(dp, f), encoding='utf-8').read()
                if 'wwb_scanner_data' in t or "expanduser('~')" in t or "'.config'" in t:
                    bad.append(f)
    assert not bad, bad


def test_time_report_goes_to_the_data_folder(monkeypatch, tmpdir):
    from wwb_scanner import paths
    from wwb_scanner.scanner.scan_timing import ScanTiming
    monkeypatch.setattr(paths, '_override', str(tmpdir.join('d')))
    p = ScanTiming().write_report()
    assert p == str(tmpdir.join('d', 'wwb_scan_timing.txt')) and os.path.exists(p)


def test_one_log_file_in_the_data_folder(tmpdir):
    rc, out = _run("""
        import logging
        from wwb_scanner import paths, log_config
        paths.set_data_dir(%r)
        logging.getLogger().setLevel(logging.INFO)
        p = log_config.add_file_handler()
        assert log_config.add_file_handler() == p                # once
        logging.getLogger('wwb_scanner.x').info('Привет, Łódź')
        for h in logging.getLogger().handlers:
            h.flush()
        print('LOG', p)
        print('TEXT', 'Łódź' in open(p, encoding='utf-8').read())
    """ % str(tmpdir))
    assert rc == 0, out
    assert 'LOG %s' % tmpdir.join('logs', 'wwb_scanner.log') in out and 'TEXT True' in out, out


# ------------------------------------------------------------------ settings schema

def test_defaults_are_the_tested_configuration():
    from wwb_scanner.scanner import config
    c = config.ScanConfig()
    t = config.TESTED
    assert (c.sampling.sample_rate, c.device.gain, c.sampling.window_type, c.sampling.window_size,
            c.sampling.samples_per_sweep, c.sampling.sweeps_per_scan, c.sampling.sweep_overlap_ratio) == \
        (2.4e6, 33.8, 'hann', 96, 2048, 40, 0.65) == \
        (t['sample_rate'], t['gain'], t['window_type'], t['window_size'], t['samples_per_sweep'],
         t['sweeps_per_scan'], t['sweep_overlap_ratio'])
    assert c.sampling.auto_gain and c.sampling.verify_peaks
    assert c.processing.clean_ripple and c.processing.clean_spurs and c.processing.clean_floor
    assert not c.processing.clean_images


def test_window_factory_comes_from_the_core():
    pytest.importorskip('PySide6')
    from wwb_scanner.scanner.config import TESTED
    from wwb_scanner.ui.pyside.scanner import FACTORY, ScanConfigData
    assert FACTORY == dict(sampleRate=2400., gain=33.8, windowSize=96, samplesPerSweep=2048,
                           sweepsPerScan=40, sweepOverlapRatio=0.65)
    d = ScanConfigData()
    assert (d.windowType, d.cleanRipple, d.cleanSpurs, d.cleanImages, d.cleanFloor, d.autoGain,
            d.verifyPeaks) == (TESTED['window_type'], True, True, False, True, True, True)


def test_unknown_key_is_an_error_and_old_keys_are_dropped():
    from wwb_scanner.scanner import config
    c = config.ScanConfig()
    with pytest.raises(KeyError):
        c.sampling.sampel_rate = 1                         # typo
    with pytest.raises(KeyError):
        c.sampling['fft_size'] = 1024
    with pytest.raises(AttributeError):
        c.sampling.no_such_key
    # settings stored by V21.x (db.json): removed keys are dropped
    old = {'scan_range': [470., 480.], '_child_conf_keys': ['device', 'sampling', 'processing'],
           'save_raw_values': False, 'eid': 7,
           'device': {'gain': 20.7, 'is_remote': False, 'remote_hostname': '127.0.0.1', 'remote_port': 1235,
                      'serial_number': None, 'freq_correction': 0},
           'sampling': {'sample_rate': 2048000.0, 'fft_size': 1024, 'rtl_crop': 50, 'rtl_bin_size': 0.025,
                        'rtl_fir_size': 4, 'window_type': 'boxcar', 'window_size': None},
           'processing': {'smoothing_enabled': False, 'smoothing_factor': 1.0, 'scaling_enabled': True,
                          'scaling_min_db': -140.0, 'scaling_max_db': -50.0}}
    c = config.ScanConfig(old)
    assert c.eid == 7 and c.device.gain == 20.7 and c.sampling.window_type == 'boxcar'
    assert sorted(c.dropped_keys) == ['save_raw_values']
    assert sorted(c.sampling.dropped_keys) == ['fft_size', 'rtl_bin_size', 'rtl_crop', 'rtl_fir_size']
    assert c.sampling.window_size is None                  # stored value kept as stored
    s = c._serialize()
    assert '_child_conf_keys' not in s and 'fft_size' not in s['sampling']
    assert config.ScanConfig(json.loads(json.dumps(s)))._serialize() == s


def test_settings_check():
    from wwb_scanner.scanner.config import ScanConfig
    assert ScanConfig({'scan_range': [470., 480.]}).check() is None
    assert 'must be above Start' in ScanConfig({'scan_range': [480., 470.]}).check()
    assert 'numbers' in ScanConfig({'scan_range': [float('nan'), 470.]}).check()
    assert 'must be above Start' in ScanConfig({'scan_ranges': [[470., 480.], [500., 500.]]}).check()


# ------------------------------------------------------------------ core without database / library

def test_scanner_without_settings_does_not_read_the_history(monkeypatch, tmpdir):
    from wwb_scanner.utils import dbstore
    from wwb_scanner.scanner import main, config
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    dbstore.db_store.add_scan_config(config.ScanConfig({'scan_range': [100., 101.],
                                                        'device': {'gain': 0.}}))
    sc = main.ScannerBase()
    assert list(sc.config.scan_range) == [470., 536.] and sc.config.device.gain == 33.8


def test_history_none_saves_nothing(monkeypatch, tmpdir):
    from wwb_scanner.utils import dbstore
    from wwb_scanner.scanner import sdrwrapper, main
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_index.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', lambda serial_number=None: sim.FakeRtlSdr())
    sc = main.Scanner(config=sim.make_config(470, 472), history=None)
    sc.run_scan()
    assert sc.error is None and sc.spectrum.sample_data.size == 81
    assert not tmpdir.join('scan_index.json').exists() and not tmpdir.join('scans').exists()
    sc = main.Scanner(config=sim.make_config(470, 472))          # default: the history
    sc.run_scan()
    assert tmpdir.join('scan_index.json').exists() and len(tmpdir.join('scans').listdir()) == 1


def test_core_imports_without_librtlsdr():
    """pyrtlsdr fails to import when its library is missing: the scanner
    core, the files and the history must still work (tools, web server)."""
    rc, out = _run("""
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name == 'rtlsdr' or name.startswith('rtlsdr.'):
                    raise ImportError('no librtlsdr here')
        sys.meta_path.insert(0, Block())
        import wwb_scanner.scanner, wwb_scanner.scan_objects, wwb_scanner.file_handlers
        from wwb_scanner.utils import dbstore
        from wwb_scanner.scanner import main
        main.Scanner(config={})
        print('OK', 'rtlsdr' in sys.modules, 'tinydb' in sys.modules, 'jsonfactory' in sys.modules,
              'matplotlib' in sys.modules)
    """)
    assert rc == 0 and 'OK False False False False' in out, out


def test_spectrum_module_has_no_import_time_links():
    rc, out = _run("""
        import sys
        import wwb_scanner.scan_objects.spectrum
        print('MODS', sorted(m for m in ('wwb_scanner.utils.dbstore', 'wwb_scanner.file_handlers',
                                          'wwb_scanner.ui.plots') if m in sys.modules))
    """)
    assert rc == 0 and 'MODS []' in out, out


# ------------------------------------------------------------------ files keep their layout

def test_json_of_earlier_versions_reads_the_same():
    """Text as json-object-factory wrote it (V17.1-V21.4)."""
    from wwb_scanner.utils import numpyjson
    import wwb_scanner.scan_objects  # noqa: F401  (registers SampleArray)
    buf = io.BytesIO()
    np.save(buf, np.arange(3.0), allow_pickle=False)
    text = json.dumps({'a': {'__npy__': base64.b64encode(buf.getvalue()).decode()},
                       'when': {'__datetime.datetime__': '2026-11-16T13:14:15.000123 '}})
    d = numpyjson.loads(text)
    assert np.array_equal(d['a'], [0., 1., 2.])
    assert d['when'] == datetime.datetime(2026, 11, 16, 13, 14, 15, 123)
    again = numpyjson.loads(numpyjson.dumps(d))
    assert np.array_equal(again['a'], d['a']) and again['when'] == d['when']
    assert '__npy__' in numpyjson.dumps(d) and '__datetime.datetime__' in numpyjson.dumps(d)


def test_history_scan_of_v21_opens(monkeypatch, tmpdir):
    """A history record and points file as V21.4.1 wrote them."""
    from wwb_scanner.utils import dbstore
    from wwb_scanner.scan_objects import Spectrum, SampleArray
    old_dtype = np.dtype([('frequency', np.float64), ('iq', np.complex128),
                          ('magnitude', np.float64), ('dbFS', np.float64)])
    pts = np.zeros(5, dtype=old_dtype)
    pts['frequency'] = 470. + 0.025 * np.arange(5)
    pts['dbFS'] = -90.
    pts['magnitude'] = 1e-9
    tmpdir.mkdir('scans')
    np.savez_compressed(str(tmpdir.join('scans', 'scan_00001_x.npz')), sample_data=pts)
    tmpdir.join('scan_index.json').write(json.dumps({'_default': {}, 'scans_performed': {'1': {
        'name': 'Bands 470-480 MHz, 16.11 13:14', 'color': {'r': 0, 'g': 1, 'b': 0, 'a': 1},
        'timestamp_utc': 1.7e9, 'step_size': 0.775, 'center_frequencies': [470.5],
        'scan_config_eid': None, 'sample_data_file': 'scan_00001_x.npz'}}}))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_index.json')))
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    sp = Spectrum.from_dbstore(eid=1)
    assert sp.name.startswith('Bands') and sp.sample_data.size == 5
    assert sp.sample_data.data.dtype == SampleArray.dtype
    assert list(dbstore.db_store.get_all_scans()) == [1]


def test_points_are_24_bytes():
    from wwb_scanner.scan_objects import SampleArray
    assert SampleArray.dtype.itemsize == 24 and 'iq' not in SampleArray.dtype.names


# ------------------------------------------------------------------ window settings file

def test_window_settings_move_from_the_registry_once(tmpdir):
    """Linux stand-in for the Windows registry: Qt's native format."""
    pytest.importorskip('PySide6')
    rc, out = _run("""
        from wwb_scanner import paths
        paths.set_data_dir(%r)
        from wwb_scanner.ui.pyside.qt import QtCore
        from wwb_scanner.ui.pyside import main as M
        S = QtCore.QSettings
        native = S(S.Format.NativeFormat, S.Scope.UserScope, 'wwb-v220-test', 'wwb_scanner')
        native.clear()
        native.setValue('Scan Config/windowSize', 128)
        native.setValue('Scan Presets/presets', '[["UHF",470,700]]')
        native.setValue('User Interface/darkWindow', True)
        native.sync()
        app = QtCore.QCoreApplication([])
        app.setOrganizationName('wwb-v220-test'); app.setApplicationName('wwb_scanner')
        p = M.setup_settings('wwb-v220-test', 'wwb_scanner')
        s = S()
        print('FILE', p == paths.settings_file('wwb-v220-test'), s.fileName() == p)
        print('VALUES', s.value('Scan Config/windowSize'), s.value('Scan Presets/presets'),
              str(s.value('User Interface/darkWindow')).lower())
        s.setValue('Scan Config/windowSize', 96); s.sync()
        native.setValue('Scan Config/windowSize', 64); native.sync()
        M.setup_settings('wwb-v220-test', 'wwb_scanner')        # second start: no copy again
        print('AFTER', S().value('Scan Config/windowSize'), native.value('Scan Config/windowSize'))
        native.clear(); native.sync()
    """ % str(tmpdir))
    assert rc == 0, out
    assert 'FILE True True' in out, out
    assert 'VALUES 128 [["UHF",470,700]] true' in out, out
    assert 'AFTER 96 64' in out, out


# ------------------------------------------------------------------ dead code is gone

def test_removed_modules_and_names():
    pkg = _pkg_dir()
    for p in ('scan_objects/sample.py', 'ui/plots.py', 'scanner/rtlpower_scan.py',
              'ui/pyside/qml/compat/qt5', 'ui/pyside/qml/compat/qt6'):
        assert not os.path.exists(os.path.join(pkg, p)), p
    from wwb_scanner.scanner import main, sample_processing, sdrwrapper
    from wwb_scanner.scan_objects import spectrum
    for mod, name in ((main, 'ThreadedScanner'), (main, 'scan_and_plot'), (sample_processing, 'USE_SYNC_READ'),
                      (sample_processing.SampleSet, 'samples_callback'),
                      (sample_processing.SampleSet, 'translate_freq'),
                      (sdrwrapper.SdrWrapper, '_open_sdr_remote'), (spectrum, 'TimeBasedSpectrum'),
                      (spectrum.Spectrum, 'smooth'), (spectrum.Spectrum, 'scale'),
                      (spectrum.Spectrum, 'interpolate'), (spectrum.Spectrum, 'add_sample')):
        assert not hasattr(mod, name), name


def test_qt_message_handler_survives_many_messages():
    """PySide6 6.12.0 on Python 3.11 aborts after a few hundred messages
    through a Python handler (setup.cfg keeps it below 6.12 there)."""
    pytest.importorskip('PySide6')
    rc, out = _run("""
        from wwb_scanner.ui.pyside.qt import QtCore
        from wwb_scanner.ui.pyside import main as M
        app = QtCore.QCoreApplication([])
        QtCore.qInstallMessageHandler(M.qt_message_handler)
        for i in range(3000):
            QtCore.qWarning('message %d' % i)
        print('SURVIVED')
    """)
    assert rc == 0 and 'SURVIVED' in out, out[-500:]


def test_code_needs_python_311():
    from wwb_scanner.ui.pyside import qt
    assert qt.API == 'PySide6' and qt.QT6
    t = io.open(os.path.join(_pkg_dir(), 'ui', 'pyside', 'qt.py'), encoding='utf-8').read()
    assert 'from PySide2' not in t and 'import PySide2' not in t
