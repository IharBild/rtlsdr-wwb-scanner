"""QA plan group S: stability, faults, long runs."""
import gc
import os
try:
    import resource
except ImportError:          # Windows
    resource = None
import threading
import time

import numpy as np
import pytest

import sim
from sim import Fault


def finished_cleanly(scanner):
    return scanner._stopped.is_set() and not scanner._running.is_set()


# S1: USB error at the start / middle / last tile, in tune or in read.
# Real pyrtlsdr closes the device after any library error -> action 'close'.
@pytest.mark.parametrize('method', ['set_center_freq', 'read_samples'])
@pytest.mark.parametrize('where', ['first', 'middle', 'last'])
@pytest.mark.parametrize('action', ['close', 'raise'])
def test_S1_usb_error(monkeypatch, tmpdir, method, where, action):
    n_tiles = 13            # 470-480 MHz
    at = {'first': 1, 'middle': 7, 'last': n_tiles}[where]
    # reads: discard read + data read per tile
    k = at if method == 'set_center_freq' else 2 * at - 1
    fault = Fault(method, action, lambda n, f, k=k: n == k)
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 480,
                                    sdr_kwargs={'air': sim.Air(), 'faults': [fault]})
    f, y = sim.spectrum_arrays(scanner)
    print('S1 %-15s %-6s %-5s: finished %s in %.2f s, %d points, use-after-close %d, error %r'
          % (method, where, action, finished_cleanly(scanner), dt, f.size,
             sdr.closed_calls, getattr(scanner, 'error', None)))
    assert finished_cleanly(scanner)
    assert sdr.closed_calls == 0
    if where != 'first':
        assert f.size > 0       # what was measured before the error is kept


# S2: a call that never returns (stuck USB)
@pytest.mark.parametrize('method', ['set_center_freq', 'read_samples'])
def test_S2_hang(monkeypatch, tmpdir, method):
    fault = Fault(method, 'hang', lambda n, f: n == 5, hang_s=3.0)
    t0 = time.time()
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 480, timeout=0.5,
                                    sdr_kwargs={'air': sim.Air(), 'faults': [fault]})
    print('S2 hang in %s: scan ended after %.2f s, aborted %s'
          % (method, dt, scanner.sample_collection.aborted))
    assert finished_cleanly(scanner)
    assert scanner.sample_collection.aborted
    assert dt < 3.0


# S4: PLL does not lock (scanner-v4 DLL extras)
@pytest.mark.parametrize('share', ['one', 'ten_percent', 'all'])
def test_S4_pll_unlock(monkeypatch, tmpdir, share, capsys):
    when = {'one': lambda n, f: n == 3,
            'ten_percent': lambda n, f: n % 10 == 0,
            'all': lambda n, f: True}[share]
    fault = Fault('set_center_freq', 'unlock', when)
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 490, lock_lib=True,
                                    sdr_kwargs={'air': sim.Air(), 'faults': [fault]})
    out = capsys.readouterr().out
    f, y = sim.spectrum_arrays(scanner)
    tiles = len(scanner.sample_collection.sample_sets)
    stat = [l for l in out.splitlines() if l.startswith('tuner PLL')]
    print('S4 %-11s: %d tiles, %d tunes, %d unlocks, %d points, %s'
          % (share, tiles, sdr.calls['set_center_freq'], sdr.unlocks, f.size, stat))
    assert finished_cleanly(scanner)
    assert sdr.calls['set_center_freq'] <= 2 * tiles
    assert stat


# S5: skipped tiles leave a hole; what does the WWB CSV show there?
def test_S5_hole_in_csv(monkeypatch, tmpdir):
    fault = Fault('set_center_freq', 'unlock', lambda n, f: 480e6 <= f <= 481.6e6)
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 490, lock_lib=True,
                                    sdr_kwargs={'air': sim.Air().tone(480.5e6, 0.05), 'faults': [fault]})
    f, y = sim.spectrum_arrays(scanner)
    gaps = np.flatnonzero(np.diff(f) > 0.026)
    p = str(tmpdir.join('hole.csv'))
    scanner.spectrum.export_to_file(filename=p, frequency_format='khz')
    lines = open(p).read().split('\n')
    k = np.array([int(l.split(',')[0]) for l in lines])
    v = np.array([float(l.split(',')[1]) for l in lines])
    hole = (k > f[gaps[0]] * 1000) & (k < f[gaps[0] + 1] * 1000) if gaps.size else np.zeros(k.size, bool)
    print('S5 gap in scan %s MHz; CSV has %d points there, filled by straight line (%.1f..%.1f dB) - tone at 480.5 is gone'
          % ([(round(f[g], 3), round(f[g + 1], 3)) for g in gaps], hole.sum(),
             v[hole].min() if hole.any() else 0, v[hole].max() if hole.any() else 0))
    assert set(np.diff(k)) == {25}
    assert scanner.sample_collection.missing_tiles      # the gap is reported


