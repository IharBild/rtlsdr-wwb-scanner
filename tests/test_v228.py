"""V22.8: reliability (external audit of 22.11.2026, sections A and B).

A1  open_sdr: a dongle whose set-up failed stayed open and the next open
    waited for ever (also the reopen after a USB error).
A3  sdb3: a gap was written as -140 (a level), not as NoDataValue.
A5  closing the window during a scan: the scan is stopped and waited for.
A6  Stop while the dongle is being opened was lost.
A7  watchdog: a scan that stops moving leaves the thread stacks in the log.
B1  marks of a scan and its raw values come back from the history.
B2  get_gains leaves the dongle settings on and an open dongle open; a gain
    that does not come back after the auto gain stops the scan with a reason.
B3  a dongle is not closed while a USB call on it hangs.
B4  history: points file first, then the index record.
B5  peak check limited by time, not by 30 tiles.
B7  0.5-1766 MHz (V4 datasheet); a scan without a point is an error; a gap is a warning.
    The timing report goes next to the history of the scan.
"""
import json
import logging
import os
import struct
import threading
import time

import numpy as np
import pytest

import sim
from wwb_scanner.scanner import main, sdrwrapper, sample_processing as sp, watchdog
from wwb_scanner.scanner.config import ScanConfig


def _patch(monkeypatch, tmpdir, factory):
    """sim.run_scan's set-up with an own dongle factory."""
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


def _run(scanner, timeout=60):
    th = threading.Thread(target=scanner.run_scan, daemon=True)
    th.start()
    th.join(timeout)
    assert not th.is_alive(), 'scan did not end in %s s' % timeout
    return scanner


def _wait_unstuck(limit=15.):
    end = time.monotonic() + limit
    while sp.stuck_usb_calls() and time.monotonic() < end:
        time.sleep(0.05)
    assert not sp.stuck_usb_calls()


class _SetupFails(sim.FakeRtlSdr):
    """Setting the sample rate fails (pyrtlsdr: LibUSBError -22 at 8 MS/s)."""
    @sim.FakeRtlSdr.sample_rate.setter
    def sample_rate(self, v):
        raise IOError('LIBUSB_ERROR_INVALID_PARAM (injected)')


# ------------------------------------------------------------------------ A1

def test_failed_setup_closes_the_dongle_and_the_next_open_does_not_hang(monkeypatch, tmpdir):
    made = []
    def factory(serial_number=None, device_index=None):
        s = (_SetupFails if not made else sim.FakeRtlSdr)(serial_number=serial_number)
        made.append(s)
        return s
    _patch(monkeypatch, tmpdir, factory)
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    w = sc.sdr_wrapper
    with pytest.raises(IOError):
        w.open_sdr()
    assert w.sdr is None and not w.device_open.is_set()
    assert made[0].device_opened is False           # closed, not left open
    box = {}
    th = threading.Thread(target=lambda: box.setdefault('sdr', w.open_sdr()), daemon=True)
    th.start()
    th.join(5)
    assert not th.is_alive(), 'the second open waited for ever (V22.7)'
    assert box['sdr'] is made[1] and w.device_open.is_set()
    w.close_sdr()
    assert made[1].device_opened is False


def test_scan_with_a_failing_setup_ends_with_an_error(monkeypatch, tmpdir):
    made = []
    def factory(serial_number=None, device_index=None):
        made.append(_SetupFails(serial_number=serial_number))
        return made[-1]
    _patch(monkeypatch, tmpdir, factory)
    sc = _run(main.Scanner(config=sim.make_config(470, 476), history=None), timeout=20)
    assert sc.error is not None and 'could not be set up' in str(sc.error)
    assert made and all(not s.device_opened for s in made)
    assert len(made) == 1                           # not tried again for 10 s
    assert sc._stopped.is_set() and not sc._running.is_set()


def test_reopen_after_a_usb_error_survives_a_failed_setup(monkeypatch, tmpdir):
    """The reopen loop of a scan: the first new handle fails its set-up,
    the next one works and the scan goes on (V22.7: the loop hung)."""
    monkeypatch.setattr(sp, 'REOPEN_PAUSE_S', 0.05)
    made = []
    def factory(serial_number=None, device_index=None):
        n = len(made)
        if n == 0:
            s = sim.FakeRtlSdr(serial_number=serial_number,
                               faults=[sim.Fault('read_samples', 'close', lambda c, f: c == 6)])
        elif n == 1:
            s = _SetupFails(serial_number=serial_number)
        else:
            s = sim.FakeRtlSdr(serial_number=serial_number)
        made.append(s)
        return s
    _patch(monkeypatch, tmpdir, factory)
    sc = _run(main.Scanner(config=sim.make_config(470, 480), history=None), timeout=30)
    coll = sc.sample_collection
    assert sc.error is None and not coll.aborted
    assert len(made) == 3 and made[1].device_opened is False
    assert coll.missing_tiles == [] and sc.spectrum.sample_data.size > 0


