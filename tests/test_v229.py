"""V22.9: reliability 2 (external audit of V22.8, three passes, 22.11.2026).

P3-01  no dongle is closed under a running USB call (gate.py); a call on a
       closed dongle never reaches the freed handle.
AUD-02 own gain per band: after a USB error the next band uses the dongle
       opened anew; a band that can not be set up is "not measured".
AUD-05 a USB error in the gain probe: the dongle is opened anew.
AUD-04 opening the dongle has a time limit.
P3-15  the step of the tiles follows the settings; without a dongle a clear error.
"""
import logging
import threading
import time

import pytest

import sim
from wwb_scanner.receivers import gate
from wwb_scanner.receivers.rtlsdr import RtlSdrReceiver
from wwb_scanner.scanner import main, sdrwrapper, sample_processing as sp, gain_plan as gp
from wwb_scanner.scanner.config import ScanConfig


def _patch(monkeypatch, tmpdir, factory):
    from wwb_scanner import scan_dll
    from wwb_scanner.utils import dbstore
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', factory)
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    monkeypatch.setenv('WWB_SCANNER_DATA', str(tmpdir.join('data')))


def _factory(made, **kw):
    def factory(serial_number=None, device_index=None):
        s = sim.FakeRtlSdr(serial_number=serial_number, **kw)
        made.append(s)
        return s
    return factory


def _wait_unstuck(limit=15.):
    end = time.monotonic() + limit
    while sp.stuck_usb_calls() and time.monotonic() < end:
        time.sleep(0.05)
    assert not sp.stuck_usb_calls()


# ---------------------------------------------------------------- P3-01

@pytest.mark.parametrize('delay', [0.15, 0.3, 0.45, 0.6, 0.75])
def test_close_from_another_thread_waits_for_the_running_read(monkeypatch, tmpdir, delay):
    made = []
    _patch(monkeypatch, tmpdir, _factory(made, read_delay=0.04))
    sc = main.Scanner(config=sim.make_config(470, 490), history=None)
    th = threading.Thread(target=sc.run_scan, daemon=True)
    th.start()
    time.sleep(delay)
    sc.sdr_wrapper.close_sdr()          # what the audit did from the main thread
    th.join(60)
    assert not th.is_alive()
    assert made and all(s.closed_under_call == 0 for s in made)
    assert all(s.closed_calls == 0 for s in made), 'a call reached a closed dongle'


def test_a_call_on_a_closed_dongle_never_reaches_it():
    dev = sim.FakeRtlSdr()
    rx = RtlSdrReceiver(dev)
    dev.close()
    for f in (lambda: rx.read(1024), lambda: rx.tune(100e6), lambda: setattr(rx, 'gain', 20.)):
        with pytest.raises(gate.DongleClosed):
            f()
    assert dev.closed_calls == 0
    assert isinstance(gate.DongleClosed('x'), IOError)


def test_a_call_that_does_not_end_leaves_the_dongle_open(monkeypatch):
    monkeypatch.setattr(gate, 'CLOSE_WAIT_S', 0.2)
    dev = sim.FakeRtlSdr(faults=[sim.Fault('read_samples', 'hang', lambda n, f: True, hang_s=1.5)])
    rx = RtlSdrReceiver(dev)
    th = threading.Thread(target=rx.read, args=(1024,), daemon=True)
    th.start()
    time.sleep(0.1)
    w = sdrwrapper.SdrWrapper()
    w.sdr = dev
    n0 = len(sdrwrapper._ABANDONED)
    try:
        w._drop_sdr()
        assert dev.device_opened and dev.closed_under_call == 0
        assert sdrwrapper._ABANDONED[-1] is dev and len(sdrwrapper._ABANDONED) == n0 + 1
    finally:
        th.join(5)
        del sdrwrapper._ABANDONED[n0:]
    assert gate.active(dev) == 0


def test_new_calls_wait_while_the_dongle_is_closed():
    dev = sim.FakeRtlSdr()
    rx = RtlSdrReceiver(dev)
    got = {}
    with gate.quiet(dev) as ok:
        assert ok
        th = threading.Thread(target=lambda: got.setdefault('e', _catch(rx.read, 64)), daemon=True)
        th.start()
        time.sleep(0.2)
        assert th.is_alive() and dev.calls['read_samples'] == 0
        dev.close()
    th.join(5)
    assert isinstance(got['e'], gate.DongleClosed) and dev.closed_calls == 0


