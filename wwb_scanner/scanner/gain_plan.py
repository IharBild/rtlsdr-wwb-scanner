"""V22.7: own gain per band ("Suggest gain A+", decision of 18-19.11.2026).

One gain for a whole wide scan loses sensitivity: the survey of 10.11.2026
(claude/gain-survey-2026-11-10.md) found 470-560 MHz overloading from
22.9 dB on, while 560-700 MHz is 7.6 dB more sensitive at 49.6 dB than at
33.8 dB. With "Own gain per band" on, a short probe before the scan picks a
gain for every band, and each band is scanned with its own gain:

1. **Probe** (a few seconds): every band of the scan (BANDS, the bands of
   the gain survey) is read in steps of about one tile width at the gain of
   the scan ``g0``: PROBE_SAMPLES samples per step, the peak of the ADC
   samples and the share of clipped ones.
2. **Choice**: the gain may go up from ``g0`` by the headroom of the
   strongest step of the band minus MARGIN_DB, using the real gain steps
   (Scan settings > Calibrate gain steps; nominal ones without). Only gains
   with a floor calibration are taken when the floor is levelled, so every
   band is levelled with its own gain. The gain never goes below ``g0``:
   clipped tiles are re-measured with less gain as before (Overload).
3. **Check**: the strongest step of a band whose gain went up is read again
   at the new gain; if it clips or comes closer than MARGIN_DB / 2 to full
   scale, the next lower gain is tried (at most MAX_CHECKS).
4. **Scan**: bands with the same gain are scanned together (scanner/main.py,
   Scanner._run_banded). Levels are brought to the gain of the scan ``g0``
   (the real gain difference is subtracted), so a carrier keeps its level
   across a band edge and the export says, as always, "dBFS/Hz at the gain
   of the scan". The raw data of the bands stay untouched (only shifted
   by the gain difference); the gains are saved with the scan
   (cleanup_info['gain_plan']).

Bands narrower than MIN_BAND_MHZ get the lower gain of their neighbour
(no sub-scan of a few MHz).
"""
import logging
import time

import numpy as np

logger = logging.getLogger(__name__)

# the bands of the gain survey (gain_profile.BANDS_MHZ)
from wwb_scanner.scanner.gain_profile import BANDS_MHZ as BANDS

MARGIN_DB = 6.          # headroom of the ADC left after raising the gain
PEAK_PERCENTILE = 99.9  # peak of the samples (ignores single spikes)
PROBE_SAMPLES = 16384   # per probe step (~7 ms at 2.4 MS/s)
STEP_RATIO = 0.9        # probe step = STEP_RATIO x sample rate
MAX_CHECKS = 3
MIN_BAND_MHZ = 5.


class PlanBand(object):
    def __init__(self, lo, hi, gain, headroom_db=None, clipped=False, strongest_hz=None, why=''):
        self.lo, self.hi = float(lo), float(hi)
        self.gain = float(gain)
        self.headroom_db = headroom_db
        self.clipped = bool(clipped)
        self.strongest_hz = strongest_hz
        self.why = why

    def as_list(self):
        return [round(self.lo, 6), round(self.hi, 6), round(self.gain, 1)]

    def __repr__(self):
        return 'PlanBand(%g-%g MHz, %.1f dB, %s)' % (self.lo, self.hi, self.gain, self.why)


class Plan(object):
    """The gain of every band of a scan; ``ref_gain``: the gain of the scan
    (levels are given at it)."""
    def __init__(self, ref_gain, bands, note='', seconds=0.):
        self.ref_gain = float(ref_gain)
        self.bands = list(bands)
        self.note = note
        self.seconds = seconds

    @property
    def banded(self):
        return any(abs(b.gain - self.ref_gain) > 0.05 for b in self.bands)

    def groups(self):
        """[(gain, [(lo, hi), ...])] - bands of one gain are scanned together."""
        out = {}
        for b in self.bands:
            out.setdefault(round(b.gain, 1), []).append((b.lo, b.hi))
        return [(g, sorted(r)) for g, r in sorted(out.items())]

    def text(self):
        return ', '.join('%g-%g %.1f' % (b.lo, b.hi, b.gain) for b in self.bands) + ' dB'

    def as_list(self):
        return [b.as_list() for b in self.bands]