# ------------------------------------------------------------------------ A6

class _SlowOpen(sim.FakeRtlSdr):
    def __init__(self, *a, **k):
        time.sleep(0.8)                              # opening takes ~1.6 s on the PC
        super(_SlowOpen, self).__init__(*a, **k)


def test_stop_while_the_dongle_opens_is_kept(monkeypatch, tmpdir, caplog):
    made = []
    def factory(serial_number=None, device_index=None):
        made.append(_SlowOpen(serial_number=serial_number))
        return made[-1]
    _patch(monkeypatch, tmpdir, factory)
    caplog.set_level(logging.INFO, logger='wwb_scanner.status')
    sc = main.Scanner(config=sim.make_config(470, 480), history=None)
    th = threading.Thread(target=sc.run_scan, daemon=True)
    th.start()
    time.sleep(0.3)
    assert not sc._running.is_set()
    assert sc.request_stop() is True                # V22.7: False, and the scan ran to the end
    th.join(20)
    assert not th.is_alive()
    assert sc.stop_requested and sc.stopped_early and sc.error is None
    assert made[0].calls['read_samples'] == 0 and sc.spectrum.sample_data.size == 0
    assert made[0].device_opened is False
    assert any('stopped before the first tile' in r.getMessage() for r in caplog.records)


def test_service_stop_during_open(monkeypatch, tmpdir):
    from wwb_scanner import service
    made = []
    def factory(serial_number=None, device_index=None):
        made.append(_SlowOpen(serial_number=serial_number))
        return made[-1]
    _patch(monkeypatch, tmpdir, factory)
    svc = service.ScanService(history=None)
    svc.start(ScanConfig(sim.make_config(470, 480)))
    time.sleep(0.2)
    t0 = time.monotonic()
    assert svc.stop() is True
    assert time.monotonic() - t0 < 0.5             # returns at once (waited for the open before)
    res = svc.wait(20)
    assert res.stopped_by_user and res.points == 0 and made[0].calls['read_samples'] == 0


def test_stop_of_a_scanner_never_started_does_not_wait():
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    assert sc.request_stop() is False
    t0 = time.monotonic()
    sc.stop_scan()
    assert time.monotonic() - t0 < 0.5 and not sc.stop_requested


def test_one_scanner_run_twice_starts_clean(monkeypatch, tmpdir):
    _patch(monkeypatch, tmpdir, lambda serial_number=None, device_index=None:
           sim.FakeRtlSdr(serial_number=serial_number))
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    _run(sc)
    assert sc.request_stop() is False               # finished: nothing to stop
    n1 = sc.spectrum.sample_data.size
    _run(sc)
    assert not sc.stop_requested and sc.error is None and sc.spectrum.sample_data.size == n1


# ------------------------------------------------------------------------ A7

def test_watchdog_writes_the_stacks_once_per_stall(caplog):
    caplog.set_level(logging.WARNING, logger='wwb_scanner.watchdog')
    dog = watchdog.Watchdog('test', stall_s=0.3, check_s=0.05).start()
    try:
        time.sleep(0.7)
        assert dog.dumps == 1
        watchdog.beat()
        time.sleep(0.7)
        assert dog.dumps == 2
    finally:
        dog.stop()
    msgs = [r.getMessage() for r in caplog.records if r.name == 'wwb_scanner.watchdog']
    assert len(msgs) == 2
    first = msgs[0].split('\n')[0]
    assert 'has not moved' in first and 'log file' in first
    assert '--- thread MainThread' in msgs[0] and 'test_watchdog_writes_the_stacks' in msgs[0]