def _catch(f, *a):
    try:
        f(*a)
    except Exception as e:
        return e


# ---------------------------------------------------------------- AUD-02

def _three_band_plan(self):
    return gp.Plan(33.8, [gp.PlanBand(470, 480, 33.8), gp.PlanBand(480, 490, 49.6),
                          gp.PlanBand(490, 500, 25.4)])


def test_usb_error_in_one_band_keeps_the_other_bands(monkeypatch, tmpdir, caplog):
    """The audit's repro (P3-02): three bands, the dongle closes itself in the
    second one. Before: OSError use after close, nothing saved."""
    monkeypatch.setattr(main.Scanner, 'make_gain_plan', _three_band_plan)
    state = {'hit': False}

    def once_in_band_2(n, f):
        if not state['hit'] and 480e6 <= f < 490e6:
            state['hit'] = True
            return True
        return False
    with caplog.at_level(logging.INFO, logger='wwb_scanner.status'):
        sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 500., band_gain=True, sdr_kwargs={
            'faults': [sim.Fault('read_samples', 'close', once_in_band_2)]})
    assert sc.error is None, sc.error
    f, y = sim.spectrum_arrays(sc)
    assert (f < 480).any() and (f > 490).any(), 'a band was lost'
    assert all(s.closed_calls == 0 for s in sim.FakeRtlSdr.instances[-3:])


def test_band_that_can_not_be_set_up_is_not_measured(monkeypatch, tmpdir, caplog):
    monkeypatch.setattr(main.Scanner, 'make_gain_plan', _three_band_plan)
    real = sim.FakeRtlSdr.gain.fset

    def gain_fails_at_49(self, v):
        if abs(float(v) - 49.6) < 0.05:
            raise IOError('LIBUSB_ERROR_PIPE (injected)')
        real(self, v)
    monkeypatch.setattr(sim.FakeRtlSdr, 'gain', sim.FakeRtlSdr.gain.setter(gain_fails_at_49))
    with caplog.at_level(logging.INFO, logger='wwb_scanner.status'):
        sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 500., band_gain=True)
    assert sc.error is None, sc.error
    assert sc.bands_not_measured == [[480, 490]]
    assert '480.000-490.000 MHz not measured' in caplog.text
    f, _ = sim.spectrum_arrays(sc)
    assert (f < 480).any() and (f > 490).any()
    assert not ((f > 481) & (f < 489)).any()


# ---------------------------------------------------------------- AUD-05

def test_usb_error_in_the_gain_probe_opens_the_dongle_again(monkeypatch, tmpdir, caplog):
    def probe_fails(self):
        self.sdr.close()                # pyrtlsdr closes the dongle after a library error
        raise IOError('LIBUSB_ERROR_IO (injected in the probe)')
    monkeypatch.setattr(main.Scanner, 'make_gain_plan', probe_fails)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 480., band_gain=True)
    assert sc.error is None, sc.error
    assert sc.spectrum.sample_data.size > 0
    first = sim.FakeRtlSdr.instances[-2]
    assert first is not sdr and first.closed_calls == 0


def test_probe_does_not_touch_a_closed_dongle():
    dev = sim.FakeRtlSdr(faults=[sim.Fault('read_samples', 'close', lambda n, f: n == 3)])
    rx = RtlSdrReceiver(dev)
    with pytest.raises(IOError):
        gp.make_plan(rx, [(470., 560.)], 33.8, [g / 10. for g in dev.get_gains()], 2.4e6)
    assert dev.closed_calls == 0


# ---------------------------------------------------------------- AUD-04

def test_opening_has_a_time_limit(monkeypatch, tmpdir):
    def slow_factory(serial_number=None, device_index=None):
        time.sleep(1.0)
        return sim.FakeRtlSdr(serial_number=serial_number)
    _patch(monkeypatch, tmpdir, slow_factory)
    monkeypatch.setattr(sdrwrapper, 'OPEN_TIMEOUT_S', 0.2)
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    t0 = time.monotonic()
    with pytest.raises(IOError, match='did not answer'):
        sc.sdr_wrapper.open_sdr()
    assert time.monotonic() - t0 < 0.9
    _wait_unstuck()


# ---------------------------------------------------------------- P3-15

