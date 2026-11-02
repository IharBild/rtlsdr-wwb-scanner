#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A/B test of two rtlsdr.dll builds on an RTL-SDR Blog V4 (Windows, Python 3.7+).

The script talks to the DLL directly through ctypes (pyrtlsdr is not used),
so each DLL is loaded from the exact path you give. Close every other SDR
program (including the WWB scanner) before running it.

  python dll_ab_test.py where
      Which rtlsdr.dll pyrtlsdr loads now, and whether Python is 32 or 64 bit.

  python dll_ab_test.py ab  OLD\\rtlsdr.dll  NEW\\rtlsdr.dll  [--full]
      Runs both DLLs one after another (separate processes) and compares them.
      About 3 minutes per DLL, --full adds a 30-1766 MHz pass (~4 min per DLL).

  python dll_ab_test.py run --dll PATH --tag NAME [--full]
  python dll_ab_test.py compare results_A.json results_B.json

Results go to the folder "ab_results" next to this script:
results_<tag>.json for each DLL and compare_<A>_vs_<B>.txt.
"""
from __future__ import print_function

import argparse
import ctypes
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
from ctypes import POINTER, byref, c_char_p, c_int, c_uint32, c_ubyte, c_void_p

import numpy as np

# Same settings as the scanner (fix-V7)
FS = 2400000            # sample rate, Hz
GAIN_TENTHS = 338       # 33.8 dB
STEP = 775000           # tile step, Hz
DISCARD = 16384         # samples dropped after each retune
SAMPLES = 2048 * 40     # samples used per tile
NFFT = 96               # 25 kHz bins at 2.4 MHz
SCAN_RANGE = (470000000, 800000000)
FULL_RANGE = (30000000, 1766000000)
PSD_FREQS = [490000000, 600000000, 700000000, 725000000, 750000000]
NOISE_FREQS = [700000000, 725000000, 750000000]   # empty band in your scans
PSD_REPEATS = 5
SETTLE_JUMPS = [(600000000, 490000000), (490000000, 600000000), (470000000, 1500000000)]
SETTLE_BLOCK = 1024
SETTLE_READ = 65536

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "ab_results")


# ---------------------------------------------------------------- DLL access
class Rtl(object):
    def __init__(self, path):
        self.path = os.path.abspath(path)
        try:
            self.lib = ctypes.CDLL(self.path)
        except OSError as e:
            msg = str(e)
            if "193" in msg:
                msg += ("\n-> 32/64-bit mismatch: this Python is %d-bit; use the x64 DLL "
                        "for 64-bit Python and the x86 DLL for 32-bit." % (struct.calcsize("P") * 8))
            raise SystemExit("Cannot load %s: %s" % (self.path, msg))
        L = self.lib
        L.rtlsdr_get_device_count.restype = c_uint32
        L.rtlsdr_get_device_usb_strings.argtypes = [c_uint32, c_char_p, c_char_p, c_char_p]
        L.rtlsdr_open.argtypes = [POINTER(c_void_p), c_uint32]
        for name, args in (("rtlsdr_close", [c_void_p]),
                           ("rtlsdr_set_sample_rate", [c_void_p, c_uint32]),
                           ("rtlsdr_set_tuner_gain_mode", [c_void_p, c_int]),
                           ("rtlsdr_set_tuner_gain", [c_void_p, c_int]),
                           ("rtlsdr_get_tuner_gains", [c_void_p, POINTER(c_int)]),
                           ("rtlsdr_set_agc_mode", [c_void_p, c_int]),
                           ("rtlsdr_set_center_freq", [c_void_p, c_uint32]),
                           ("rtlsdr_reset_buffer", [c_void_p]),
                           ("rtlsdr_get_tuner_type", [c_void_p]),
                           ("rtlsdr_read_sync", [c_void_p, c_void_p, c_int, POINTER(c_int)])):
            f = getattr(L, name)
            f.argtypes = args
            f.restype = c_int
        self.has_pll = hasattr(L, "rtlsdr_get_tuner_pll_lock")
        if self.has_pll:
            L.rtlsdr_get_tuner_pll_lock.argtypes = [c_void_p, POINTER(c_uint32), POINTER(c_uint32)]
            L.rtlsdr_get_tuner_pll_lock.restype = c_int
        self.build_info = "(no rtlsdr_get_build_info: stock driver)"
        if hasattr(L, "rtlsdr_get_build_info"):
            L.rtlsdr_get_build_info.restype = c_char_p
            self.build_info = L.rtlsdr_get_build_info().decode("ascii", "replace")
        self.dev = c_void_p()
        self.buf = None

    def loaded_path(self):
        return module_path(self.lib._handle) or self.path

    def open(self):
        n = self.lib.rtlsdr_get_device_count()
        if n < 1:
            raise SystemExit("No RTL-SDR found (is it plugged in, WinUSB installed with Zadig?)")
        m, p, s = (ctypes.create_string_buffer(256) for _ in range(3))
        self.lib.rtlsdr_get_device_usb_strings(0, m, p, s)
        self.usb = "%s / %s / %s" % (m.value.decode(errors="replace"), p.value.decode(errors="replace"),
                                     s.value.decode(errors="replace"))
        r = self.lib.rtlsdr_open(byref(self.dev), 0)
        if r < 0:
            raise SystemExit("rtlsdr_open failed (%d). Close other SDR programs and try again." % r)
        self.lib.rtlsdr_set_sample_rate(self.dev, FS)
        gains = (c_int * 64)()
        ng = self.lib.rtlsdr_get_tuner_gains(self.dev, gains)
        gain = min(list(gains)[:max(ng, 0)] or [GAIN_TENTHS], key=lambda g: abs(g - GAIN_TENTHS))
        self.lib.rtlsdr_set_tuner_gain_mode(self.dev, 1)
        self.lib.rtlsdr_set_tuner_gain(self.dev, gain)
        self.lib.rtlsdr_set_agc_mode(self.dev, 0)
        self.lib.rtlsdr_reset_buffer(self.dev)
        self.gain = gain
        self.tuner = self.lib.rtlsdr_get_tuner_type(self.dev)

    def close(self):
        if self.dev:
            self.lib.rtlsdr_close(self.dev)
            self.dev = c_void_p()

    def tune(self, f):
        return self.lib.rtlsdr_set_center_freq(self.dev, int(f))

    def pll(self):
        if not self.has_pll:
            return None, None, None
        late, unl = c_uint32(), c_uint32()
        lk = self.lib.rtlsdr_get_tuner_pll_lock(self.dev, byref(late), byref(unl))
        return lk, late.value, unl.value

    def read(self, nsamp):
        nbytes = 2 * nsamp
        if self.buf is None or len(self.buf) < nbytes:
            self.buf = (c_ubyte * nbytes)()
        got = c_int(0)
        r = self.lib.rtlsdr_read_sync(self.dev, self.buf, nbytes, byref(got))
        if r < 0:
            raise IOError("rtlsdr_read_sync failed (%d)" % r)
        raw = np.frombuffer(self.buf, dtype=np.uint8, count=got.value).astype(np.float32)
        raw = (raw - 127.4) / 128.0
        return raw[0::2] + 1j * raw[1::2], got.value == nbytes


def module_path(handle):
    if os.name != "nt":
        return None
    buf = ctypes.create_unicode_buffer(1024)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetModuleFileNameW.argtypes = [c_void_p, ctypes.c_wchar_p, c_uint32]
    if k32.GetModuleFileNameW(c_void_p(handle), buf, 1024):
        return buf.value
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


# ---------------------------------------------------------------- DSP helpers
_WIN = np.hanning(NFFT + 1)[:-1]    # periodic Hann, like scipy 'hann'


def psd_db(iq):
    hop = NFFT // 2
    idx = np.arange(0, len(iq) - NFFT + 1, hop)
    frames = iq[idx[:, None] + np.arange(NFFT)] * _WIN
    p = np.mean(np.abs(np.fft.fft(frames, axis=1)) ** 2, axis=0) / np.sum(_WIN) ** 2
    return 10 * np.log10(np.fft.fftshift(p) + 1e-20)


def used_bins():
    """Bins the scanner keeps: |offset| <= 0.39*Fs and > 30 kHz from the centre."""
    off = (np.arange(NFFT) - NFFT // 2) * (FS / float(NFFT))
    return np.where((np.abs(off) <= 0.39 * FS) & (np.abs(off) > 30000))[0]


def plan(rng):
    return list(range(rng[0], rng[1] + 1, STEP)) + ([rng[1]] if (rng[1] - rng[0]) % STEP else [])


def pct(a, q):
    return float(np.percentile(np.asarray(a), q)) if len(a) else None


# ---------------------------------------------------------------- tests
def test_retune(rtl, passes=3):
    freqs = plan(SCAN_RANGE)
    ms, errors, unlocked = [], 0, []
    for p in range(passes):
        rtl.tune(freqs[0] - STEP)
        for f in freqs:
            t0 = time.perf_counter()
            r = rtl.tune(f)
            ms.append((time.perf_counter() - t0) * 1000.0)
            if r != 0:
                errors += 1
            lk = rtl.pll()[0]
            if lk == 0:
                unlocked.append(f)
        print("  retune pass %d/%d: median %.1f ms" % (p + 1, passes, pct(ms[-len(freqs):], 50)))
    return {"calls": len(ms), "median_ms": pct(ms, 50), "p95_ms": pct(ms, 95), "max_ms": max(ms),
            "mean_ms": float(np.mean(ms)), "errors": errors, "unlocked_freqs": unlocked}


def test_scan(rtl, rng, passes):
    freqs = plan(rng)
    times, short = [], 0
    for p in range(passes):
        rtl.tune(freqs[0] - STEP)
        rtl.read(DISCARD)
        t0 = time.perf_counter()
        for i, f in enumerate(freqs):
            rtl.tune(f)
            _, ok = rtl.read(DISCARD + SAMPLES)
            short += 0 if ok else 1
            if i and i % 500 == 0:
                print("    tile %d/%d" % (i, len(freqs)))
        times.append(time.perf_counter() - t0)
        print("  scan %d-%d MHz pass %d/%d: %.1f s (%d tiles)" % (rng[0] // 10**6, rng[1] // 10**6, p + 1,
                                                               passes, times[-1], len(freqs)))
    return {"range_mhz": [rng[0] / 1e6, rng[1] / 1e6], "tiles": len(freqs), "seconds": times,
            "ms_per_tile": 1000.0 * min(times) / len(freqs), "short_reads": short}


def test_psd(rtl):
    out = {}
    for f in PSD_FREQS:
        rows = []
        for _ in range(PSD_REPEATS):
            rtl.tune(f - STEP)
            rtl.tune(f)
            iq, _ = rtl.read(DISCARD + SAMPLES)
            rows.append(psd_db(iq[DISCARD:]))
        rows = np.array(rows)
        out[str(f)] = {"mean_db": rows.mean(axis=0).round(3).tolist(), "std_db": rows.std(axis=0).round(3).tolist()}
        print("  PSD %.1f MHz: median %.2f dB" % (f / 1e6, float(np.median(rows.mean(axis=0)[used_bins()]))))
    return out


def settle_samples(iq):
    """Samples after the retune call until block power stays within 0.5 dB of the final level."""
    nb = len(iq) // SETTLE_BLOCK
    p = 10 * np.log10(np.mean(np.abs(iq[:nb * SETTLE_BLOCK].reshape(nb, SETTLE_BLOCK)) ** 2, axis=1) + 1e-20)
    final = np.median(p[int(nb * 0.75):])
    bad = np.where(np.abs(p - final) > 0.5)[0]
    return int((bad[-1] + 1) * SETTLE_BLOCK) if len(bad) else 0, p.round(2).tolist()


def test_settle(rtl):
    out = []
    for a, b in SETTLE_JUMPS:
        for mode in ("plain", "reset"):
            res = []
            for _ in range(3):
                rtl.tune(a)
                rtl.read(DISCARD + SAMPLES)
                rtl.tune(b)
                if mode == "reset":
                    rtl.lib.rtlsdr_reset_buffer(rtl.dev)
                iq, _ = rtl.read(SETTLE_READ)
                res.append(settle_samples(iq)[0])
            out.append({"from_mhz": a / 1e6, "to_mhz": b / 1e6, "mode": mode, "settle_samples": res})
            print("  settle %.0f->%.0f MHz (%s): %s samples" % (a / 1e6, b / 1e6, mode, res))
    return out


def run(dll, tag, full):
    rtl = Rtl(dll)
    print("DLL  : %s" % rtl.loaded_path())
    print("build: %s" % rtl.build_info)
    rtl.open()
    print("USB  : %s, tuner type %d, gain %.1f dB" % (rtl.usb, rtl.tuner, rtl.gain / 10.0))
    res = {"tag": tag, "dll": rtl.loaded_path(), "sha256": sha256(rtl.path), "build_info": rtl.build_info,
           "python_bits": struct.calcsize("P") * 8, "usb": rtl.usb, "tuner_type": rtl.tuner,
           "gain_db": rtl.gain / 10.0, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "settings": {"fs": FS, "step": STEP, "discard": DISCARD, "samples": SAMPLES, "nfft": NFFT}}
    try:
        print("1/4 retune timing"); res["retune"] = test_retune(rtl)
        print("2/4 scan timing");   res["scan"] = test_scan(rtl, SCAN_RANGE, 2)
        if full:
            res["scan_full"] = test_scan(rtl, FULL_RANGE, 1)
        print("3/4 spectra");       res["psd"] = test_psd(rtl)
        print("4/4 settling");      res["settle"] = test_settle(rtl)
        lk, late, unl = rtl.pll()
        res["pll"] = {"api": rtl.has_pll, "lock_now": lk, "late_locks": late, "unlocks": unl}
    finally:
        rtl.close()
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    path = os.path.join(OUTDIR, "results_%s.json" % tag)
    with open(path, "w") as f:
        json.dump(res, f, indent=1)
    print("saved %s" % path)
    return path


# ---------------------------------------------------------------- compare
def compare(pa, pb):
    A, B = json.load(open(pa)), json.load(open(pb))
    ub = used_bins()
    L = []
    w = L.append
    w("A = %s : %s" % (A["tag"], A["build_info"]))
    w("    %s  sha256 %s" % (A["dll"], A["sha256"][:16]))
    w("B = %s : %s" % (B["tag"], B["build_info"]))
    w("    %s  sha256 %s" % (B["dll"], B["sha256"][:16]))
    w("USB %s | gain %.1f dB | Python %d-bit | %s" % (B["usb"], B["gain_db"], B["python_bits"], B["time"]))
    w("")
    ra, rb = A["retune"], B["retune"]
    w("RETUNE (set_center_freq, 470-800 MHz, %d calls)      A        B" % rb["calls"])
    for k, n in (("median_ms", "median, ms"), ("p95_ms", "95%, ms"), ("max_ms", "max, ms")):
        w("  %-44s %7.1f  %7.1f" % (n, ra[k], rb[k]))
    w("  %-44s %7d  %7d" % ("errors (return != 0)", ra["errors"], rb["errors"]))
    w("  %-44s %7s  %7s" % ("PLL not locked (calls)", "n/a" if not A["pll"]["api"] else len(ra["unlocked_freqs"]),
                            "n/a" if not B["pll"]["api"] else len(rb["unlocked_freqs"])))
    if B["pll"]["api"]:
        w("  PLL totals in B: late locks %s, unlocks %s" % (B["pll"]["late_locks"], B["pll"]["unlocks"]))
        if rb["unlocked_freqs"]:
            w("  B unlocked at MHz: %s" % ", ".join("%.3f" % (f / 1e6) for f in rb["unlocked_freqs"][:30]))
    w("")
    sa, sb = A["scan"], B["scan"]
    w("SCAN 470-800 MHz (%d tiles, retune + %d samples read)  A        B" % (sb["tiles"], DISCARD + SAMPLES))
    w("  %-44s %7.1f  %7.1f" % ("best pass, s", min(sa["seconds"]), min(sb["seconds"])))
    w("  %-44s %7.1f  %7.1f" % ("ms per tile", sa["ms_per_tile"], sb["ms_per_tile"]))
    for name, tiles in (("470-1000 MHz", len(plan((470000000, 1000000000)))), ("30-1766 MHz", len(plan(FULL_RANGE)))):
        w("  %-44s %7.1f  %7.1f" % ("estimate %s (%d tiles), s" % (name, tiles),
                                     sa["ms_per_tile"] * tiles / 1000.0, sb["ms_per_tile"] * tiles / 1000.0))
    w("  %-44s %7d  %7d" % ("short reads", sa["short_reads"], sb["short_reads"]))
    if "scan_full" in A and "scan_full" in B:
        w("  %-44s %7.1f  %7.1f" % ("measured 30-1766 MHz, s", A["scan_full"]["seconds"][0], B["scan_full"]["seconds"][0]))
    w("  (without the scanner's FFT/merge time, ~14 ms per tile in V7)")
    w("")
    w("SPECTRA, scanner bins only (%d of %d), B - A, dB" % (len(ub), NFFT))
    w("  freq MHz   mean diff  max |diff|  repeat std A / B   level A")
    worst_noise = 0.0
    for f in PSD_FREQS:
        a, b = A["psd"][str(f)], B["psd"][str(f)]
        ma, mb = np.array(a["mean_db"])[ub], np.array(b["mean_db"])[ub]
        d = mb - ma
        sda, sdb = float(np.median(np.array(a["std_db"])[ub])), float(np.median(np.array(b["std_db"])[ub]))
        tag = " (empty band)" if f in NOISE_FREQS else ""
        if f in NOISE_FREQS:
            worst_noise = max(worst_noise, abs(float(np.mean(d))))
        w("  %8.3f   %+8.2f   %9.2f    %5.2f / %5.2f   %8.2f%s" % (f / 1e6, float(np.mean(d)), float(np.max(np.abs(d))),
                                                               sda, sdb, float(np.median(ma)), tag))
    w("  verdict: %s" % ("same within 0.3 dB on the empty band" if worst_noise <= 0.3 else
                         "LEVEL DIFFERENCE %.2f dB on the empty band - send me the json files" % worst_noise))
    w("")
    w("SETTLING after retune (samples until power is within 0.5 dB, 1024-sample blocks)")
    for x, y in zip(A["settle"], B["settle"]):
        w("  %6.0f -> %6.0f MHz %-6s  A %-24s B %s" % (x["from_mhz"], x["to_mhz"], x["mode"],
                                                     x["settle_samples"], y["settle_samples"]))
    w("  (the scanner drops %d samples after each retune)" % DISCARD)
    text = "\n".join(L)
    print(text)
    out = os.path.join(OUTDIR, "compare_%s_vs_%s.txt" % (A["tag"], B["tag"]))
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    with open(out, "w") as f:
        f.write(text + "\n")
    print("\nsaved %s" % out)


def where():
    print("Python %s, %d-bit, %s" % (sys.version.split()[0], struct.calcsize("P") * 8, sys.executable))
    try:
        from rtlsdr import librtlsdr as L
    except Exception as e:
        print("pyrtlsdr could not load a DLL: %s" % e)
        return
    lib = L.librtlsdr
    print("pyrtlsdr loads: %s" % (module_path(lib._handle) or "(unknown path)"))
    if hasattr(lib, "rtlsdr_get_build_info"):
        lib.rtlsdr_get_build_info.restype = c_char_p
        print("build: %s" % lib.rtlsdr_get_build_info().decode())
    else:
        print("build: stock driver (no scanner-v4 functions)")
    try:
        import pyrtlsdrlib
        print("NOTE: pyrtlsdrlib is installed, pyrtlsdr prefers its bundled DLL: %s" % pyrtlsdrlib.__file__)
    except ImportError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("where")
    r = sub.add_parser("run"); r.add_argument("--dll", required=True); r.add_argument("--tag", required=True)
    r.add_argument("--full", action="store_true")
    b = sub.add_parser("ab"); b.add_argument("old"); b.add_argument("new"); b.add_argument("--full", action="store_true")
    c = sub.add_parser("compare"); c.add_argument("a"); c.add_argument("b")
    a = ap.parse_args()
    if a.cmd == "where":
        where()
    elif a.cmd == "run":
        run(a.dll, a.tag, a.full)
    elif a.cmd == "compare":
        compare(a.a, a.b)
    elif a.cmd == "ab":
        paths = []
        for dll, tag in ((a.old, "old"), (a.new, "new")):
            print("\n===== %s: %s" % (tag, dll))
            cmd = [sys.executable, os.path.abspath(__file__), "run", "--dll", dll, "--tag", tag]
            if a.full:
                cmd.append("--full")
            if subprocess.call(cmd) != 0:
                raise SystemExit("run failed for %s" % dll)
            paths.append(os.path.join(OUTDIR, "results_%s.json" % tag))
            time.sleep(2)
        print("\n===== comparison")
        compare(*paths)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