# S5b: a tile that fails once is measured again at the end (no gap)
def test_S5b_failed_tile_retried(monkeypatch, tmpdir):
    seen = {}
    def once(n, f):
        if 480e6 <= f <= 481.6e6 and f not in seen:
            seen[f] = 1
            return True
        return False
    fault = Fault('set_center_freq', 'unlock', once)
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 490, lock_lib=True,
                                    sdr_kwargs={'air': sim.Air().tone(480.5e6, 0.05), 'faults': [fault]})
    f, y = sim.spectrum_arrays(scanner)
    gaps = np.flatnonzero(np.diff(f) > 0.026)
    fp = f[np.argmax(y)]
    print('S5b tiles failing once: gaps %d, tone found at %.3f MHz, missing tiles %d'
          % (gaps.size, fp, len(scanner.sample_collection.missing_tiles)))
    assert gaps.size == 0 and abs(fp - 480.5) < 1e-6


# S6: Stop in the middle of a scan, then a new scan
def test_S6_stop_then_start(monkeypatch, tmpdir):
    from wwb_scanner.scanner import sdrwrapper, main
    from wwb_scanner.utils import dbstore
    from wwb_scanner import scan_dll
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', lambda serial_number=None: sim.FakeRtlSdr(read_delay=0.05))
    times = []
    for attempt in range(3):
        sc = main.Scanner(config=sim.make_config(470, 520))
        th = threading.Thread(target=sc.run_scan, daemon=True)
        th.start()
        time.sleep(0.5)
        t0 = time.time()
        stopper = threading.Thread(target=sc.stop_scan, daemon=True)
        stopper.start()
        stopper.join(10)
        th.join(10)
        times.append(time.time() - t0)
        assert not stopper.is_alive() and not th.is_alive(), 'Stop hangs'
    print('S6 stop took %s s' % ['%.2f' % t for t in times])
    assert max(times) < 2.0


# S7: Stop after the scan finished, and Stop before it started
def test_S7_stop_edge_cases(monkeypatch, tmpdir):
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 472, sdr_kwargs={'air': sim.Air()})
    t = threading.Thread(target=scanner.stop_scan, daemon=True)
    t.start(); t.join(3)
    after = not t.is_alive()
    from wwb_scanner.scanner import main
    sc2 = main.Scanner(config=sim.make_config(470, 472))
    t2 = threading.Thread(target=sc2.stop_scan, daemon=True)
    t2.start(); t2.join(3)
    before = not t2.is_alive()
    print('S7 stop after finish returns: %s; stop before start returns: %s' % (after, before))
    assert finished_cleanly(scanner)
    assert after
    assert before


def rss_mb():
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS'):
                    return int(line.split()[1]) / 1024.
    except OSError:
        pass
    if resource is not None:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.
    try:                         # Windows
        import ctypes
        from ctypes import wintypes
        class PMC(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                        ('a', ctypes.c_size_t), ('b', ctypes.c_size_t), ('c', ctypes.c_size_t),
                        ('d', ctypes.c_size_t), ('e', ctypes.c_size_t), ('f', ctypes.c_size_t)]
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
                                                 ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 1048576.
    except Exception:
        return 0.