def test_step_size_without_a_dongle_is_a_clear_error(monkeypatch, tmpdir):
    _patch(monkeypatch, tmpdir, _factory([]))
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    with pytest.raises(main.ScanFailed, match='not open'):
        sc.step_size
    assert getattr(sc, 'step_size', None) is None


def test_step_size_follows_the_settings(monkeypatch, tmpdir):
    made = []
    _patch(monkeypatch, tmpdir, _factory(made))
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    sc.sdr_wrapper.open_sdr()
    try:
        first = sc.step_size
        assert first == pytest.approx(0.775, abs=0.01)
        n = len(made[-1].calls) and made[-1]._fs
        assert sc.step_size == first and made[-1]._fs == n      # cached
        sc.sampling_config.sample_rate = 1.024e6
        assert sc.step_size < first
        sc.sampling_config.sample_rate = 2.4e6
        sc.sampling_config.sweep_overlap_ratio = 0.5
        assert sc.step_size == pytest.approx(0.6, abs=0.01)
    finally:
        sc.sdr_wrapper.close_sdr()


# ---------------------------------------------------------------- P3-03 / P3-13

_WRITER = r'''
import sys, numpy as np
from wwb_scanner.utils import dbstore
from wwb_scanner.scan_objects import Spectrum
store = dbstore.DBStore()          # the data folder: WWB_SCANNER_DATA
for i in range(int(sys.argv[3])):
    sp = Spectrum(name='%s-%d' % (sys.argv[2], i))
    f = 470. + np.arange(8) * 0.025
    sp.add_sample_set(frequency=f, dbFS=np.full(8, -90.0 - i))
    sp.save_to_dbstore(store)
'''


def test_two_programs_write_one_history(tmpdir):
    """The audit's repro (P3-03): 2 programs x 15 scans. Before: 16 of 30
    records, two scans with one file, PermissionError on Windows."""
    import json
    import os
    import subprocess
    import sys
    script = tmpdir.join('w.py')
    script.write(_WRITER)
    d = str(tmpdir.mkdir('data'))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), WWB_SCANNER_DATA=d)
    procs = [subprocess.Popen([sys.executable, str(script), d, name, '15'], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT) for name in 'ab']
    outs = [p.communicate(timeout=120)[0].decode('utf-8', 'replace') for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    rows = json.load(open(os.path.join(d, 'scan_index.json'), encoding='utf-8'))['scans_performed']
    assert len(rows) == 30
    files = [r['sample_data_file'] for r in rows.values()]
    assert len(set(files)) == 30
    assert len(os.listdir(os.path.join(d, 'scans'))) == 30
    names = sorted(r['name'] for r in rows.values())
    assert names == sorted('%s-%d' % (n, i) for n in 'ab' for i in range(15))
    assert not [f for f in os.listdir(d) if f.endswith('.tmp')]


def test_replace_is_tried_again_while_the_file_is_held(monkeypatch, tmpdir):
    from wwb_scanner.utils import dbstore
    real = os_replace = __import__('os').replace
    calls = []

    def held(src, dst):
        calls.append(dst)
        if len(calls) < 4:
            raise PermissionError(5, 'Access is denied (injected)')
        real(src, dst)
    monkeypatch.setattr(dbstore.os, 'replace', held)
    monkeypatch.setattr(dbstore, 'REPLACE_PAUSE_S', 0.01)
    db = dbstore.JsonDB(str(tmpdir.join('scan_index.json')))
    assert db.table('scans_performed').insert({'name': 'x'}) == 1
    assert len(calls) == 4 and os_replace is real


def test_a_history_that_can_not_be_written_keeps_the_scan(monkeypatch, tmpdir, caplog):
    from wwb_scanner.utils import dbstore

    def never(src, dst):
        raise PermissionError(5, 'Access is denied (injected)')
    monkeypatch.setattr(dbstore, 'REPLACE_TRIES', 2)
    monkeypatch.setattr(dbstore, 'REPLACE_PAUSE_S', 0.01)
    with caplog.at_level(logging.INFO, logger='wwb_scanner.status'):
        sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 476., sdr_kwargs={})
        assert sc.error is None
        monkeypatch.setattr(dbstore.os, 'replace', never)
        assert sc.save_to_dbstore() is None
    assert isinstance(sc.history_error, PermissionError)
    assert 'not saved to the history' in caplog.text
    assert sc.spectrum.sample_data.size > 0