def test_watchdog_shows_where_a_scan_hangs(monkeypatch, tmpdir, caplog):
    """A read that hangs (shorter than its timeout): the stack of the USB
    call is in the log, the scan itself is not changed."""
    monkeypatch.setattr(watchdog, 'STALL_S', 0.4)
    monkeypatch.setattr(watchdog, 'CHECK_S', 0.05)
    caplog.set_level(logging.WARNING, logger='wwb_scanner.watchdog')
    fault = sim.Fault('read_samples', 'hang', lambda c, f: c == 3, hang_s=1.2)
    _patch(monkeypatch, tmpdir, lambda serial_number=None, device_index=None:
           sim.FakeRtlSdr(serial_number=serial_number, faults=[fault]))
    sc = _run(main.Scanner(config=sim.make_config(470, 476), history=None))
    assert sc.error is None and fault.hits == 1
    text = '\n'.join(r.getMessage() for r in caplog.records if r.name == 'wwb_scanner.watchdog')
    assert 'has not moved' in text and 'wwb-usb-call' in text and 'read_samples' in text
    th = [t for t in threading.enumerate() if t.name == 'wwb-watchdog']
    assert not th                                    # stopped with the scan


# ------------------------------------------------------------------------ B3

def test_dongle_is_not_closed_while_a_usb_call_hangs(monkeypatch, tmpdir):
    monkeypatch.setattr(sdrwrapper, '_ABANDONED', [])
    fault = sim.Fault('read_samples', 'hang', lambda c, f: c == 3, hang_s=2.0)
    made = []
    def factory(serial_number=None, device_index=None):
        made.append(sim.FakeRtlSdr(serial_number=serial_number, faults=[fault]))
        return made[-1]
    _patch(monkeypatch, tmpdir, factory)
    monkeypatch.setattr(sp, 'READ_TIMEOUT', 0.4)
    try:
        sc = _run(main.Scanner(config=sim.make_config(470, 480), history=None))
        assert sc.sample_collection.aborted
        assert made[0].device_opened is True        # V22.7: closed under the hanging call
        assert sdrwrapper._ABANDONED == [made[0]] and sc.sdr_wrapper.sdr is None
    finally:
        _wait_unstuck()


# ------------------------------------------------------------------------ B2

def test_get_gains_turns_the_settings_back_on_after_an_error(monkeypatch, tmpdir):
    class NoGains(sim.FakeRtlSdr):
        def get_gains(self):
            raise IOError('LIBUSB_ERROR_PIPE (injected)')
    _patch(monkeypatch, tmpdir, lambda serial_number=None, device_index=None: NoGains())
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    with pytest.raises(IOError):
        sc.get_gains()
    assert sc.sdr_wrapper.enable_scanner_updates is True
    assert sc.sdr_wrapper.sdr is None


def test_get_gains_does_not_close_the_open_dongle(monkeypatch, tmpdir):
    _patch(monkeypatch, tmpdir, lambda serial_number=None, device_index=None: sim.FakeRtlSdr())
    sc = main.Scanner(config=sim.make_config(470, 476), history=None)
    dev = sc.sdr_wrapper.open_sdr()
    assert sc.get_gains()
    assert sc.sdr_wrapper.sdr is dev and dev.device_opened
    sc.sdr_wrapper.close_sdr()


class _GainStuckLow(sim.FakeRtlSdr):
    """After the auto gain lowered the gain, setting it back fails."""
    @sim.FakeRtlSdr.gain.setter
    def gain(self, v):
        if self.gains_set and min(self.gains_set) < 30. and float(v) > 30.:
            raise IOError('LIBUSB_ERROR_PIPE (injected, gain back)')
        sim.FakeRtlSdr.gain.fset(self, v)


def test_gain_not_set_back_after_the_auto_gain_stops_the_scan(monkeypatch, tmpdir, caplog):
    caplog.set_level(logging.INFO, logger='wwb_scanner')
    air = sim.Air(noise=0.01).tone(475.0e6, 3.0)            # clips the tiles around 475 MHz
    made = []
    def factory(serial_number=None, device_index=None):
        made.append(_GainStuckLow(serial_number=serial_number, air=air, gain_affects=True))
        return made[-1]
    _patch(monkeypatch, tmpdir, factory)
    sc = _run(main.Scanner(config=sim.make_config(470, 490, auto_gain=True), history=None))
    coll = sc.sample_collection
    assert coll.aborted and 'could not be set back to 33.8 dB' in coll.gain_lost
    assert 'auto gain' in coll.abort_reason
    assert sc.spectrum.sample_data.size > 0                 # the part before is kept
    status = [r for r in caplog.records if r.name == 'wwb_scanner.status']
    assert any(r.levelno == logging.WARNING and 'could not be set back' in r.getMessage() for r in status)
    # no tile after the failure was read with the low gain as if normal
    assert max(coll.processed_keys) < 490e6 - 2e6


