# -*- coding: utf-8 -*-
"""Compare our scan with a Shure WWB scan (V16).

    python compare_wwb.py OUR_SCAN REFERENCE_SCAN [--png picture.png]

Both files can be .csv (WWB or ours), .sdb2, .sdb3 or .npz. For a fair
comparison both scans should be made at the same place and time with the
same antenna (e.g. the antenna moved from the Shure receiver to the dongle).

Report (printed and written next to OUR_SCAN as <name>_vs_<ref>.txt):
  - frequency shift between the scans (cross-correlation, 25 kHz grid)
  - level difference per band (median of ours - median of reference)
  - strong peaks of the reference: found / not found in ours
  - strong peaks only in ours (marked if they are the dongle's own spurs)
"""
from __future__ import print_function

import argparse
import os
import sys

import numpy as np

STEP_MHZ = 0.025
SPURS_MHZ = [round(28.8 * k, 1) for k in range(3, 62)]
BANDS = [(30, 88), (88, 108), (108, 174), (174, 240), (240, 470), (470, 550), (550, 610),
         (610, 694), (694, 790), (790, 870), (870, 1000), (1000, 1500), (1500, 2000)]


def load(path):
    from wwb_scanner.file_handlers.importers import BaseImporter
    sp = BaseImporter.import_file(path)
    f = np.asarray(sp.sample_data.frequency, dtype=float)
    y = np.asarray(sp.sample_data['dbFS'], dtype=float)
    o = np.argsort(f)
    f, y = f[o], y[o]
    ok = np.isfinite(y) & (y > -139.5)
    return f[ok], y[ok]