def test_save_returns_the_eid(monkeypatch, tmpdir):
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 476.)
    eid = sc.save_to_dbstore()
    assert isinstance(eid, int) and eid == sc.spectrum.eid


def test_a_record_with_a_key_that_is_not_a_number_is_left_out(tmpdir):
    from wwb_scanner.utils import dbstore
    p = tmpdir.join('scan_index.json')
    p.write('{"scans_performed": {"x": {"name": "bad"}, "2": {"name": "good"}, "3": 5}}')
    db = dbstore.JsonDB(str(p))
    t = db.table('scans_performed')
    assert [(dbstore.doc_id(d), d['name']) for d in t.all()] == [(2, 'good')]
    assert t.insert({'name': 'new'}) == 3


def test_lock_between_programs_times_out(monkeypatch, tmpdir):
    import subprocess
    import sys
    from wwb_scanner.utils import dbstore
    path = str(tmpdir.join('scan_index.json.lock'))
    holder = subprocess.Popen([sys.executable, '-c', (
        'import sys,time;sys.path[:0]=%r\n'
        'from wwb_scanner.utils import dbstore\n'
        'l=dbstore.FileLock(%r);l.acquire();print("held",flush=True);time.sleep(3)') % (sys.path, path)],
        stdout=subprocess.PIPE)
    assert holder.stdout.readline().strip() == b'held'
    try:
        t0 = time.monotonic()
        with pytest.raises(dbstore.HistoryBusy):
            dbstore.FileLock(path).acquire(timeout=0.3)
        assert time.monotonic() - t0 < 2
    finally:
        holder.wait(10)
    with dbstore.FileLock(path):
        pass


# ---------------------------------------------------------------- P2-GAIN1 / RATE1 / FCORR1 / P3-14

@pytest.mark.parametrize('field,value,text', [
    ('gain', float('nan'), 'gain must be'), ('gain', 100., 'gain must be'), ('gain', -3., 'gain must be'),
    ('gain', 'x', 'gain must be'),
    ('sample_rate', 8e6, '900-3200'), ('sample_rate', 500e3, '900-3200'), ('sample_rate', 100e3, '900-3200'),
    ('freq_correction', 100000, 'whole number of ppm'), ('freq_correction', 2.5, 'whole number of ppm'),
    ('freq_correction', float('nan'), 'whole number of ppm'),
])
def test_settings_the_dongle_can_not_take(field, value, text):
    conf = ScanConfig(sim.make_config(470, 476))
    (conf.sampling if field == 'sample_rate' else conf.device)[field] = value
    assert text in (conf.device_problem() or '')
    assert text in (conf.check() or '')


@pytest.mark.parametrize('field,value', [('gain', 0.), ('gain', 49.6), ('gain', 33.8), ('gain', None),
                                         ('sample_rate', 2.4e6), ('sample_rate', 250e3),
                                         ('sample_rate', 3.2e6), ('freq_correction', -1),
                                         ('freq_correction', 60), ('freq_correction', None)])
def test_settings_the_dongle_takes(field, value):
    conf = ScanConfig(sim.make_config(470, 476))
    (conf.sampling if field == 'sample_rate' else conf.device)[field] = value
    assert conf.device_problem() is None


def test_a_bad_gain_stops_the_scan_before_the_dongle(monkeypatch, tmpdir):
    made = []
    _patch(monkeypatch, tmpdir, _factory(made))
    conf = sim.make_config(470, 476)
    conf['device']['gain'] = float('nan')
    sc = main.Scanner(config=conf, history=None)
    sc.run_scan()
    assert isinstance(sc.error, main.ScanSettingsError) and 'gain' in str(sc.error)
    assert not made


def test_sample_rate_above_2_56_is_a_warning(monkeypatch, tmpdir, caplog):
    with caplog.at_level(logging.INFO, logger='wwb_scanner.status'):
        sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 480., fs=3.2e6)
    assert sc.error is None and sc.spectrum.sample_data.size
    assert 'above 2.56 MS/s' in caplog.text
    assert ScanConfig(sim.make_config(470, 480)).warnings() == []


def test_window_notes_a_rate_above_2560():
    import os
    import wwb_scanner
    pkg = os.path.dirname(wwb_scanner.__file__)
    text = open(os.path.join(pkg, 'ui', 'pyside', 'qml', 'DeviceConfigPopup.qml'), encoding='utf-8').read()
    assert "v > 2560" in text