# S8 + S10: many scans in a row: time, memory, threads
def test_S8_S10_many_scans(monkeypatch, tmpdir):
    n = int(os.environ.get('QA_SCANS', '50'))
    times, rss, threads = [], [], []
    for i in range(n):
        scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 490, sdr_kwargs={'air': sim.Air()})
        assert finished_cleanly(scanner)
        del scanner, sdr
        gc.collect()
        times.append(dt)
        rss.append(rss_mb())
        threads.append(threading.active_count())
    print('S8 %d scans 470-490: time first %.2f last %.2f s; RSS %.0f -> %.0f MB; threads %d -> %d'
          % (n, times[0], times[-1], rss[0], rss[-1], threads[0], threads[-1]))
    assert times[-1] < 2 * max(times[0], 0.3)
    assert rss[-1] - rss[2] < 100
    assert threads[-1] <= threads[0] + 2


# S10b: threads left behind by hung calls
def test_S10_threads_after_hangs(monkeypatch, tmpdir):
    before = threading.active_count()
    for i in range(3):
        fault = Fault('read_samples', 'hang', lambda n, f: n == 3, hang_s=1.0)
        sim.run_scan(monkeypatch, tmpdir, 470, 475, timeout=0.2,
                     sdr_kwargs={'air': sim.Air(), 'faults': [fault]})
    mid = threading.active_count()
    time.sleep(1.5)
    after = threading.active_count()
    print('S10 threads: before %d, right after 3 hung scans %d, 1.5 s later %d' % (before, mid, after))
    assert after <= before + 1


# S11: graph reads the spectrum while the scan writes it
def test_S11_concurrent_reader(monkeypatch, tmpdir):
    from wwb_scanner.scanner import sdrwrapper, main
    errors, reads = [], [0]
    stop = threading.Event()
    holder = {}
    def reader():
        while not stop.is_set():
            sc = holder.get('sc')
            if sc is None:
                time.sleep(0.001); continue
            try:
                sp = sc.spectrum
                with sp.data_update_lock:
                    d = sp.sample_data.data
                    x = np.array(d['frequency']); y = np.array(d['dbFS'])
                    assert x.size == y.size
                    if x.size > 1:
                        assert np.all(np.diff(x) > 0)
                reads[0] += 1
            except Exception as exc:
                errors.append(repr(exc))
    orig = main.Scanner.__init__
    def init(self, **kw):
        orig(self, **kw)
        holder['sc'] = self
    monkeypatch.setattr(main.Scanner, '__init__', init)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    sim.run_scan(monkeypatch, tmpdir, 470, 520, sdr_kwargs={'air': sim.Air()})
    stop.set(); t.join(2)
    print('S11 %d concurrent reads, %d errors %s' % (reads[0], len(errors), errors[:2]))
    assert not errors


# S13: no dongle / dongle busy
def test_S13_no_dongle(monkeypatch, tmpdir):
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 480,
                                    sdr_kwargs={'fail_open': True})
    print('S13 no dongle: finished %s in %.2f s, error %r' % (finished_cleanly(scanner), dt,
                                                               getattr(scanner, 'error', None)))
    assert finished_cleanly(scanner)
    assert 'not found' in str(scanner.error)


# S14: odd settings must not hang or crash the scan thread
CASES = {
    'start>end': dict(start=480, end=470),
    'start=end': dict(start=480, end=480),
    'sweeps=0': dict(start=470, end=472, sweeps=0),
    'per_sweep=0': dict(start=470, end=472, per_sweep=0),
    'window=None': dict(start=470, end=472, window=None),
    'window=1': dict(start=470, end=472, window=1),
    'window=65536': dict(start=470, end=472, window=65536),
    'overlap=0': dict(start=470, end=472, overlap=0.0),
    'overlap=1.5': dict(start=470, end=472, overlap=1.5),
    'negative': dict(start=-10, end=5),
    'above_limit': dict(start=1800, end=1900),
}


@pytest.mark.parametrize('case', sorted(CASES))
def test_S14_odd_settings(monkeypatch, tmpdir, case):
    kw = dict(CASES[case])
    start, end = kw.pop('start'), kw.pop('end')
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, start, end, sdr_kwargs={'air': sim.Air()}, **kw)
    f, y = sim.spectrum_arrays(scanner)
    print('S14 %-12s: finished %s in %.2f s, %d points, tiles %d, error %r'
          % (case, finished_cleanly(scanner), dt, f.size,
             len(sdr.tuned) if sdr else -1, getattr(scanner, 'error', None)))
    assert finished_cleanly(scanner)
    assert dt < 20
