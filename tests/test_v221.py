"""V22.1: scan service and events in the core (audit 16.11.2026, items 3-4)."""
import logging
import os
import threading

import numpy as np
import pytest

import sim


@pytest.fixture
def store(monkeypatch, tmpdir):
    from wwb_scanner.utils import dbstore
    from wwb_scanner.scanner import sdrwrapper
    from wwb_scanner import scan_dll, paths
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_index.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setattr(paths, '_override', str(tmpdir))
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    made = []
    def factory(serial_number=None):
        s = sim.FakeRtlSdr(serial_number=serial_number)
        made.append(s)
        return s
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', factory)
    return tmpdir


class Recorder(object):
    def __init__(self, kinds=None):
        from wwb_scanner import events
        self.events = []
        self.lock = threading.Lock()
        self.token = events.bus.subscribe(self, kinds)
    def __call__(self, ev):
        with self.lock:
            self.events.append(ev)
    def kinds(self):
        return [e['kind'] for e in self.events]
    def close(self):
        from wwb_scanner import events
        events.bus.unsubscribe(self.token)


def _raw(conf):
    from wwb_scanner.scanner import config
    conf.processing.update(config.raw_processing())
    conf.sampling.auto_gain = False
    conf.sampling.verify_peaks = False
    return conf


# ------------------------------------------------------------------ events

def test_bus_subscribe_filter_and_failing_callback():
    from wwb_scanner.events import EventBus
    b = EventBus()
    got, only = [], []
    t1 = b.subscribe(got.append)
    b.subscribe(only.append, kinds=('progress',))
    b.subscribe(lambda e: 1 / 0)                     # never stops the others
    b.emit('progress', value=0.5)
    b.emit('tile', center_mhz=470.)
    assert [e['kind'] for e in got] == ['progress', 'tile'] and [e['value'] for e in only] == [0.5]
    b.unsubscribe(t1)
    b.emit('progress', value=1.)
    assert len(got) == 2 and len(only) == 2


def test_status_records_become_events(capsys):
    from wwb_scanner import events
    events.install_log_bridge()
    rec = Recorder(('status',))
    try:
        logging.getLogger('wwb_scanner.status').info('Scan 470-480 MHz done in 3 s')
        logging.getLogger('wwb_scanner.scanner.x').warning('a warning')
        logging.getLogger('wwb_scanner.scanner.x').info('not for the status line')
        logging.getLogger('wwb_scanner.ui').error('an error\nwith details')
    finally:
        rec.close()
    assert [(e['level'], e['text']) for e in rec.events] == [
        ('info', 'Scan 470-480 MHz done in 3 s'), ('warning', 'a warning'), ('error', 'an error')]


def test_console_lines_are_printed_and_sent(capsys):
    from wwb_scanner import events
    rec = Recorder(('console',))
    try:
        events.console('scan tile 1/13 at 470.388 MHz')
    finally:
        rec.close()
    assert 'scan tile 1/13 at 470.388 MHz' in capsys.readouterr().out
    assert rec.events[0]['text'] == 'scan tile 1/13 at 470.388 MHz'


def test_no_print_left_in_the_core():
    import wwb_scanner
    root = os.path.dirname(wwb_scanner.__file__)
    bad = []
    for dp, _, files in os.walk(root):
        if os.sep + 'ui' in dp:
            continue
        for f in files:
            if f.endswith('.py') and f != 'events.py':
                for i, line in enumerate(open(os.path.join(dp, f), encoding='utf-8'), 1):
                    if line.lstrip().startswith('print('):
                        bad.append('%s:%d' % (f, i))
    assert not bad, bad


def test_scan_sends_its_events(store):
    from wwb_scanner.scanner import main
    rec = Recorder()
    try:
        sc = main.Scanner(config=sim.make_config(470, 480), history=None)
        sc.run_scan()
    finally:
        rec.close()
    k = rec.kinds()
    assert k.count('scan_started') == 1 and k.index('scan_started') < k.index('tile')
    tiles = [e for e in rec.events if e['kind'] == 'tile']
    assert len(tiles) == len(sc.sample_collection.sample_sets) == 13
    assert all(e['points'] > 0 for e in tiles)
    prog = [e['value'] for e in rec.events if e['kind'] == 'progress']
    assert prog == sorted(prog) and prog[-1] == 1.0
    started = [e for e in rec.events if e['kind'] == 'scan_started'][0]
    assert started['ranges'] == [[470.0, 480.0]] and started['tiles'] == 13
    assert any(e['kind'] == 'status' and e['text'].startswith('Scan 470') and ' done in ' in e['text']
               for e in rec.events), [e for e in rec.events if e['kind'] == 'status']


# ------------------------------------------------------------------ prepare / start problems

