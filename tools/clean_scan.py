# -*- coding: utf-8 -*-
"""Try the V17 "clean scan" on scans you already have (V17).

    python clean_scan.py SCAN [--calibration SCAN_WITHOUT_ANTENNA] [--png picture.png]

SCAN can be .csv (WWB or ours), .sdb2, .sdb3 or .npz. Writes SCAN_clean.csv
(WWB layout: kHz, 25 kHz grid) next to it and prints what was removed.

  - tile ripple and the dongle's own spurs: always (no calibration needed)
  - floor levelling: only with --calibration, a full scan 31-1766 MHz made
    WITHOUT antenna with the same gain (e.g. the no-antenna scan of the
    qa kit, scan_no_antenna_31-1766.csv)

The tile plan is taken from the first frequency of the scan (the program
starts the tiles there) and the step (0.775 MHz at 2.4 MS/s); give --start
and --step if the scan was cropped or made with other settings.
"""
from __future__ import print_function

import argparse
import os
import sys

import numpy as np


def load(path):
    from wwb_scanner.file_handlers.importers import BaseImporter
    sp = BaseImporter.import_file(path)
    f = np.asarray(sp.sample_data.frequency, dtype=float)
    y = np.asarray(sp.sample_data['dbFS'], dtype=float)
    o = np.argsort(f)
    return f[o], y[o]


def write_csv(path, f, y):
    lines = []
    for a, b in zip(f, y):
        k = a * 1000.0
        lines.append(('%d' % round(k) if abs(k - round(k)) < 1e-6 else '%.3f' % k) + ',%.1f' % b)
    with open(path, 'w', newline='') as fh:
        fh.write('\n'.join(lines))


def main():
    from wwb_scanner.scanner import cleanup as C
    ap = argparse.ArgumentParser(description='Clean scan (V17) for existing scans')
    ap.add_argument('scan')
    ap.add_argument('--calibration', default=None, help='scan WITHOUT antenna, 31-1766 MHz, same gain')
    ap.add_argument('--start', type=float, default=None, help='first tile centre, MHz (default: first point)')
    ap.add_argument('--step', type=float, default=0.775, help='tile step, MHz (default 0.775)')
    ap.add_argument('--png', default=None, help='also save a picture before/after (needs matplotlib)')
    args = ap.parse_args()

    f, y = load(args.scan)
    bin_mhz = float(np.median(np.diff(f)))
    period = int(round(args.step / bin_mhz))
    start = f[0] if args.start is None else args.start
    profile = None
    if args.calibration:
        cf, cy = load(args.calibration)
        profile = C.build_profile(cf, cy, cf[0], period, meta={'source': os.path.basename(args.calibration)})
        why = C.check_calibration_scan(cf, cy, profile)
        if why:
            print('Calibration scan not used: %s' % why)
            profile = None
        else:
            print('Calibration: floor %.1f dB, %d own spurs, %d outside signals'
                  % (profile['floor_ref_db'], len(profile['spurs']), len(profile['external'])))
    c, info = C.clean(f, y, start, period, profile=profile, default_ripple=C.DEFAULT_RIPPLE_DB)
    print('Scan %s: %.3f-%.3f MHz, %d points' % (os.path.basename(args.scan), f[0], f[-1], f.size))
    print('  tile ripple: %s (%s dB peak-to-peak)' % (info.get('ripple') or 'not removed',
                                                     info.get('ripple_pp_db', '-')))
    gone = info.get('spurs_removed') or []
    print('  own spurs removed: %d  %s' % (len(gone), ' '.join('%g' % g for g in gone[:40])))
    print('  floor levelled: %s' % ('yes, to %.1f dB' % info['floor_ref_db'] if info.get('flatten')
                                    else 'no (needs --calibration)'))
    r0 = y - C.running_median(y, 61)
    r1 = c - C.running_median(c, 61)
    print('  floor roughness (std of points around the 1.5 MHz median): %.2f -> %.2f dB'
          % (np.std(r0[np.abs(r0) < 3]), np.std(r1[np.abs(r1) < 3])))
    out = os.path.splitext(args.scan)[0] + '_clean.csv'
    write_csv(out, f, c)
    print('saved %s' % out)
    if args.png:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError:
            print('matplotlib is not installed: python -m pip install matplotlib')
            return 0
        fig, ax = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
        ax[0].plot(f, y, lw=0.5); ax[0].set_title('as measured'); ax[0].grid(alpha=.3)
        ax[1].plot(f, c, lw=0.5, color='C1'); ax[1].set_title('clean'); ax[1].grid(alpha=.3)
        ax[1].set_xlabel('MHz')
        for a in ax:
            a.set_ylabel('dB')
        fig.tight_layout(); fig.savefig(args.png, dpi=120)
        print('saved %s' % args.png)
    return 0


if __name__ == '__main__':
    sys.exit(main())