# ---------------------------------------------------------------- import (P3-04..07, P3-12, P3-18)

def _import(path):
    from wwb_scanner.file_handlers import importers
    return importers.BaseImporter.import_file(str(path))


def _sdb3_header_file(path, end_khz):
    import json
    head = {'BitWidth': 16, 'Scale Factor': 10, 'NoDataValue': -1400, 'FreqUnits': 'KHz',
            'BinarySchema': [{'Curve': {'FreqRanges': [{'StartFreq': 470000, 'EndFreq': end_khz,
                                                         'StepFreq': 1000}]}}]}
    path.write_binary(b'//@ShureScan' + json.dumps(head).encode() + b'@Binary:' + b'\x00\x00' * 8)
    return path


def test_sdb3_with_a_huge_grid_is_refused_at_once(tmpdir):
    p = _sdb3_header_file(tmpdir.join('huge.sdb3'), 2e12)
    t0 = time.monotonic()
    with pytest.raises(ValueError, match='too many points'):
        _import(p)
    assert time.monotonic() - t0 < 1.


@pytest.mark.parametrize('step', [0, -25, float('nan')])
def test_sdb3_with_a_bad_step_is_refused(tmpdir, step):
    import json
    head = {'BitWidth': 16, 'BinarySchema': [{'Curve': {'FreqRanges': [
        {'StartFreq': 470000, 'EndFreq': 471000, 'StepFreq': step}]}}]}
    p = tmpdir.join('bad.sdb3')
    p.write_binary(b'//@ShureScan' + json.dumps(head).encode() + b'@Binary:' + b'\x00' * 100)
    with pytest.raises(ValueError):
        _import(p)


def _npy_bomb_bytes():
    import io
    from numpy.lib import format as fmt
    buf = io.BytesIO()
    fmt.write_array_header_1_0(buf, {'descr': '<f8', 'fortran_order': False, 'shape': (10 ** 12,)})
    return buf.getvalue()


def test_npz_bomb_is_refused_before_it_is_loaded(tmpdir):
    import zipfile
    p = tmpdir.join('bomb.npz')
    with zipfile.ZipFile(str(p), 'w') as z:
        z.writestr('sample_data.npy', _npy_bomb_bytes())
    t0 = time.monotonic()
    with pytest.raises(ValueError, match='too large'):
        _import(p)
    assert time.monotonic() - t0 < 1.


def test_stored_array_bomb_in_json_is_refused():
    import base64
    from wwb_scanner.utils import numpyjson
    text = '{"a": {"__npy__": "%s"}}' % base64.b64encode(_npy_bomb_bytes()).decode()
    with pytest.raises(ValueError, match='too large'):
        numpyjson.loads(text)


def test_npz_problems_are_one_clear_error(tmpdir):
    import numpy as np
    other = tmpdir.join('other.npz')
    np.savez(str(other), something=np.arange(3))
    notzip = tmpdir.join('text.npz')
    notzip.write('not a zip file')
    objs = tmpdir.join('objs.npz')
    np.savez(str(objs), sample_data=np.array([{'a': 1}], dtype=object))
    for p, text in ((other, "no 'sample_data'"), (notzip, 'not an .npz'), (objs, 'Python objects')):
        with pytest.raises(ValueError) as e:
            _import(p)
        assert text in str(e.value) and p.basename in str(e.value)


def test_our_npz_still_imports(monkeypatch, tmpdir):
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 476.)
    fn = str(tmpdir.join('scan.npz'))
    sc.spectrum.export_to_file(filename=fn)
    back = _import(fn)
    assert back.sample_data.size == sc.spectrum.sample_data.size


def test_csv_nan_and_inf_are_not_levels(tmpdir):
    import numpy as np
    p = tmpdir.join('nan.csv')
    p.write('470000,-90\n470025,nan\n470050,inf\n470075,-inf\nnan,-80\n470100,-91\n470125,-92\n')
    sp_ = _import(p)
    y = np.asarray(sp_.sample_data['dbFS'], float)
    assert np.isfinite(y).all() and y.size == 3