def test_prepare_kinds():
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    from wwb_scanner.scanner import gain_cal, cleanup
    base = ScanConfig({'scan_range': [470., 536.], 'device': {'gain': 33.8}, 'eid': 5})
    c, name = service.prepare(service.SCAN, base, when=0)
    assert list(c.scan_range) == [470., 536.] and name.startswith('470-536 MHz, ')
    assert c.processing.clean_spurs and c.get('eid') is None and base.eid == 5
    c, name = service.prepare(service.FLOOR_CALIBRATION, base)
    assert list(c.scan_range) == list(cleanup.CALIBRATION_RANGE_MHZ) and not c.sampling.auto_gain
    assert not any(c.processing.get(k) for k in c.processing.keys())
    assert name == 'Calibration without antenna, gain 33.8 dB'
    c, name = service.prepare(service.GAIN_CALIBRATION, base)
    assert c.gain_calibration and c.device.gain == gain_cal.SURVEY_GAIN_DB and c.scan_ranges
    assert 'Gain step calibration' in name
    multi = ScanConfig({'scan_ranges': [[470., 480.], [600., 610.]], 'scan_range': [470., 610.]})
    assert service.prepare(service.SCAN, multi, when=0)[1].startswith('Bands 470-480, 600-610 MHz, ')
    with pytest.raises(ValueError):
        service.prepare('other', base)


def test_start_problems():
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    ok = ScanConfig({'scan_range': [470., 480.]})
    assert service.start_problem(ok) == ''
    assert service.start_problem(ok, dongle_found=False) == service.NO_DONGLE_TEXT
    bad = ScanConfig({'scan_range': [480., 470.]})
    assert service.start_problem(bad) == 'End Freq (470 MHz) must be above Start Freq (480 MHz).'
    assert service.start_problem(bad, service.FLOOR_CALIBRATION) == ''     # own range
    nan = ScanConfig({'scan_range': [float('nan'), 470.]})
    assert service.start_problem(nan) == 'Start Freq and End Freq must be numbers (MHz).'
    no_rate = ScanConfig({'sampling': {'sample_rate': float('nan')}})
    assert 'sample rate' in service.start_problem(no_rate)
    no_gain = ScanConfig({'device': {'gain': None}})
    assert 'gain is not set' in service.start_problem(no_gain)


# ------------------------------------------------------------------ service

def test_service_runs_a_scan_and_reports_the_result(store):
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    rec = Recorder(('scan_finished',))
    try:
        svc = service.ScanService(history=None)
        sc = svc.start(_raw(ScanConfig({'scan_range': [470., 480.]})))
        assert svc.running or svc.result is not None
        res = svc.wait(60)
    finally:
        rec.close()
    assert res is not None and res.ok and not svc.running
    assert res.kind == 'scan' and res.points == 401 and res.tiles_done == res.tiles_total == 13
    assert res.ranges == [[470.0, 480.0]] and res.missing_tiles == [] and res.saved_eid is None
    assert res.name == sc.spectrum.name and res.name.startswith('470-480 MHz, ')
    assert rec.events[0]['result'] is res
    d = res.as_dict()
    assert d['points'] == 401 and 'spectrum' not in d


def test_service_refuses_and_stops(store):
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    svc = service.ScanService(history=None)
    with pytest.raises(service.ScanError) as e:
        svc.start(ScanConfig({'scan_range': [480., 470.]}))
    assert 'must be above' in str(e.value)
    with pytest.raises(service.ScanError):
        svc.start(ScanConfig(), dongle_found=False)
    svc.start(_raw(ScanConfig({'scan_range': [470., 1000.]})))
    with pytest.raises(service.ScanError):
        svc.start(ScanConfig())                      # one scan at a time
    assert svc.stop() is True
    res = svc.wait(60)
    assert res.stopped_by_user and not res.ok and 0 < res.tiles_done < res.tiles_total


def test_service_saves_to_the_history_by_default(store):
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    from wwb_scanner.utils import dbstore
    svc = service.ScanService()
    svc.start(_raw(ScanConfig({'scan_range': [470., 472.]})))
    res = svc.wait(60)
    assert res.ok and res.saved_eid is not None
    assert list(dbstore.db_store.get_all_scans()) == [res.saved_eid]


def test_floor_calibration_is_finished_by_the_core(store, monkeypatch):
    """The window only shows the new state (V22.1: the core saves it)."""
    from wwb_scanner import service
    from wwb_scanner.scanner import cleanup
    from wwb_scanner.scanner.config import ScanConfig
    monkeypatch.setattr(cleanup, 'CALIBRATION_RANGE_MHZ', (31.0, 1766.0))
    saved = {}
    def fake_calibrate(f, y, start, step, meta, clipped_share=None):
        saved.update(points=len(f), start=start, meta=meta, clipped=clipped_share)
        return True, 'Calibration saved (test)'
    monkeypatch.setattr(cleanup, 'calibrate', fake_calibrate)
    svc = service.ScanService(history=None)
    svc.start(ScanConfig({'device': {'gain': 49.6}}), kind=service.FLOOR_CALIBRATION, serial='00000001')
    res = svc.wait(300)
    assert res.calibration_ok is True and res.calibration_text == 'Calibration saved (test)'
    assert saved['start'] == 31.0 and saved['meta']['serial'] == '00000001' and saved['meta']['gain'] == 49.6
    assert saved['points'] == res.points and saved['clipped'] is not None


def test_stopped_calibration_is_not_saved(store, monkeypatch):
    from wwb_scanner import service
    from wwb_scanner.scanner import cleanup
    from wwb_scanner.scanner.config import ScanConfig
    called = []
    monkeypatch.setattr(cleanup, 'calibrate', lambda *a, **k: called.append(1) or (True, 'x'))
    svc = service.ScanService(history=None)
    svc.start(ScanConfig(), kind=service.FLOOR_CALIBRATION)
    svc.stop()
    res = svc.wait(120)
    assert res.calibration_ok is False and 'did not finish' in res.calibration_text and not called
