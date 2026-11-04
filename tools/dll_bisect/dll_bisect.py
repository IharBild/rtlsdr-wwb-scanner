#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Find which change in the scanner-v4 rtlsdr.dll raises the noise floor.

Runs several builds of the same driver one after another on your RTL-SDR
Blog V4 and measures, with the antenna DISCONNECTED (empty connector):
  - the dongle's own noise floor at 8 frequencies 60-1600 MHz,
  - the internal spurs (multiples of 28.8 MHz),
  - retune time,
  - driver error messages (the "i2c wr failed ... reg=06" seen at close).
The first build (RTL-SDR Blog driver of 2023) is measured at the start AND at the end,
so we also see how much the dongle drifts while it warms up.

  python dll_bisect.py            all builds from the x64 folder (~5 min)
  python dll_bisect.py where      which Python / DLL is used now

Close the WWB scanner and every other SDR program first.
Results: folder "bisect_results" next to this script (report.txt + json).
Uses dll_ab_test.py (must lie next to this file).
"""
from __future__ import print_function

import glob
import json
import os
import struct
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dll_ab_test as ab  # noqa: E402

OUTDIR = os.path.join(HERE, "bisect_results")
NOISE_MHZ = [60, 175, 400, 600, 725, 1000, 1300, 1600]
SPUR_MHZ = [86.4, 144.0, 259.2, 288.0, 316.8, 403.2]
SPUR_OFFSET = 300000          # spur placed 300 kHz left of the tile centre
REPEATS = 5
WARMUP_S = 90


def measure_floor(rtl, f):
    vals = []
    for _ in range(REPEATS):
        rtl.tune(f - ab.STEP)
        rtl.tune(f)
        iq, _ = rtl.read(ab.DISCARD + ab.SAMPLES)
        vals.append(ab.psd_db(iq[ab.DISCARD:]))
    m = np.mean(vals, axis=0)
    return float(np.median(m[ab.used_bins()])), float(np.median(np.std(vals, axis=0)[ab.used_bins()]))


def measure_spur(rtl, f):
    c = int(round(f * 1e6)) + SPUR_OFFSET
    vals = []
    for _ in range(REPEATS):
        rtl.tune(c - ab.STEP)
        rtl.tune(c)
        iq, _ = rtl.read(ab.DISCARD + ab.SAMPLES)
        vals.append(ab.psd_db(iq[ab.DISCARD:]))
    m = np.mean(vals, axis=0)
    k = ab.NFFT // 2 - int(round(SPUR_OFFSET / (ab.FS / float(ab.NFFT))))
    peak = float(np.max(m[k - 1:k + 2]))
    floor = float(np.median(m[ab.used_bins()]))
    return peak, peak - floor


def retune_ms(rtl, n=200):
    freqs = ab.plan(ab.SCAN_RANGE)[:n]
    rtl.tune(freqs[0] - ab.STEP)
    ms = []
    for f in freqs:
        t0 = time.perf_counter()
        rtl.tune(f)
        ms.append((time.perf_counter() - t0) * 1000.0)
    return float(np.median(ms))


def one(dll, tag, warmup):
    rtl = ab.Rtl(dll)
    print("  build: %s" % rtl.build_info)
    rtl.open()
    res = {"tag": tag, "dll": rtl.loaded_path(), "sha256": ab.sha256(rtl.path),
           "build_info": rtl.build_info, "gain_db": rtl.gain / 10.0,
           "time": time.strftime("%Y-%m-%d %H:%M:%S"), "floor": {}, "spur": {}}
    try:
        if warmup:
            print("  warm-up %d s (dongle streams, nothing is measured)..." % warmup)
            t_end = time.time() + warmup
            rtl.tune(600000000)
            while time.time() < t_end:
                rtl.read(262144)
        res["retune_ms"] = retune_ms(rtl)
        for f in NOISE_MHZ:
            lvl, sd = measure_floor(rtl, int(f * 1e6))
            res["floor"][str(f)] = [lvl, sd]
        for f in SPUR_MHZ:
            res["spur"][str(f)] = measure_spur(rtl, f)
        res["pll"] = list(rtl.pll())
        print("  floor %s" % "  ".join("%g:%.1f" % (f, res["floor"][str(f)][0]) for f in NOISE_MHZ))
        print("  retune %.1f ms" % res["retune_ms"])
    finally:
        rtl.close()
    path = os.path.join(OUTDIR, "bisect_%s.json" % tag)
    with open(path, "w") as fh:
        json.dump(res, fh, indent=1)


def report(runs):
    L = []
    w = L.append
    base = runs[0]
    w("DLL bisect, antenna disconnected, gain %.1f dB, %s" % (base["gain_db"], base["time"]))
    w("")
    w("Builds:")
    for r in runs:
        w("  %-14s %s" % (r["tag"], r["build_info"]))
    w("")
    w("NOISE FLOOR, dB (25 kHz bins), difference to the first stock run")
    w("  %-14s" % "build" + "".join("%9s" % ("%g MHz" % f) for f in NOISE_MHZ) + "   mean diff  retune ms")
    for r in runs:
        lv = [r["floor"][str(f)][0] for f in NOISE_MHZ]
        d = [a - base["floor"][str(f)][0] for a, f in zip(lv, NOISE_MHZ)]
        w("  %-14s" % r["tag"] + "".join("%9.1f" % v for v in lv) + "   %+8.2f   %7.1f" % (np.mean(d), r["retune_ms"]))
        if r is not base:
            w("  %-14s" % "" + "".join("%+9.1f" % v for v in d))
    w("")
    w("SPURS (k x 28.8 MHz), dB above the floor of the same tile")
    w("  %-14s" % "build" + "".join("%9s" % ("%g" % f) for f in SPUR_MHZ))
    for r in runs:
        w("  %-14s" % r["tag"] + "".join("%9.1f" % r["spur"][str(f)][1] for f in SPUR_MHZ))
    w("")
    drift = [r for r in runs if r["tag"].startswith(base["tag"].split("#")[0]) and r is not base]
    if drift:
        d = np.mean([drift[-1]["floor"][str(f)][0] - base["floor"][str(f)][0] for f in NOISE_MHZ])
        w("Drift of the stock build between first and last run: %+.2f dB" % d)
        w("(differences smaller than about twice this are noise, not the DLL)")
    w("")
    w("Driver messages (I2C / PLL errors, incl. at close):")
    for r in runs:
        msgs = r.get("driver_messages", [])
        w("  %-14s %s" % (r["tag"], "; ".join(msgs[:5]) if msgs else "none"))
    w("")
    w("PLL (lock, late, unlocked): " + ", ".join("%s %s" % (r["tag"], r.get("pll")) for r in runs))
    return "\n".join(L)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "where":
        ab.where()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "_one":
        one(sys.argv[2], sys.argv[3], int(sys.argv[4]))
        return
    bits = struct.calcsize("P") * 8
    print("Python %s, %d-bit" % (sys.version.split()[0], bits))
    if bits != 64:
        raise SystemExit("These builds are 64-bit (x64). Tell me that your Python is 32-bit, I will build x86.")
    builds = sorted(glob.glob(os.path.join(HERE, "x64", "*", "rtlsdr.dll")))
    if not builds:
        raise SystemExit("No x64\\*\\rtlsdr.dll next to this script")
    order = [(b, os.path.basename(os.path.dirname(b))) for b in builds]
    again = [builds[0]] + [b for b in builds if "sv4_full" in b][:1]
    order += [(b, os.path.basename(os.path.dirname(b)) + "#end") for b in again]
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    print("Antenna must be DISCONNECTED. %d runs, about 5 minutes.\n" % len(order))
    runs = []
    for i, (dll, tag) in enumerate(order):
        print("===== %d/%d %s" % (i + 1, len(order), tag))
        cmd = [sys.executable, os.path.abspath(__file__), "_one", dll, tag, str(WARMUP_S if i == 0 else 0)]
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE)
        err = proc.communicate()[1].decode("ascii", "replace")
        sys.stdout.write(err)
        if proc.returncode != 0:
            print("  FAILED, skipped")
            continue
        r = json.load(open(os.path.join(OUTDIR, "bisect_%s.json" % tag)))
        r["driver_messages"] = [l.strip() for l in err.splitlines()
                                if "failed" in l or "PLL" in l or "error" in l.lower()]
        runs.append(r)
        time.sleep(2)
    if not runs:
        raise SystemExit("nothing measured")
    text = report(runs)
    print("\n" + text)
    with open(os.path.join(OUTDIR, "report.txt"), "w") as fh:
        fh.write(text + "\n")
    print("\nsaved %s  - send me this file" % os.path.join(OUTDIR, "report.txt"))


if __name__ == "__main__":
    main()
