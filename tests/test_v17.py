"""V17 "clean scan": tile ripple, own spurs, floor shape (calibration), auto gain."""
import logging
import os

import numpy as np
import pytest

import sim
from wwb_scanner.scanner import cleanup as C

BIN = 0.025
PERIOD = 31


def synthetic(lo=31.0, hi=1766.0, seed=0, ripple=True, shape=True, spurs=True):
    """A scan without antenna: floor shape + tile ripple + 3-point spurs."""
    rng = np.random.RandomState(seed)
    f = np.round(np.arange(lo, hi + 1e-9, BIN), 4)
    floor = np.full(f.size, -101.0)
    if shape:
        floor += 1.2 * np.exp(-((f - 150.0) / 80.0) ** 2) - 2.4 * np.clip((f - 1000.0) / 766.0, 0, 1)
    y = floor + rng.randn(f.size) * 0.15
    if ripple:
        r = 0.2 * np.cos(2 * np.pi * np.arange(PERIOD) / PERIOD)
        y += r[C._phase(f, lo, PERIOD)]
    if spurs:
        for k in range(2, 30):
            s = 28.8 * k
            i = int(np.argmin(np.abs(f - s)))
            if abs(f[i] - s) > 0.001:
                continue
            a = 10.0 - 0.3 * k
            if a <= 0.5:
                continue
            p = 10 ** (y[i - 1:i + 2] / 10.)
            add = 10 ** (floor[i] / 10.) * (10 ** (np.array([a - 5, a, a - 5]) / 10.) - 1)
            y[i - 1:i + 2] = 10 * np.log10(p + add)
    return f, y, floor


def test_ripple_is_measured_and_removed():
    f, y, floor = synthetic(shape=False, spurs=False)
    r, n = C.measure_ripple(f, y, 31.0, PERIOD)
    want = 0.2 * np.cos(2 * np.pi * np.arange(PERIOD) / PERIOD)
    assert n > 1000
    assert np.max(np.abs(r - want)) < 0.03
    c = C.remove_ripple(f, y, 31.0, r)
    assert np.std(c - floor) < 0.16 < np.std(y - floor)


def test_spurs_removed_signals_kept():
    f, y, floor = synthetic(ripple=False, shape=False)
    # a wide transmitter (200 kHz) right on a spur and a narrow carrier elsewhere
    on = 28.8 * 18                      # 518.4
    m = np.abs(f - on) <= 0.1
    y[m] = 10 * np.log10(10 ** (y[m] / 10.) + 10 ** ((floor[m] + 15) / 10.))
    i0 = int(np.argmin(np.abs(f - 600.05)))
    y[i0] += 12.0
    c, gone = C.remove_spurs(f, y)
    assert 86.4 in gone and 259.2 in gone
    assert on not in gone
    assert c[np.argmin(np.abs(f - on))] > floor[0] + 14         # transmitter kept
    assert c[i0] == y[i0]                                        # carrier kept
    for s in gone:
        i = int(np.argmin(np.abs(f - s)))
        assert abs(c[i] - floor[i]) < 0.6, s


def test_profile_flattens_floor_and_roundtrip(tmpdir):
    f, y, floor = synthetic(seed=1)
    prof = C.build_profile(f, y, 31.0, PERIOD, meta=dict(gain=29.7, sample_rate=2.4e6))
    assert len(prof['spurs']) >= 20 and not prof['external']
    path = C.save_profile(prof, str(tmpdir.join('p.npz')))
    p2 = C.load_profile(path)
    assert np.allclose(p2['floor_db'], prof['floor_db']) and p2['meta']['gain'] == 29.7
    # another scan of the same dongle, other noise
    f, y2, _ = synthetic(seed=2)
    c, info = C.clean(f, y2, 31.0, PERIOD, profile=p2)
    assert info['flatten'] and info['ripple'] == 'scan' and len(info['spurs_removed']) >= 20
    assert np.ptp(C.running_median(y2, 201)) > 3.0
    assert np.ptp(C.running_median(c, 201)) < 0.4
    assert C.profile_matches(p2, 29.7, 2.4e6, PERIOD) == ''
    assert 'gain' in C.profile_matches(p2, 38.6, 2.4e6, PERIOD)


