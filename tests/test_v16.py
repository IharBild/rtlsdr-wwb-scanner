"""V16: ADC overload detector and window messages (logging -> status)."""
import logging

import numpy as np

import sim
from wwb_scanner.scanner import sample_processing as sp


def test_clip_ratio_values():
    x = np.array([0.1 + 0.1j, 1.0 + 0j, -1.0 + 0.5j, 0.2 - 1.0j, 0.99 + 0.99j])
    assert abs(sp.clip_ratio(x) - 3 / 5.) < 1e-12
    assert sp.clip_ratio(np.zeros(0, complex)) == 0.


def test_overload_reported_only_where_clipped(monkeypatch, tmpdir, caplog):
    air = sim.Air(noise=0.02).tone(500.0e6, 1.6)      # clips the 8-bit ADC
    caplog.set_level(logging.WARNING, logger='wwb_scanner')
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 540, sdr_kwargs={'air': air})
    ov = scanner.sample_collection.overload
    assert ov, 'no overload found'
    assert all(lo <= 500.0 <= hi or hi < 500.0 + 3 for lo, hi, _ in ov)
    assert any(lo <= 500.0 <= hi for lo, hi, _ in ov)
    assert any('Overload' in r.getMessage() for r in caplog.records)


def test_no_overload_on_normal_signal(monkeypatch, tmpdir):
    air = sim.Air(noise=0.02).tone(500.0e6, 0.05)
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 540, sdr_kwargs={'air': air})
    assert scanner.sample_collection.overload == []


def test_scan_failure_is_logged_as_error(monkeypatch, tmpdir, caplog):
    caplog.set_level(logging.INFO, logger='wwb_scanner')
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 480, sdr_kwargs={'fail_open': True})
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(l >= logging.ERROR and m.startswith('Scan failed') for l, m in msgs), msgs


def test_scan_done_status_info(monkeypatch, tmpdir, caplog):
    caplog.set_level(logging.INFO, logger='wwb_scanner')
    sim.run_scan(monkeypatch, tmpdir, 470, 480)
    assert any(r.name == 'wwb_scanner.status' and 'done' in r.getMessage() for r in caplog.records)


def test_compare_tool_self_and_shift(tmpdir):
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    import compare_wwb
    f = np.round(np.arange(470.0, 520.0001, 0.025), 3)
    rng = np.random.RandomState(1)
    y = -100 + rng.randn(f.size) * 0.3
    for p in (480.0, 490.5, 505.25):
        y[np.argmin(abs(f - p))] += 20
    a = tmpdir.join('a.csv'); b = tmpdir.join('b.csv')
    a.write('\n'.join('%d,%.1f' % (round(x * 1000), v) for x, v in zip(f, y)))
    yb = np.roll(y, 2) - 6.0                       # reference: 50 kHz higher, 6 dB lower
    b.write('\n'.join('%d,%.1f' % (round(x * 1000), v) for x, v in zip(f, yb)))
    text, _ = compare_wwb.compare(str(a), str(a))
    assert 'shifted by +0 kHz' in text and 'not found 0' in text
    text, _ = compare_wwb.compare(str(a), str(b))
    assert 'shifted by -50 kHz' in text
    assert '+6.0' in text


def test_status_says_stopped_after_dongle_error(monkeypatch, tmpdir, caplog):
    caplog.set_level(logging.INFO, logger='wwb_scanner')
    faults = [sim.Fault('read_samples', 'close', lambda n, f: n == 10)]
    sim.run_scan(monkeypatch, tmpdir, 470, 520, sdr_kwargs={'faults': faults})
    status = [r.getMessage() for r in caplog.records if r.name == 'wwb_scanner.status']
    assert status and 'stopped after' in status[-1] and 'done' not in status[-1], status
