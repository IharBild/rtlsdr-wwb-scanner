"""QA plan group P: speed and memory without the dongle.

python tools/qa_perf.py  -> prints one line per measurement
"""
import cProfile
import io
import os
import pstats
import sys
import threading
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tests')]
import conftest  # noqa: F401  (rtlsdr stub)
import numpy as np
import sim
from wwb_scanner.scanner import sample_processing as sp, sdrwrapper, main as smain
from wwb_scanner.scan_objects import Spectrum
from wwb_scanner.utils import dbstore
from wwb_scanner import scan_dll
import tempfile

TMP = tempfile.mkdtemp(prefix='qa_perf_')
os.environ['HOME'] = TMP
dbstore.DBStore.DB_PATH = os.path.join(TMP, 'db.json')
dbstore.DBStore.SCAN_DB_PATH = os.path.join(TMP, 'scan_db.json')
scan_dll._cache['lib'] = None


def full_scan(start, end, profile=False, **cfg):
    made = []
    def factory(serial_number=None):
        s = sim.FakeRtlSdr(air=sim.Air(noise=0.03), quantize=False)
        made.append(s)
        return s
    sdrwrapper.RtlSdr = factory
    sc = smain.Scanner(config=sim.make_config(start, end, **cfg))
    sdrwrapper.RtlSdr = factory
    pr = cProfile.Profile() if profile else None
    t0 = time.time()
    if pr:
        pr.enable()
    sc.run_scan()
    if pr:
        pr.disable()
    return sc, time.time() - t0, pr


def p1_tile_compute():
    sdrwrapper.RtlSdr = lambda serial_number=None: sim.FakeRtlSdr()
    sc = smain.Scanner(config=sim.make_config(470, 480))
    with sc.sdr_wrapper:
        _ = sc.step_size
        coll = sp.SampleCollection(scanner=sc)
        for sweeps in (20, 40, 80, 160):
            sc.sweeps_per_scan = sweeps
            ss = sp.SampleSet(scanner=sc, collection=coll, center_frequency=600e6)
            n = sweeps * 2048
            ss.raw = ((np.random.randn(n) + 1j * np.random.randn(n)) * 0.03).reshape(sweeps, 2048)
            coll.scanner = type('S', (), {'on_sample_set_processed': lambda self, s: None})()
            t0 = time.time()
            for _ in range(20):
                ss.raw = ((np.random.randn(n) + 1j * np.random.randn(n)) * 0.03).reshape(sweeps, 2048)
                ss.process_samples()
            print('P1 tile calculation (welch + crop), %3d x 2048 samples: %.1f ms' % (sweeps, (time.time() - t0) / 20 * 1000))


def p2_merge_growth():
    spec = Spectrum()
    f0 = 0.0
    times = []
    for k in range(2279):
        f = np.round(f0 + np.arange(-37, 38) * 0.025, 6)
        f = f[np.abs(f - f0) > 0.03]
        t0 = time.time()
        spec.add_sample_set(frequency=f, magnitude=np.ones(f.size) * 1e-10, center_frequency=f0 * 1e6,
                            force_lower_freq=True)
        times.append(time.time() - t0)
        f0 = round(f0 + 0.775, 6)
    t = np.array(times) * 1000
    print('P2 merge per tile: first 10%% %.2f ms, last 10%% %.2f ms (x%.1f), total %.1f s for 2279 tiles'
          % (t[:228].mean(), t[-228:].mean(), t[-228:].mean() / t[:228].mean(), t.sum() / 1000))


def p3_p4_full_scan():
    sc, dt, pr = full_scan(0, 1766, profile=True)
    tiles = len(sc.sample_collection.sample_sets)
    print('P3 full scan 0-1766 MHz without USB time: %.1f s, %d tiles, %.1f ms per tile (Python part)'
          % (dt, tiles, dt / tiles * 1000))
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats('tottime').print_stats(12)
    lines = [l for l in s.getvalue().splitlines() if l.strip() and (l.strip()[0].isdigit())][:12]
    print('P4 top functions by own time (calls, tottime s, function):')
    for l in lines:
        parts = l.split()
        print('    %10s %8s  %s' % (parts[0], parts[1], ' '.join(parts[5:])[-90:]))


def p5_memory():
    tracemalloc.start()
    sc, dt, _ = full_scan(0, 1766)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print('P5 memory of full scan (Python heap): now %.0f MB, peak %.0f MB' % (cur / 1e6, peak / 1e6))
    return sc


def p6_finalisation(sc):
    from wwb_scanner.ui.pyside import graph  # needs PySide2 (venv38)
    t0 = time.time(); sc.spectrum.crop(0, 1766); t1 = time.time()
    sc.spectrum.eid = None
    sc.spectrum.save_to_dbstore(); t2 = time.time()
    p = os.path.join(TMP, 'x.csv'); sc.spectrum.export_to_file(filename=p, frequency_format='khz'); t3 = time.time()
    p = os.path.join(TMP, 'x.sdb2'); sc.spectrum.export_to_file(filename=p); t4 = time.time()
    xy = np.zeros(sc.spectrum.sample_data.size, graph.GRAPH_DTYPE)
    xy['x'] = sc.spectrum.sample_data.data['frequency']; xy['y'] = sc.spectrum.sample_data.data['dbFS']
    t5 = time.time(); d = graph.decimate_max(xy, graph.group_size_for(70640)); t6 = time.time()
    print('P6 finalisation 70 641 points: crop %.2f s, save to database %.2f s, CSV %.2f s, sdb2 %.2f s'
          % (t1 - t0, t2 - t1, t3 - t2, t4 - t3))
    print('P7 chart decimation 70 641 -> %d points: %.1f ms' % (d.size, (t6 - t5) * 1000))


def p10_timing_overhead():
    a = full_scan(470, 570)[1]
    orig = sp.SampleSet._tick
    sp.SampleSet._tick = lambda self, stage: None
    b = full_scan(470, 570)[1]
    sp.SampleSet._tick = orig
    print('P10 scan-timing report overhead: %.2f s with, %.2f s without (%.1f %%)' % (a, b, (a - b) / b * 100))


def p11_settling():
    """Real IQ: how long after the start of a recording the level is not yet
    steady (the recordings start right after tuning)."""
    rep = sim.IQReplay()
    if not rep.available():
        print('P11 skipped: no IQ recordings')
        return
    for fc, (iq, fs) in sorted(rep.rec.items()):
        blk = 2048
        n = min(iq.size // blk, 400)
        p = 10 * np.log10(np.mean(np.abs(iq[:n * blk].reshape(n, blk) - np.mean(iq)) ** 2, axis=1))
        ref = np.median(p[n // 2:])
        bad = np.flatnonzero(np.abs(p - ref) > 0.5)
        settle = (bad.max() + 1) * blk if bad.size and bad.max() < n // 2 else 0
        print('P11 %.1f MHz: level steady (within 0.5 dB) after %d samples (%.1f ms); first block %+.2f dB'
              % (fc / 1e6, settle, settle / fs * 1000, p[0] - ref))


if __name__ == '__main__':
    p1_tile_compute()
    p2_merge_growth()
    p3_p4_full_scan()
    sc = p5_memory()
    try:
        p6_finalisation(sc)
    except ImportError:
        print('P6/P7 need PySide2 (run with the Python 3.8 environment)')
    p10_timing_overhead()
    p11_settling()