# ------------------------------------------------------------------------ A3

def _sdb3_values(data):
    i = data.index(b'@Binary:') + len(b'@Binary:')
    header = json.loads(data[len(b'//@ShureScan'):data.index(b'@Binary:')].decode('utf-8'))
    j = data.rindex(b'@Extended:')
    rec = data[i:j]
    n = (len(rec) - 12 - 2) // 2
    vals = np.frombuffer(rec[12:12 + 2 * n], dtype='>i2')
    crc = struct.unpack('>H', rec[12 + 2 * n:])[0]
    return header, vals, crc, rec[:12 + 2 * n]


def test_sdb3_gap_is_written_as_no_data_value(tmpdir):
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.file_handlers import exporters, importers
    sp_ = Spectrum(step_size=0.025)
    f = np.concatenate([np.arange(470.0, 480.0 + 1e-9, 0.025), np.arange(490.0, 500.0 + 1e-9, 0.025)])
    y = np.full(f.size, -100.)
    from wwb_scanner.scan_objects.samplearray import SampleArray
    sp_.sample_data = SampleArray.create(frequency=f, dbFS=y)
    fn = str(tmpdir.join('gap.sdb3'))
    exporters.WWB3Exporter(spectrum=sp_, filename=fn)()
    data = open(fn, 'rb').read()
    header, vals, crc, body = _sdb3_values(data)
    assert header['NoDataValue'] == -1400 and header['Scale Factor'] == 1
    grid = np.arange(header['BinarySchema'][3]['Curve']['FreqRanges'][0]['StartFreq'],
                     header['BinarySchema'][3]['Curve']['FreqRanges'][0]['EndFreq'] + 1, 25)
    assert grid.size == vals.size
    gap = (grid > 480025) & (grid < 489975)
    assert gap.sum() > 300
    assert (vals[gap] == -1400).all()                       # V22.7: -140 (a level)
    assert (vals[~gap] == -100).all()
    assert crc == exporters.crc16_arc(body)
    back = importers.BaseImporter.import_file(fn)
    yb = np.asarray(back.sample_data['dbFS'], float)
    fb = np.asarray(back.sample_data['frequency'], float)
    # V22.9 (audit P3-18): the gap stays a gap when read back (was -140)
    assert yb.size == (~gap).sum() and np.allclose(yb, -100.)
    assert not ((fb > 480.05) & (fb < 489.95)).any()


# ------------------------------------------------------------------------ B1, B4

def _store(tmpdir):
    from wwb_scanner.utils import dbstore
    st = dbstore.DBStore()
    st.DB_PATH = str(tmpdir.join('h', 'db.json'))
    st.SCAN_DB_PATH = str(tmpdir.join('h', 'scan_index.json'))
    return st


def _spectrum():
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.scan_objects.samplearray import SampleArray
    s = Spectrum(name='t', step_size=0.025)
    f = np.arange(470.0, 471.0 + 1e-9, 0.025)
    s.sample_data = SampleArray.create(frequency=f, dbFS=np.full(f.size, -100.))
    s.raw_dbFS = np.full(f.size, -97.5)
    s.image_marks = [{'f': 470.2, 'of': 472.6, 'hidden': False}]
    s.product_marks = [{'f': 470.5, 'delta': 8.0, 'drop': 9.6}]
    s.loss_bands = [{'lo': 470.6, 'hi': 470.9, 'db': 12.0, 'signal': 470.75}]
    s.unmeasurable_bands = [{'lo': 470.7, 'hi': 470.8, 'clip': 0.02}]
    return s


def test_marks_and_raw_values_come_back_from_the_history(tmpdir):
    from wwb_scanner.scan_objects import Spectrum
    st = _store(tmpdir)
    s = _spectrum()
    eid = st.add_scan(s)
    back = Spectrum.from_json(st.get_scan(eid), eid=eid)
    for k in ('image_marks', 'product_marks', 'loss_bands', 'unmeasurable_bands'):
        assert getattr(back, k) == getattr(s, k), k
    assert np.allclose(back.raw_dbFS, -97.5) and back.raw_dbFS.size == back.sample_data.size


