"""Fixed simulator scenarios for regression ("golden") checks.

Each scenario is a scan through the real Scanner.run_scan with the dongle
simulator; metrics() reduces the result to numbers that must not change
between versions unless the change is intended (then the reference file
tests/reference/golden.json is written again: qa_sandbox.py --update-golden).
"""
import json
import os

import numpy as np

import sim

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reference', 'golden.json')
CLEAN = {'clean_ripple': True, 'clean_spurs': True, 'clean_floor': True}


def air_uhf():
    a = sim.Air(noise=0.01, seed=1)
    for k in range(16, 21):                       # own spurs 460.8 ... 576.0
        a.tone(28.8e6 * k, 0.003)
    a.tone(479.3e6, 0.05).tone(503.125e6, 0.02).tone(551.0e6, 0.004)
    a.band(482.19e6, 489.81e6, 0.02)              # DVB-T 7.6 MHz
    a.tone(526.0e6, 1.6)                          # own IEM next to the antenna: clips
    return a


def air_vhf():
    a = sim.Air(noise=0.01, seed=2)
    for k in range(3, 9):
        a.tone(28.8e6 * k, 0.004)
    for f in (88.4, 91.3, 95.1, 100.6, 104.9):
        a.tone(f * 1e6, 0.03)
    a.band(178.19e6, 185.81e6, 0.015)
    return a


SCENARIOS = {
    'uhf_raw': dict(start=470, end=560, air=air_uhf, cfg=dict(gain=29.7)),
    'uhf_clean_autogain': dict(start=470, end=560, air=air_uhf,
                               cfg=dict(gain=29.7, auto_gain=True, clean=CLEAN)),
    'vhf_clean': dict(start=80, end=200, air=air_vhf, cfg=dict(gain=29.7, clean=CLEAN)),
}


def run(name, monkeypatch, tmpdir):
    sc = SCENARIOS[name]
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, sc['start'], sc['end'],
                                   sdr_kwargs={'air': sc['air'](), 'gain_affects': True}, **sc['cfg'])
    return scanner


def _rmed(y, n=81):
    pad = np.pad(y, n // 2, mode='edge')
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        return np.median(sliding_window_view(pad, n), axis=1)
    except ImportError:
        return np.array([np.median(pad[i:i + n]) for i in range(y.size)])


def metrics(scanner):
    f, y = sim.spectrum_arrays(scanner)
    e = y - _rmed(y)
    idx = [i for i in range(2, y.size - 2) if e[i] > 6 and y[i] == y[i - 2:i + 3].max()]
    idx = sorted(idx, key=lambda i: -e[i])[:12]
    info = getattr(scanner.spectrum, 'cleanup_info', None) or {}
    coll = scanner.sample_collection
    return {
        'n': int(f.size), 'f0': round(float(f[0]), 4), 'f1': round(float(f[-1]), 4),
        'median': round(float(np.median(y)), 3),
        'p10': round(float(np.percentile(y, 10)), 3), 'p90': round(float(np.percentile(y, 90)), 3),
        'peaks': sorted([[round(float(f[i]), 4), round(float(y[i]), 2)] for i in idx]),
        'spurs_removed': sorted(info.get('spurs_removed') or []),
        'ripple': info.get('ripple'),
        'overload': [[round(a, 1), round(b, 1)] for a, b, _ in (getattr(coll, 'overload', []) or [])],
        'remeasured': len(getattr(coll, 'remeasured', []) or []),
    }


def compare(got, ref, level_tol=0.1, stat_tol=0.05):
    """List of differences (empty = same)."""
    out = []
    for k in ('n', 'f0', 'f1', 'spurs_removed', 'ripple', 'overload', 'remeasured'):
        if got.get(k) != ref.get(k):
            out.append('%s: %r, reference %r' % (k, got.get(k), ref.get(k)))
    for k in ('median', 'p10', 'p90'):
        if abs(got[k] - ref[k]) > stat_tol:
            out.append('%s: %.3f, reference %.3f' % (k, got[k], ref[k]))
    gf = [p[0] for p in got['peaks']]
    rf = [p[0] for p in ref['peaks']]
    if gf != rf:
        out.append('peak frequencies %s, reference %s' % (gf, rf))
    else:
        for a, b in zip(got['peaks'], ref['peaks']):
            if abs(a[1] - b[1]) > level_tol:
                out.append('peak %.4f MHz: %.2f dB, reference %.2f' % (a[0], a[1], b[1]))
    return out


def load_golden():
    if not os.path.exists(GOLDEN_PATH):
        return None
    with open(GOLDEN_PATH) as fh:
        return json.load(fh)


def save_golden(data):
    with open(GOLDEN_PATH, 'w') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