def test_utf16_sdb2_with_a_dtd_is_refused(tmpdir):
    xml = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE s [<!ENTITY f "470">]>'
           '<scan_data_source><data_sets><freq_set><f>&f;000</f><f>470025</f></freq_set>'
           '<data_set><v>-90</v><v>-91</v></data_set></data_sets></scan_data_source>')
    p = tmpdir.join('dtd16.sdb2')
    p.write_binary(xml.encode('utf-16'))
    with pytest.raises(ValueError, match='DTD'):
        _import(p)


def test_real_wwb_sdb2_still_imports():
    import os
    import numpy as np
    sp_ = _import(os.path.join(os.path.dirname(__file__), 'data', '572-636.sdb2'))
    f = np.asarray(sp_.sample_data['frequency'], float)
    assert f.min() == pytest.approx(572.0) and f.size > 1000


def test_sdb2_gap_stays_a_gap(tmpdir):
    import numpy as np
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.scan_objects.samplearray import SampleArray
    from wwb_scanner.file_handlers import exporters
    f = np.concatenate([np.arange(470.0, 471.0 + 1e-9, 0.025), np.arange(475.0, 476.0 + 1e-9, 0.025)])
    sp_ = Spectrum(step_size=0.025)
    sp_.sample_data = SampleArray.create(frequency=f, dbFS=np.full(f.size, -95.))
    fn = str(tmpdir.join('gap.sdb2'))
    exporters.WWBExporter(spectrum=sp_, filename=fn)()
    back = _import(fn)
    fb = np.asarray(back.sample_data['frequency'], float)
    yb = np.asarray(back.sample_data['dbFS'], float)
    assert not ((fb > 471.05) & (fb < 474.95)).any() and np.allclose(yb, -95.)


# ---------------------------------------------------------------- P2-DATE1

def test_export_carries_the_time_of_the_scan(tmpdir):
    import datetime
    import re
    import numpy as np
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.scan_objects.samplearray import SampleArray
    from wwb_scanner.file_handlers import exporters
    when = datetime.datetime(2026, 5, 3, 21, 15, 7)
    sp_ = Spectrum(step_size=0.025, timestamp_utc=when.timestamp())
    f = np.arange(470.0, 471.0 + 1e-9, 0.025)
    sp_.sample_data = SampleArray.create(frequency=f, dbFS=np.full(f.size, -95.))
    fn = str(tmpdir.join('t.sdb2'))
    exporters.WWBExporter(spectrum=sp_, filename=fn)()
    text = open(fn, encoding='utf-8').read()
    assert 'date="Sun May 3 2026"' in text and 'time="21:15:07.000"' in text
    ms = int(re.search(r'date_time="(\d+)"', text).group(1))
    assert ms == int(when.timestamp() * 1000)


# ---------------------------------------------------------------- AUD-03 / P3-09

def test_open_a_scan_of_the_history_by_its_record(monkeypatch, tmpdir):
    import numpy as np
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.utils import dbstore
    monkeypatch.setenv('WWB_SCANNER_DATA', str(tmpdir.join('data')))
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', None)
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', None)
    store = dbstore.DBStore()
    monkeypatch.setattr(dbstore, 'db_store', store)
    for i in range(3):
        sp_ = Spectrum(name='scan %d' % (i + 1))
        f = 470. + i + np.arange(8) * 0.025
        sp_.add_sample_set(frequency=f, dbFS=np.full(8, -90.))
        assert sp_.save_to_dbstore(store) == i + 1
    recs = store.get_all_scans()
    got = Spectrum.from_dbstore(dbdata=recs[3])
    assert got.eid == 3 and got.name == 'scan 3'
    assert float(np.min(got.sample_data['frequency'])) == pytest.approx(472.0)
    by_eid = Spectrum.from_dbstore(eid=2)
    assert by_eid.eid == 2 and by_eid.name == 'scan 2'
    with pytest.raises(ValueError):
        Spectrum.from_dbstore()
    with pytest.raises(ValueError):
        Spectrum.from_dbstore(eid=99)
    assert store.get_scan(None) is None


# ---------------------------------------------------------------- AUD-19

def test_time_report_in_the_order_of_time():
    from wwb_scanner.scanner.scan_timing import ScanTiming, STAGES
    t = ScanTiming()
    base = 1000.
    # tile 470 read again last (after 480), as retry_failed_tiles does
    for key, t0 in ((480.0, 0.), (490.0, 1.), (470.0, 5.)):
        t.tiles[key] = {s: base + t0 + 0.1 * i for i, s in enumerate(STAGES)}
    rep = t.build_report()
    wall = float(rep.split('last tile end: ')[1].split('\n')[0])
    assert wall == pytest.approx(5.5, abs=0.01)
    import re
    assert not re.search(r'-\d', rep), rep     # no negative mean or share