def test_points_file_is_written_before_the_index_record(tmpdir, monkeypatch):
    st = _store(tmpdir)
    def broken(*a, **k):
        raise OSError('disk full (injected)')
    monkeypatch.setattr(st, '_write_points', broken)
    with pytest.raises(OSError):
        st.add_scan(_spectrum())
    assert st.scan_db.table('scans_performed').all() == []   # V22.7: a record without points
    monkeypatch.undo()
    st2 = _store(tmpdir)
    s = _spectrum()
    eid = st2.add_scan(s)
    rec = st2.scan_db.table('scans_performed').get(doc_id=eid)
    assert eid == 1 and os.path.exists(os.path.join(st2.scans_dir, rec['sample_data_file']))


def test_new_points_file_never_replaces_another(tmpdir):
    st = _store(tmpdir)
    s = _spectrum()
    eid = st.add_scan(s)
    name = st.scan_db.table('scans_performed').get(doc_id=eid)['sample_data_file']
    # a damaged index was replaced: numbers start at 1 again, same second
    os.remove(st.scan_db_path)
    st._scan_db = None
    s2 = _spectrum()
    s2.timestamp_utc = s.timestamp_utc
    eid2 = st.add_scan(s2)
    name2 = st.scan_db.table('scans_performed').get(doc_id=eid2)['sample_data_file']
    assert eid2 == 1 and name2 != name
    assert os.path.exists(os.path.join(st.scans_dir, name))


# ------------------------------------------------------------------------ B5

def test_peak_check_budget_follows_the_scan_time():
    coll = sp.SampleCollection(scanner=None)
    coll.t_scan_start = time.monotonic() - 100.
    coll.processed_keys = set(range(1000))           # 0.1 s per tile
    budget, per_tile = coll._verify_budget()
    assert budget == pytest.approx(15., abs=0.1) and per_tile == pytest.approx(0.3, abs=0.01)
    assert abs(int(budget / per_tile) - 50) <= 1     # V22.7: 30 tiles whatever the time
    coll.t_scan_start = time.monotonic() - 1000.
    assert coll._verify_budget()[0] == sp.VERIFY_MAX_BUDGET_S
    coll.t_scan_start = time.monotonic() - 5.
    assert coll._verify_budget()[0] == sp.VERIFY_MIN_BUDGET_S


def test_peak_check_stops_at_its_time_limit(monkeypatch, tmpdir):
    monkeypatch.setattr(sp, 'VERIFY_MIN_BUDGET_S', 0.4)
    monkeypatch.setattr(sp, 'VERIFY_MAX_BUDGET_S', 0.4)
    monkeypatch.setattr(sp, 'VERIFY_MIN_TILES', 20)      # more tiles than the time allows
    a = sim.Air(noise=0.01)
    for m in (600.0, 600.4, 603.0, 603.5):
        a.tone(m * 1e6, 0.12)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 596., 608., sdr_kwargs=dict(air=a, read_delay=0.05, gain_affects=True,
                                                                      lna=sim.Lna(k3=1.0)),
                              verify_peaks=True)
    coll = sc.sample_collection
    assert coll.peak_check_late and coll.peak_check_skipped > 0
    assert 'not checked (time limit 0 s)' in coll.overload_note
    assert coll.peak_check_seconds < 0.4 * 1.5 + 1.


# ------------------------------------------------------------------------ B7

def test_frequency_limits_of_the_dongle():
    # RTL-SDR Blog V4 datasheet: 500 kHz to 1.766 GHz (HF through its upconverter)
    for rng in ([0.5, 1766.], [24., 1766.], [1., 30.]):
        assert ScanConfig({'scan_range': rng}).check() is None, rng
    for rng in ([0., 10.], [-100., 470.], [470., 2000.], [0.4, 30.], [1700., 1766.1]):
        why = ScanConfig({'scan_range': rng}).check()
        assert why and '0.5-1766 MHz' in why, rng
    bands = ScanConfig({'scan_range': [470., 1800.], 'scan_ranges': [[470., 500.], [1700., 1800.]]})
    assert '1700-1800' in bands.check()


def test_service_checks_a_list_of_bands_too():
    from wwb_scanner import service
    conf = ScanConfig({'scan_range': [470., 1800.], 'scan_ranges': [[470., 500.], [1700., 1800.]]})
    assert '0.5-1766 MHz' in service.start_problem(conf)
    assert service.start_problem(conf, service.FLOOR_CALIBRATION) == ''


def test_scanner_refuses_a_range_outside_without_opening_the_dongle(monkeypatch, tmpdir):
    made = []
    _patch(monkeypatch, tmpdir, lambda serial_number=None, device_index=None: made.append(1))
    sc = _run(main.Scanner(config=sim.make_config(1700, 1900), history=None))
    assert sc.error is not None and '0.5-1766' in str(sc.error) and not made
    assert isinstance(sc.error, ValueError)