def running_median(y, n):
    n = max(3, int(n) | 1)
    pad = np.pad(y, n // 2, mode='edge')
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        return np.median(sliding_window_view(pad, n), axis=1)
    except ImportError:
        return np.array([np.median(pad[i:i + n]) for i in range(y.size)])


def peaks(f, y, floor, min_db=6.0, distance_bins=4):
    e = y - floor
    idx = [i for i in range(1, y.size - 1)
           if e[i] >= min_db and y[i] == y[max(0, i - distance_bins):i + distance_bins + 1].max()]
    # flat tops (receiver scans with a wide filter) give several equal
    # maxima next to each other: one peak per 200 kHz, centre of the top
    out = []
    for i in idx:
        if out and f[i] - f[out[-1][-1]] <= 0.2:
            out[-1].append(i)
        else:
            out.append([i])
    res = []
    for grp in out:
        i = grp[len(grp) // 2]
        res.append((f[i], y[i], e[i]))
    return res


def compare(our_path, ref_path):
    fo, yo = load(our_path)
    fr, yr = load(ref_path)
    lo, hi = max(fo.min(), fr.min()), min(fo.max(), fr.max())
    if hi - lo < 1.0:
        raise SystemExit('The scans do not overlap (ours %.1f-%.1f, reference %.1f-%.1f MHz)'
                         % (fo.min(), fo.max(), fr.min(), fr.max()))
    g = np.arange(np.ceil(lo / STEP_MHZ) * STEP_MHZ, hi + 1e-9, STEP_MHZ)
    a = np.interp(g, fo, yo)
    b = np.interp(g, fr, yr)
    L = []
    w = L.append
    w('Ours:      %s  (%.3f-%.3f MHz, %d points)' % (os.path.basename(our_path), fo.min(), fo.max(), fo.size))
    w('Reference: %s  (%.3f-%.3f MHz, %d points)' % (os.path.basename(ref_path), fr.min(), fr.max(), fr.size))
    w('Compared:  %.3f-%.3f MHz on a 25 kHz grid' % (g[0], g[-1]))
    w('')
    # frequency shift: cross-correlation of the detrended spectra, +-1 MHz
    n_med = int(2.0 / STEP_MHZ)
    da = a - running_median(a, n_med)
    db = b - running_median(b, n_med)
    best, best_c = 0, -2.
    for lag in range(-40, 41):
        if lag >= 0:
            x, y = da[lag:], db[:db.size - lag]
        else:
            x, y = da[:lag], db[-lag:]
        if x.size < 50 or x.std() == 0 or y.std() == 0:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best_c:
            best, best_c = lag, c
    w('FREQUENCY: ours is shifted by %+d kHz against the reference (correlation %.2f)'
      % (best * 25, best_c))
    w('  (|shift| <= 25 kHz = one bin: frequencies agree)')
    w('')
    # level per band
    w('LEVEL per band, median dB      ours   reference   ours - ref')
    for blo, bhi in BANDS:
        m = (g >= blo) & (g < bhi)
        if m.sum() < 40:
            continue
        ma, mb = float(np.median(a[m])), float(np.median(b[m]))
        w('  %4d-%-4d MHz             %7.1f   %7.1f     %+6.1f' % (blo, bhi, ma, mb, ma - mb))
    w('  whole range               %7.1f   %7.1f     %+6.1f'
      % (np.median(a), np.median(b), np.median(a) - np.median(b)))
    w('  (RTL-SDR levels are not calibrated dBm: a constant offset is normal,')
    w('   differences between bands show the frequency response)')
    w('')
    # peaks
    fa = running_median(a, n_med)
    fb = running_median(b, n_med)
    pr = sorted(peaks(g, b, fb), key=lambda p: -p[2])[:60]
    po = peaks(g, a, fa, min_db=3.0)
    po_f = np.array([p[0] for p in po]) if po else np.zeros(0)
    found, missed = [], []
    for f0, lvl, ex in pr:
        near = po_f[np.abs(po_f - f0) <= 0.05] if po_f.size else []
        (found if len(near) else missed).append((f0, lvl, ex))
    w('PEAKS of the reference (>= 6 dB over its local floor): %d, found in ours %d, not found %d'
      % (len(pr), len(found), len(missed)))
    for f0, lvl, ex in missed[:20]:
        k = int(np.argmin(np.abs(g - f0)))
        w('  not found: %9.3f MHz  ref +%.1f dB over floor, ours +%.1f dB' % (f0, ex, a[k] - fa[k]))
    pr_f = np.array([p[0] for p in pr]) if pr else np.zeros(0)
    extra = [p for p in sorted(peaks(g, a, fa), key=lambda p: -p[2])[:60]
             if not (pr_f.size and np.any(np.abs(pr_f - p[0]) <= 0.05))]
    w('')
    w('PEAKS only in ours (>= 6 dB): %d' % len(extra))
    for f0, lvl, ex in extra[:20]:
        spur = any(abs(f0 - s) <= 0.06 for s in SPURS_MHZ)
        w('  %9.3f MHz  +%.1f dB%s' % (f0, ex, '   <- own spur of the dongle (k x 28.8 MHz)' if spur else ''))
    return '\n'.join(L), (g, a, b)


def main():
    ap = argparse.ArgumentParser(description='Compare our scan with a Shure WWB scan')
    ap.add_argument('ours')
    ap.add_argument('reference')
    ap.add_argument('--png', default=None, help='also save a picture (needs matplotlib)')
    args = ap.parse_args()
    text, (g, a, b) = compare(args.ours, args.reference)
    print(text)
    base = os.path.splitext(args.ours)[0]
    out = '%s_vs_%s.txt' % (base, os.path.splitext(os.path.basename(args.reference))[0])
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    print('\nsaved %s' % out)
    if args.png:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError:
            print('matplotlib is not installed: python -m pip install matplotlib')
            return 0
        fig, ax = plt.subplots(2, 1, figsize=(14, 7), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
        ax[0].plot(g, b, lw=0.6, label='reference')
        ax[0].plot(g, a, lw=0.6, label='ours')
        ax[0].legend(); ax[0].set_ylabel('dB'); ax[0].grid(alpha=.3)
        ax[1].plot(g, a - b, lw=0.5, color='k'); ax[1].set_ylabel('ours - ref'); ax[1].grid(alpha=.3)
        ax[1].set_xlabel('MHz')
        fig.tight_layout(); fig.savefig(args.png, dpi=120)
        print('saved %s' % args.png)
    return 0


if __name__ == '__main__':
    sys.exit(main())