def test_calibration_refused_with_antenna(tmpdir, monkeypatch):
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    f, y, floor = synthetic(seed=3)
    fm = (f > 88) & (f < 108)
    y[fm] += 20.0 * (np.sin(f[fm] * 7) > 0)          # FM stations
    ok, text = C.calibrate(f, y, 31.0, 0.775, dict(gain=29.7, sample_rate=2.4e6))
    assert not ok and 'antenna' in text
    f, y, floor = synthetic(seed=4)
    i = int(np.argmin(np.abs(f - 541.575)))
    y[i - 3:i + 4] += 12.0                              # own IEM leaking in
    ok, text = C.calibrate(f, y, 31.0, 0.775, dict(gain=29.7, sample_rate=2.4e6))
    assert ok and '541.5' in text
    prof, why = C.find_profile(29.7, 2.4e6, PERIOD)
    assert prof is not None and why == ''
    assert prof['external'] and prof['external'][0][0] == pytest.approx(541.575, abs=0.03)
    # the IEM is not a spur: a later scan keeps it
    c, info = C.clean(f, y, 31.0, PERIOD, profile=prof)
    assert c[i] > -95
    assert C.find_profile(33.8, 2.4e6)[0] is None
    assert 'Calibrated' in C.status_text(29.7, 2.4e6)
    assert 'Not calibrated' in C.status_text(33.8, 2.4e6)


def test_merge_weight():
    from wwb_scanner.scan_objects.samplearray import SampleArray
    a = SampleArray.create(frequency=np.array([1.0, 1.025]), magnitude=np.array([1.0, 1.0]))
    b = SampleArray.create(frequency=np.array([1.025, 1.05]), magnitude=np.array([100.0, 100.0]))
    a.insert_sorted(b, 0.01)
    m = np.asarray(a.magnitude)
    assert m[0] == 1.0 and m[2] == 100.0
    assert m[1] == pytest.approx((1.0 + 0.01 * 100.0) / 1.01)


def _tone_level(scanner, f0):
    f, y = sim.spectrum_arrays(scanner)
    return float(y[np.abs(f - f0) <= 0.05].max())


def test_auto_gain_fixes_clipped_tile(monkeypatch, tmpdir, caplog):
    f0 = 500.0e6
    caplog.set_level(logging.INFO, logger='wwb_scanner')
    ref_air = sim.Air(noise=0.01).tone(f0, 0.1)
    ref, _, _ = sim.run_scan(monkeypatch, tmpdir, 495, 505, gain=29.7,
                             sdr_kwargs={'air': ref_air, 'gain_affects': True})
    want = _tone_level(ref, 500.0) + 20 * np.log10(1.6 / 0.1)
    hot = sim.Air(noise=0.01).tone(f0, 1.6)              # clips at gain 29.7
    raw, _, _ = sim.run_scan(monkeypatch, tmpdir, 495, 505, gain=29.7,
                             sdr_kwargs={'air': hot, 'gain_affects': True})
    fixed, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 495, 505, gain=29.7, auto_gain=True,
                                 sdr_kwargs={'air': hot, 'gain_affects': True})
    err_raw = abs(_tone_level(raw, 500.0) - want)
    err_fix = abs(_tone_level(fixed, 500.0) - want)
    assert err_fix < 1.0 < err_raw, (err_fix, err_raw)
    coll = fixed.sample_collection
    assert coll.remeasured and coll.overload
    assert all(coll.sample_sets[k].clip_ratio < 1e-3 for k in coll.remeasured)
    assert sdr.gains_set[-1] == 29.7 and min(sdr.gains_set) < 29.7     # gain set back
    assert any('measured again' in r.getMessage() for r in caplog.records)


def test_auto_gain_peak_threshold_configurable(monkeypatch, tmpdir):
    """The peak threshold (not just clipping) triggers the re-measure, and a
    higher threshold suppresses it (V18)."""
    f0 = 500.0e6
    air = sim.Air(noise=0.01).tone(f0, 0.6)   # strong (peak ~0.6) but not clipping

    s1, sdr1, _ = sim.run_scan(monkeypatch, tmpdir, 495, 505, gain=29.7, auto_gain=True,
                                auto_gain_peak_db=-6.0, sdr_kwargs={'air': air, 'gain_affects': True})
    dropped = [k for k, s in s1.sample_collection.sample_sets.items()
               if getattr(s, 'gain_drop', 0.) > 0]
    assert dropped, 'peak above threshold should trigger the re-measure'
    assert sdr1.gains_set[-1] == 29.7                      # gain set back

    s2, sdr2, _ = sim.run_scan(monkeypatch, tmpdir, 495, 505, gain=29.7, auto_gain=True,
                                auto_gain_peak_db=0.0, sdr_kwargs={'air': air, 'gain_affects': True})
    dropped2 = [k for k, s in s2.sample_collection.sample_sets.items()
                if getattr(s, 'gain_drop', 0.) > 0]
    assert not dropped2, 'peak below threshold should not trigger'