def test_one_time_report_for_all_bands(monkeypatch, tmpdir):
    monkeypatch.setattr(main.Scanner, 'make_gain_plan', _three_band_plan)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 500., band_gain=True)
    assert sc.error is None
    rep = sc.sample_collection.timing.build_report()
    n = int(rep.split('tiles measured: ')[1].split('\n')[0])
    assert n >= 3 * 10, rep               # every band, not only the last one


# ---------------------------------------------------------------- AUD-27 / AUD-28 / P3-20

def test_table_model_roles_without_qvariant():
    pytest.importorskip('PySide6')
    from wwb_scanner.ui.pyside.qt import QtCore
    from wwb_scanner.ui.pyside.graph import GraphTableModel
    m = GraphTableModel()
    idx = m.index(0, 0)
    assert m.data(idx, QtCore.Qt.ToolTipRole) is None
    assert m.data(idx, QtCore.Qt.TextAlignmentRole) is None


def _package_files(sub=''):
    import os
    import wwb_scanner
    root = os.path.join(os.path.dirname(os.path.abspath(wwb_scanner.__file__)), sub)
    for d, _, files in os.walk(root):
        for f in files:
            if f.endswith('.py'):
                yield os.path.join(d, f)


def test_no_print_in_the_window_code():
    import re
    bad = []
    for p in _package_files('ui/pyside'):
        for n, line in enumerate(open(p, encoding='utf-8'), 1):
            if re.match(r'\s*print\(', line):
                bad.append('%s:%d' % (p, n))
    assert not bad


def test_text_files_are_opened_as_utf8():
    import re
    bad = []
    for p in _package_files():
        for n, line in enumerate(open(p, encoding='utf-8'), 1):
            m = re.search(r"\bopen\(([^)]*)\)", line)
            if not m or 'encoding' in line or "'rb'" in line or "'wb'" in line or 'mode' in m.group(1):
                continue
            if re.search(r"open\(\s*(self\.filename|filename|path|tmp|probe|fn|os\.devnull)", line) and \
                    'b' not in re.sub(r"[^']*'([^']*)'.*", r'\1', m.group(1)):
                bad.append('%s:%d: %s' % (p, n, line.strip()))
    assert not bad, bad


def test_package_version():
    import configparser
    import os
    import wwb_scanner
    v = wwb_scanner.__version__
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(wwb_scanner.__file__))), 'setup.cfg')
    if os.path.exists(cfg) and v != 'unknown':
        c = configparser.ConfigParser()
        c.read(cfg, encoding='utf-8')
        assert v == c['metadata']['version']
    with pytest.raises(AttributeError):
        wwb_scanner.no_such_thing


# ---------------------------------------------------------------- F4 / H6 (research, round 2)

def test_offset_tuning_is_never_used():
    """F4: in the RTL-SDR Blog library rtlsdr_set_offset_tuning switches the
    bias tee on the V4 (power on the antenna input)."""
    bad = [p for p in _package_files() if 'offset_tuning' in open(p, encoding='utf-8').read()]
    assert not bad


@pytest.mark.parametrize('ext', ['csv', 'sdb2', 'sdb3'])
def test_export_scale_10_db_stays_10_db(tmpdir, ext):
    """H6: a level 10 dB higher in the scan is 10.0 +- 0.1 dB higher in the file."""
    import numpy as np
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.scan_objects.samplearray import SampleArray
    f = np.arange(470.0, 480.0 + 1e-9, 0.025)
    y = np.full(f.size, -100.)
    y[(f > 474) & (f < 476)] = -90.
    sp_ = Spectrum(step_size=0.025)
    sp_.sample_data = SampleArray.create(frequency=f, dbFS=y)
    fn = str(tmpdir.join('scale.' + ext))
    sp_.export_to_file(filename=fn)
    back = _import(fn)
    fb = np.asarray(back.sample_data['frequency'], float)
    yb = np.asarray(back.sample_data['dbFS'], float)
    hi = np.median(yb[(fb > 474.2) & (fb < 475.8)])
    lo = np.median(yb[(fb < 473) | (fb > 477)])
    assert hi - lo == pytest.approx(10., abs=0.1)