def split(segments, bands=BANDS):
    """The scan ranges cut at the band edges: [(lo, hi)] MHz in order."""
    edges = sorted({float(e) for b in bands for e in b})
    out = []
    for lo, hi in segments:
        cuts = [lo] + [e for e in edges if lo < e < hi] + [hi]
        out.extend((a, b) for a, b in zip(cuts[:-1], cuts[1:]) if b - a > 1e-6)
    return out


def candidates(gains, g0, has_floor=None):
    """Gains above ``g0`` that may be used (``has_floor(g)``: a floor
    calibration exists, when the floor is levelled)."""
    out = []
    for g in sorted(float(x) for x in gains):
        if g <= g0 + 0.05:
            continue
        if has_floor is not None and not has_floor(g):
            continue
        out.append(g)
    return out


def peak_db(iq):
    """Peak of the I and Q samples (PEAK_PERCENTILE) in dB below full scale."""
    a = np.asarray(iq)
    if a.size == 0:
        return -200.
    m = np.maximum(np.abs(a.real), np.abs(a.imag))
    p = float(np.percentile(m, PEAK_PERCENTILE))
    return 20. * np.log10(max(p, 1e-10))


def choose(g0, cands, headroom_db, drop, clipped=False):
    """The highest gain of ``cands`` whose real rise over ``g0``
    (``drop(g, g0)``) stays within ``headroom_db - MARGIN_DB``; ``g0`` when
    none does (or the band clipped at ``g0``)."""
    if clipped or headroom_db is None:
        return g0
    room = headroom_db - MARGIN_DB
    best = g0
    for g in cands:
        if drop(g, g0) <= room + 1e-9:
            best = max(best, g)
    return best


def _merge(bands):
    """Neighbouring bands of one gain become one; narrow bands take the
    lower gain of a neighbour."""
    bands = [b for b in bands]

    def touching(a, b):
        return abs(a.hi - b.lo) < 1e-6 or abs(b.hi - a.lo) < 1e-6
    for i, b in enumerate(bands):
        if b.hi - b.lo < MIN_BAND_MHZ:
            near = [bands[j].gain for j in (i - 1, i + 1)
                    if 0 <= j < len(bands) and touching(bands[j], b)]
            if near and min(near) < b.gain:
                b.gain = min(near)
                b.why = (b.why + '; ' if b.why else '') + 'narrow band: gain of its neighbour'
    out = []
    for b in bands:
        if out and abs(out[-1].hi - b.lo) < 1e-6 and abs(out[-1].gain - b.gain) < 0.05:
            out[-1].hi = b.hi
            out[-1].why = out[-1].why if out[-1].why == b.why else (out[-1].why + '; ' + b.why)
            continue
        out.append(b)
    return out


def probe_steps(lo, hi, sample_rate):
    """Centre frequencies (Hz) of the probe steps of ``lo``-``hi`` MHz."""
    step = STEP_RATIO * sample_rate
    lo_hz, hi_hz = lo * 1e6, hi * 1e6
    n = max(1, int(np.ceil((hi_hz - lo_hz) / step)))
    return [lo_hz + (k + 0.5) * (hi_hz - lo_hz) / n for k in range(n)]