def test_hdr_scan_runs_and_merges(monkeypatch, tmpdir):
    """run_hdr_scan() does two passes and returns a merged spectrum."""
    import threading
    from wwb_scanner.scanner import sdrwrapper, main
    from wwb_scanner import scan_dll
    from wwb_scanner.utils import dbstore

    air = sim.Air(noise=0.01).tone(500.0e6, 1.0)   # strong tone
    made = []

    def factory(serial_number=None):
        s = sim.FakeRtlSdr(serial_number=serial_number, air=air, gain_affects=True)
        made.append(s)
        return s

    monkeypatch.setattr(sdrwrapper, 'RtlSdr', factory)
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))

    scanner = main.Scanner(config=sim.make_config(495, 505, gain=29.7,
                                                  hdr_enabled=True, hdr_gain_drop_db=30.0))
    th = threading.Thread(target=scanner.run_hdr_scan, daemon=True)
    th.start()
    th.join(120)
    assert not th.is_alive(), 'HDR scan did not finish in 120 s'

    f, y = sim.spectrum_arrays(scanner)
    assert f.size > 0
    assert np.isfinite(y).all()
    tone = y[np.abs(f - 500.0) <= 0.1].max()
    assert tone > np.median(y) + 10


def test_scan_with_cleanup_keeps_raw(monkeypatch, tmpdir, caplog):
    caplog.set_level(logging.INFO, logger='wwb_scanner')
    air = sim.Air(noise=0.01).tone(28.8e6 * 18, 0.003).tone(510.3e6, 0.05)
    clean = dict(clean_ripple=True, clean_spurs=True, clean_floor=True)
    scanner, _, _ = sim.run_scan(monkeypatch, tmpdir, 505, 525, sdr_kwargs={'air': air}, clean=clean)
    sp = scanner.spectrum
    assert sp.cleanup_info['spurs'] and sp.raw_dbFS.size == sp.sample_data.size
    assert 518.4 in sp.cleanup_info['spurs_removed']
    assert 'no calibration' in sp.cleanup_info.get('floor_skipped', '')
    f, y = sim.spectrum_arrays(scanner)
    i = int(np.argmin(np.abs(f - 518.4)))
    assert sp.raw_dbFS[i] - y[i] > 3                       # spur gone
    k = int(np.argmin(np.abs(f - 510.3)))
    assert abs(y[k] - sp.raw_dbFS[k]) < 0.5                 # real carrier kept
    status = [r.getMessage() for r in caplog.records if r.name == 'wwb_scanner.status']
    assert status and 'clean:' in status[-1] and 'own spurs removed' in status[-1]
    from wwb_scanner.utils.dbstore import db_store
    rec = db_store.scan_db.table('scans_performed').all()[-1]
    assert rec['cleanup']['spurs']
    with np.load(os.path.join(db_store.scans_dir, rec['sample_data_file'])) as z:
        assert 'raw_dbFS' in z.files and z['raw_dbFS'].size == sp.sample_data.size


def test_cleanup_off_by_default(monkeypatch, tmpdir):
    scanner, _, _ = sim.run_scan(monkeypatch, tmpdir, 505, 515)
    assert getattr(scanner.spectrum, 'cleanup_info', None) is None


def test_clean_scan_tool(tmpdir, monkeypatch, capsys):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    import clean_scan
    f, y, _ = synthetic(seed=5)
    a = tmpdir.join('scan.csv')
    a.write('\n'.join('%d,%.1f' % (round(x * 1000), v) for x, v in zip(f, y)))
    cal = tmpdir.join('noant.csv')
    f2, y2, _ = synthetic(seed=6)
    cal.write('\n'.join('%d,%.1f' % (round(x * 1000), v) for x, v in zip(f2, y2)))
    monkeypatch.setattr(sys, 'argv', ['clean_scan.py', str(a), '--calibration', str(cal)])
    assert clean_scan.main() == 0
    out = capsys.readouterr().out
    assert 'own spurs removed: 2' in out and 'floor levelled: yes' in out
    lines = tmpdir.join('scan_clean.csv').read().split('\n')
    assert len(lines) == f.size and lines[0].startswith('31000,')