def test_scan_without_a_single_point_is_an_error(monkeypatch, tmpdir):
    from wwb_scanner.utils import dbstore
    unlock = sim.Fault('set_center_freq', 'unlock', lambda c, f: True)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 476., lock_lib=True,
                              sdr_kwargs=dict(faults=[unlock]))
    assert sc.spectrum.sample_data.size == 0
    assert sc.error is not None and 'measured nothing' in str(sc.error)
    assert dbstore.db_store.scan_db.table('scans_performed').all() == []   # V22.7: saved as a scan


def test_tiles_without_data_are_a_warning(monkeypatch, tmpdir, caplog):
    caplog.set_level(logging.INFO, logger='wwb_scanner.status')
    unlock = sim.Fault('set_center_freq', 'unlock', lambda c, f: abs(f - 473.5e6) < 0.5e6)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 480., lock_lib=True,
                              sdr_kwargs=dict(faults=[unlock]))
    assert sc.error is None and sc.sample_collection.missing_tiles
    rec = [r for r in caplog.records if 'tile(s) without data' in r.getMessage()]
    assert rec and rec[-1].levelno == logging.WARNING and 'gap at' in rec[-1].getMessage()


def test_timing_report_goes_next_to_the_history(monkeypatch, tmpdir):
    data = tmpdir.join('real_data')
    monkeypatch.setenv('WWB_SCANNER_DATA', str(data))
    from wwb_scanner import scan_dll
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', lambda serial_number=None, device_index=None:
                        sim.FakeRtlSdr())
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    st = _store(tmpdir)
    sc = _run(main.Scanner(config=sim.make_config(470, 476), history=st))
    assert sc.error is None and sc.spectrum.eid is not None
    assert os.path.exists(str(tmpdir.join('h', 'wwb_scan_timing.txt')))
    assert not os.path.exists(str(data.join('wwb_scan_timing.txt')))   # V22.7: written there
    sc2 = _run(main.Scanner(config=sim.make_config(470, 476), history=None))
    assert sc2.error is None and os.path.exists(str(data.join('wwb_scan_timing.txt')))


# ------------------------------------------------------------------------ A5

def test_closing_the_window_stops_the_scan_and_waits():
    from test_v210 import _isolated
    _isolated("""
    from wwb_scanner.scanner import sample_processing as sp
    from wwb_scanner.ui.pyside import main as M
    made = []
    def slow(serial_number=None):
        made.append(sim.FakeRtlSdr(serial_number=serial_number, read_delay=0.02))
        return made[-1]
    sdrwrapper.RtlSdr = slow
    si = make()
    si.scanConfig.startFreq, si.scanConfig.endFreq = 470., 560.
    si.start()
    end = time.time() + 20
    while time.time() < end and not (made and made[-1].calls['read_samples'] > 4):
        pump(0.02)
    sc = si.scanner
    assert sc is not None and sc._running.is_set()
    t0 = time.time()
    assert S.shutdown_all(10.) is True
    assert time.time() - t0 < 5., time.time() - t0
    assert sc.stop_requested and sc._stopped.is_set()
    assert not si.scan_thread._thread.isRunning()
    assert made[-1].device_opened is False              # the scan closed the dongle
    print('ALL_OK')
    """)


def test_closing_the_window_with_a_hanging_usb_call_ends_the_process():
    from test_v210 import _isolated
    _isolated("""
    from wwb_scanner.scanner import sample_processing as sp
    from wwb_scanner.ui.pyside import main as M
    sp.READ_TIMEOUT = 30.
    fault = sim.Fault('read_samples', 'hang', lambda c, f: c == 4, hang_s=6.)
    def hanging(serial_number=None):
        return sim.FakeRtlSdr(serial_number=serial_number, faults=[fault])
    sdrwrapper.RtlSdr = hanging
    si = make()
    si.start()
    end = time.time() + 20
    while time.time() < end and not fault.hits:
        pump(0.02)
    assert fault.hits == 1
    codes = []
    M.exit_now = codes.append
    import wwb_scanner.ui.pyside.scanner as S2
    S2.shutdown_all.__defaults__ = (0.5,)
    M.on_app_quit()
    assert codes == [1], codes
    print('ALL_OK')
    sys.stdout.flush()
    os._exit(0)          # what exit_now does (Qt would abort: thread still running)
    """)