def make_plan(rx, segments, g0, gains, sample_rate, has_floor=None, drop=None,
              should_stop=None, clip_ratio=None, overload_ratio=1e-3):
    """Probe the bands of ``segments`` with the open receiver ``rx`` (at the
    gain ``g0``) and return the Plan. The gain of ``rx`` is ``g0`` again
    afterwards. Never raises for a band: a band that can not be probed
    keeps ``g0``."""
    from wwb_scanner.scanner import sample_processing as sp
    from wwb_scanner.receivers import as_receiver
    rx = as_receiver(rx)
    clip_ratio = clip_ratio or sp.clip_ratio
    if drop is None:
        def drop(g_hi, g_lo, f_hz=None):
            return float(g_hi) - float(g_lo)
    t0 = time.monotonic()
    cands = candidates(gains, g0, has_floor)
    pieces = split(segments)
    if not cands:
        why = ('no gain above %.1f dB has a floor calibration' % g0 if has_floor is not None
               else 'no gain above %.1f dB' % g0)
        return Plan(g0, [PlanBand(lo, hi, g0, why=why) for lo, hi in pieces], note=why)
    run = sp.run_with_timeout
    bands = []
    try:
        for lo, hi in pieces:
            if should_stop is not None and should_stop():
                bands.append(PlanBand(lo, hi, g0, why='stopped'))
                continue
            worst, worst_hz, clipped = None, None, False
            for c in probe_steps(lo, hi, sample_rate):
                run(rx.tune, sp.TUNE_TIMEOUT, c)
                run(rx.read, sp.READ_TIMEOUT, sp.NUM_SAMPLES_DISCARDED)
                x = run(rx.read, sp.READ_TIMEOUT, PROBE_SAMPLES)
                if clip_ratio(x) >= overload_ratio:
                    clipped = True
                h = -peak_db(x)
                if worst is None or h < worst:
                    worst, worst_hz = h, c
            fd = (lambda g, gg, f=worst_hz: drop(g, gg, f))
            g = choose(g0, cands, worst, fd, clipped)
            why = ('clips at %.1f dB' % g0 if clipped else
                   'headroom %.1f dB at %.3f MHz' % (worst, (worst_hz or 0) / 1e6))
            # check the strongest step at the new gain
            tries = 0
            while g > g0 + 0.05 and tries < MAX_CHECKS:
                tries += 1
                run(rx.tune, sp.TUNE_TIMEOUT, worst_hz)
                run(setattr, sp.TUNE_TIMEOUT, rx, 'gain', g)
                run(rx.read, sp.READ_TIMEOUT, sp.NUM_SAMPLES_DISCARDED)
                x = run(rx.read, sp.READ_TIMEOUT, PROBE_SAMPLES)
                h = -peak_db(x)
                if clip_ratio(x) < overload_ratio and h >= MARGIN_DB / 2.:
                    why += '; checked at %.1f dB: headroom %.1f dB' % (g, h)
                    break
                lower = [c for c in cands if c < g - 0.05]
                why += '; %.1f dB too strong (headroom %.1f dB)' % (g, h)
                g = lower[-1] if lower else g0
            else:
                if g > g0 + 0.05:
                    g = g0
            bands.append(PlanBand(lo, hi, g, headroom_db=worst, clipped=clipped,
                                  strongest_hz=worst_hz, why=why))
    finally:
        try:
            run(setattr, sp.TUNE_TIMEOUT, rx, 'gain', g0)
        except Exception:
            logger.exception('could not set the gain back to %s dB', g0)
    bands = _merge(bands)
    plan = Plan(g0, bands, seconds=time.monotonic() - t0)
    logger.info('gain plan (%.1f s): %s', plan.seconds, '; '.join(
        '%g-%g MHz %.1f dB (%s)' % (b.lo, b.hi, b.gain, b.why) for b in bands))
    return plan


def level_shift(f_mhz, g_band, g0, drop):
    """dB to subtract from levels measured at ``g_band`` to give them at
    ``g0``: the real gain difference at every frequency (evaluated every
    MHz and interpolated)."""
    f = np.asarray(f_mhz, dtype=np.float64)
    if f.size == 0 or abs(g_band - g0) < 0.05:
        return np.zeros(f.size)
    grid = np.arange(np.floor(f.min()), np.ceil(f.max()) + 1.0, 1.0)
    vals = np.array([drop(g_band, g0, x * 1e6) for x in grid], dtype=np.float64)
    return np.interp(f, grid, vals)
