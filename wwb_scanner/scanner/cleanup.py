# -*- coding: utf-8 -*-
"""Clean scan (V17): remove what the dongle adds by itself.

Three corrections, all done after the scan in a fraction of a second
(no extra measuring time):

1. Tile ripple. Every tile is 2.4 MHz wide and the dongle's filter is not
   flat inside it, so the floor has a small wave (about +-0.2 dB) that
   repeats with the tile step (0.775 MHz = 31 points). It is measured from
   the scan itself (median over all tiles) and removed.

2. Own spurs. The dongle makes its own carriers at multiples of 14.4 MHz
   (half of its 28.8 MHz crystal; V20.9: the odd multiples too) and at
   480 MHz. Such a carrier is hidden only while it is narrow and not more
   than SPUR_HIDE_MAX_DB above the floor; a stronger narrow carrier on
   that grid is kept and reported - it may be a transmitter.

3. Floor shape. Without antenna the floor is not flat (a hump below
   300 MHz, falling 2 dB above 1 GHz). The calibration records that shape
   and the scan is divided by it, so the floor is level and a carrier
   3 dB above the floor shows 3 dB above it in every band.

The calibration is valid only for the same gain and sample rate.
All functions are plain numpy; dB here is 10*log10(power).
"""
from __future__ import division

import json
import logging
import os
import shutil
import time

import numpy as np

logger = logging.getLogger('wwb_scanner.cleanup')

PROFILE_VERSION = 2        # V20.9: floor without the signals that leak in without antenna
BIN_MHZ = 0.025
FLOOR_BINS = 81            # running median for the floor shape: 2 MHz
SPUR_DB = 0.5              # a calibration point this much over the floor is a spur
SPUR_WIDEN = 2             # spur area +-2 points (window leakage)
# V20.9: half of the 28.8 MHz crystal. The odd multiples (475.2, 504.0,
# 532.8 ... MHz) were seen in every scan of 11-12.11.2026: +6.8 dB without
# antenna at 49.6 dB gain, up to +16.5 dB with the antenna.
SPUR_STEP_MHZ = 14.4
# V20.9: a narrow carrier on the spur grid is hidden only up to this level
# above the floor around it. Strongest own spur measured with the antenna:
# +16.5 dB (504.0 MHz, 49.6 dB), +12.8 dB at 33.8 dB; a microphone next to
# the antenna is +35 dB and more. Where the calibration (no antenna) shows
# a stronger own spur (86.4 MHz: +21.5 dB at 49.6 dB) the limit is that
# level + SPUR_CAL_MARGIN_DB.
SPUR_HIDE_MAX_DB = 20.0
SPUR_CAL_MARGIN_DB = 6.0
SPUR_NEAR_MHZ = 0.05
EXTRA_SPURS_MHZ = [480.0]  # USB 2.0 clock of the dongle
EXTERNAL_DB = 3.0          # other peaks in the calibration = transmitters nearby
SPUR_MIN_DB = 0.5          # spur centre at least this much over its surroundings
SPUR_SHOULDER_DB = 1.0     # points 75-100 kHz away at most this much over them
RIPPLE_MIN_PERIODS = 40    # shorter scans use the ripple of the calibration
RIPPLE_CLIP_DB = 3.0       # points further than this from the floor are signals
GAIN_TOLERANCE_DB = 0.6
# V20.9: strong transmitters reach the dongle even without antenna (user's
# calibration of 12.11.2026 at 49.6 dB: TV 518-526 MHz +5.6 dB, 423-424 MHz
# +11 dB, GSM 930-942 MHz +4 dB in the floor shape, so every scan was
# lowered by that much there). Such a signal is a bump: the floor goes up
# and comes down again within LEAK_MAX_MHZ. The dongle's own floor has
# steps (75, 250, 278 MHz: up to +3.7 dB) that go up and stay - not touched.
LEAK_GRID_BINS = 20        # the floor shape is looked at every 0.5 MHz
LEAK_EDGE_STEPS = 5        # an edge: LEAK_DB within 2.5 MHz
LEAK_DB = 1.0
LEAK_RETURN_DB = 0.5       # "down again": this close to the level before the bump
LEAK_FALL_DB = 0.5         # ... with a falling edge of at least this much within 2.5 MHz
LEAK_MAX_MHZ = 24.0
LEAK_MARGIN_MHZ = 1.0

# Ripple measured on the user's RTL-SDR Blog V4 (4 scans, 31-1766 MHz,
# 2.4 MS/s, 25 kHz points, step 0.775 MHz); used when a scan is too short
# to measure its own and there is no calibration.
DEFAULT_RIPPLE_DB = [
    0.20, 0.20, 0.20, 0.20, 0.13, 0.10, 0.00, 0.10, 0.10, 0.00, 0.00, -0.10, -0.10,
    -0.20, -0.20, -0.20, -0.20, -0.20, -0.20, -0.13, -0.10, -0.10, 0.00, 0.05, 0.10,
    -0.20, -0.10, 0.00, 0.10, 0.20, 0.18]
DEFAULT_RIPPLE_FS = 2.4e6
DEFAULT_RIPPLE_STEP_HZ = 775000


def to_db(p):
    return 10.0 * np.log10(np.maximum(p, np.finfo(np.float64).tiny))


def from_db(d):
    return 10.0 ** (np.asarray(d, dtype=np.float64) / 10.0)


def running_median(y, n, ends='edge'):
    """Median over n points. ends='odd' continues the slope at both ends
    (point-mirrored around the median of the end points) instead of
    repeating the last value, so a sloped floor is not bent at the ends."""
    y = np.asarray(y, dtype=np.float64)
    n = max(3, int(n) | 1)
    if y.size == 0:
        return y.copy()
    h = n // 2
    if ends == 'odd' and y.size > n + 2:
        a = np.median(y[:min(y.size, h // 2 + 1)])
        b = np.median(y[-min(y.size, h // 2 + 1):])
        pad = np.concatenate([2 * a - y[h:0:-1], y, 2 * b - y[-2:-h - 2:-1]])
    else:
        pad = np.pad(y, h, mode='edge')
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        return np.median(sliding_window_view(pad, n), axis=1)
    except ImportError:          # numpy < 1.20
        from numpy.lib.stride_tricks import as_strided
        s = pad.strides[0]
        v = as_strided(pad, shape=(y.size, n), strides=(s, s))
        return np.median(v, axis=1)


def _phase(f_mhz, start_mhz, period_bins, bin_mhz=BIN_MHZ):
    return np.rint((np.asarray(f_mhz) - start_mhz) / bin_mhz).astype(np.int64) % period_bins


def measure_ripple(f_mhz, y_db, start_mhz, period_bins, smooth_bins=61):
    """Average wave of the floor over one tile step, from the scan itself.
    Returns (ripple_db[period_bins], number_of_periods)."""
    f = np.asarray(f_mhz, dtype=np.float64)
    y = np.asarray(y_db, dtype=np.float64)
    if f.size < 3 * period_bins:
        return None, 0
    r = y - running_median(y, smooth_bins)
    ph = _phase(f, start_mhz, period_bins)
    ok = np.abs(r) < RIPPLE_CLIP_DB
    out = np.zeros(period_bins)
    for p in range(period_bins):
        v = r[ok & (ph == p)]
        if v.size < 5:
            return None, 0
        # trimmed mean: finer than the median of 0.1 dB steps from a CSV
        lo, hi = np.percentile(v, [20, 80])
        v = v[(v >= lo) & (v <= hi)]
        out[p] = v.mean() if v.size else 0.0
    out -= out.mean()
    return out, int(f.size // period_bins)


def remove_ripple(f_mhz, y_db, start_mhz, ripple_db):
    ripple_db = np.asarray(ripple_db, dtype=np.float64)
    ph = _phase(f_mhz, start_mhz, ripple_db.size)
    return np.asarray(y_db, dtype=np.float64) - ripple_db[ph]


def ignore_leaks(f_mhz, floor_db):
    """V20.9: floor shape without the bumps of outside signals.
    Returns (floor_db, [[lo MHz, hi MHz, dB], ...] of the bumps taken out).
    Between the edges of a bump the floor is a straight line."""
    f = np.asarray(f_mhz, dtype=np.float64)
    floor = np.asarray(floor_db, dtype=np.float64)
    n = f.size
    e = LEAK_EDGE_STEPS
    if n < LEAK_GRID_BINS * (4 * e + 2):
        return floor.copy(), []
    step = float(np.median(np.diff(f)))
    if not (0.6 * BIN_MHZ <= step <= 1.4 * BIN_MHZ):
        return floor.copy(), []
    at = np.arange(0, n, LEAK_GRID_BINS)
    g = floor[at]
    d = g[e:] - g[:-e]                       # d[k]: change from point k to k + e
    max_steps = int(round(LEAK_MAX_MHZ / (step * LEAK_GRID_BINS)))
    margin = int(round(LEAK_MARGIN_MHZ / step))
    mask = np.zeros(n, dtype=bool)
    leaks = []
    k = 0
    while k < d.size:
        if d[k] < LEAK_DB:
            k += 1
            continue
        a = k                                # the floor starts to rise here
        base = float(g[a])
        stop = min(g.size - 1, a + max_steps)
        b = None
        top = base
        for m in range(a + 1, stop + 1):
            top = max(top, float(g[m]))
            if m > a + e and top - base >= LEAK_DB and \
                    g[m] <= base + max(LEAK_RETURN_DB, 0.25 * (top - base)):
                b = m                        # ... and is down again here
                break
        if b is None or d[a:min(b, d.size)].min() > -LEAK_FALL_DB:
            k += 1                           # up and stays up, or comes down slowly: a step
            continue
        height = top - base
        lo = max(int(at[a]) - margin, 0)
        hi = min(int(at[b]) + margin, n - 1)
        mask[lo:hi + 1] = True
        leaks.append([round(float(f[lo]), 1), round(float(f[hi]), 1), round(height, 1)])
        k = b
    if not leaks:
        return floor.copy(), []
    good = ~mask
    if good.sum() < 2:
        return floor.copy(), []
    out = floor.copy()
    out[mask] = np.interp(f[mask], f[good], floor[good])
    merged = []
    for lo, hi, db in leaks:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
            merged[-1][2] = max(merged[-1][2], db)
        else:
            merged.append([lo, hi, db])
    return out, merged


# ---------------------------------------------------------------- profile

def build_profile(f_mhz, y_db, start_mhz, period_bins, meta=None):
    """Dongle profile from a scan WITHOUT antenna (wide range, e.g. 31-1766)."""
    f = np.asarray(f_mhz, dtype=np.float64)
    y = np.asarray(y_db, dtype=np.float64)
    ripple, n = measure_ripple(f, y, start_mhz, period_bins)
    if ripple is None:
        raise ValueError('calibration scan is too short')
    yc = remove_ripple(f, y, start_mhz, ripple)
    floor = running_median(yc, FLOOR_BINS, ends='odd')
    # the floor shape itself must be smooth: median again over 6 MHz of the
    # 2 MHz median would flatten the 300 MHz hump edge too much, so only a
    # light 9-point mean
    k = np.ones(9) / 9.0
    floor = np.convolve(np.pad(floor, 4, mode='edge'), k, mode='valid')
    floor, leaks = ignore_leaks(f, floor)    # V20.9
    excess = yc - floor
    k_near = np.abs(f - np.rint(f / SPUR_STEP_MHZ) * SPUR_STEP_MHZ) <= SPUR_NEAR_MHZ
    for x in EXTRA_SPURS_MHZ:
        k_near |= np.abs(f - x) <= SPUR_NEAR_MHZ
    centre = []                      # one centre point per spur
    spur = np.zeros(f.size, dtype=bool)
    cands = list(np.arange(np.ceil(f[0] / SPUR_STEP_MHZ), np.floor(f[-1] / SPUR_STEP_MHZ) + 1) * SPUR_STEP_MHZ)
    cands += [x for x in EXTRA_SPURS_MHZ if f[0] <= x <= f[-1]]
    for kf in sorted(cands):
        m = np.flatnonzero(np.abs(f - kf) <= SPUR_NEAR_MHZ)
        if not m.size:
            continue
        i = m[np.argmax(excess[m])]
        if excess[i] < SPUR_DB:
            continue
        centre.append(i)
        spur[max(0, i - SPUR_WIDEN):i + SPUR_WIDEN + 1] = True
    spur_pw = np.where(spur, np.maximum(from_db(yc) - from_db(floor), 0.0), 0.0)
    spurs = [[round(float(f[i]), 4), round(float(excess[i]), 1)] for i in centre]
    # everything else that sticks out is not the dongle: a transmitter nearby
    # leaks into the dongle even without antenna. Not removed, only reported.
    external = []
    ext = (excess >= EXTERNAL_DB) & ~spur & ~k_near
    if ext.any():
        idx = np.flatnonzero(ext)
        for grp in np.split(idx, np.flatnonzero(np.diff(idx) > 4) + 1):
            top = grp[excess[grp] >= excess[grp].max() - 0.5]
            i = top[top.size // 2]           # centre of a flat top
            external.append([round(float(f[i]), 3), round(float(excess[i]), 1)])
    prof = dict(
        version=PROFILE_VERSION,
        freq=f, floor_db=floor, spur_pw=spur_pw, ripple_db=ripple,
        start_mhz=float(start_mhz), period_bins=int(period_bins),
        floor_ref_db=float(np.median(floor)),
        spurs=spurs,
        external=external,
        leaks=leaks,
        meta=dict(meta or {}),
    )
    prof['meta'].setdefault('created', time.strftime('%Y-%m-%d %H:%M:%S'))
    return prof


def save_profile(prof, path):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    info = {k: v for k, v in prof.items() if k not in ('freq', 'floor_db', 'spur_pw', 'ripple_db')}
    tmp = path + '.tmp.npz'
    np.savez_compressed(tmp, freq=prof['freq'], floor_db=prof['floor_db'],
                        spur_pw=prof['spur_pw'], ripple_db=prof['ripple_db'],
                        info=np.array(json.dumps(info)))
    os.replace(tmp, path)
    return path


def load_profile(path):
    with np.load(path) as d:
        prof = json.loads(str(d['info']))
        for k in ('freq', 'floor_db', 'spur_pw', 'ripple_db'):
            prof[k] = np.array(d[k])
    return prof


def profile_matches(prof, gain=None, sample_rate=None, period_bins=None):
    """'' when the profile fits these settings, else the reason."""
    m = prof.get('meta', {})
    pg = m.get('gain')
    if gain is not None and pg is not None and abs(float(pg) - float(gain)) > GAIN_TOLERANCE_DB:
        return 'calibration was made at gain %.1f dB, the scan uses %.1f dB' % (pg, gain)
    ps = m.get('sample_rate')
    if sample_rate is not None and ps is not None and abs(float(ps) - float(sample_rate)) > 1:
        return 'calibration was made at %.3f MS/s, the scan uses %.3f MS/s' % (ps / 1e6, sample_rate / 1e6)
    if period_bins is not None and int(prof.get('period_bins', period_bins)) != int(period_bins):
        return 'calibration was made with another tile step'
    return ''


# ---------------------------------------------------------------- cleaning

def spur_freqs(f_lo, f_hi):
    """Frequencies (MHz) where the dongle makes its own carriers."""
    k0 = int(np.ceil(f_lo / SPUR_STEP_MHZ))
    k1 = int(np.floor(f_hi / SPUR_STEP_MHZ))
    out = [round(k * SPUR_STEP_MHZ, 4) for k in range(max(k0, 1), k1 + 1)]
    out += [x for x in EXTRA_SPURS_MHZ if f_lo <= x <= f_hi]
    return sorted(out)


def known_spur_levels(profile):
    """{MHz: dB above the floor without antenna} of the spur grid, from a
    calibration (its 'spurs'; calibrations made before V20.9 list the odd
    multiples of 14.4 MHz under 'external')."""
    out = {}
    if not profile:
        return out
    for key in ('spurs', 'external'):
        for item in profile.get(key) or []:
            try:
                fr, db = float(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            on_grid = abs(fr - round(fr / SPUR_STEP_MHZ) * SPUR_STEP_MHZ) <= SPUR_NEAR_MHZ or \
                any(abs(fr - x) <= SPUR_NEAR_MHZ for x in EXTRA_SPURS_MHZ)
            if on_grid:
                k = round(fr, 1)
                out[k] = max(out.get(k, 0.), db)
    return out


def remove_spurs(f_mhz, y_db):
    """As hide_spurs() with the default limit; returns (y_db, hidden)."""
    y, hidden, _ = hide_spurs(f_mhz, y_db)
    return y, hidden


def hide_spurs(f_mhz, y_db, limit_db=None, known=None):
    """Hide the dongle's own carriers (k x 14.4 MHz, 480 MHz).

    Such a carrier is one pure tone: exactly 3 points wide (centre and one
    point on each side, the window of the FFT), at a known frequency. With
    the antenna on, the dongle also receives its own carrier through the
    antenna, so its level can not be known in advance; the shape and the
    frequency can. Where the points 50-100 kHz away are at the level of
    the surroundings (nothing wide there), the 5 centre points get the
    level of the surroundings. A real transmitter on that frequency is
    wider than 100 kHz and stays untouched.

    V20.9: a narrow carrier there that stands more than ``limit_db``
    (SPUR_HIDE_MAX_DB) above the floor is NOT hidden: no own spur was ever
    that strong, so it may be a transmitter. ``known`` ({MHz: dB without
    antenna}, known_spur_levels()) raises the limit where the dongle's own
    spur is known to be stronger. limit_db=float('inf'): as before V20.9.
    Returns (y_db, hidden frequencies, kept [[MHz, dB above the floor], ...])."""
    f = np.asarray(f_mhz, dtype=np.float64)
    y = np.asarray(y_db, dtype=np.float64).copy()
    removed, kept = [], []
    if limit_db is None:
        limit_db = SPUR_HIDE_MAX_DB
    known = known or {}
    if f.size < 25:
        return y, removed, kept
    step = float(np.median(np.diff(f)))
    if not (0.6 * BIN_MHZ <= step <= 1.4 * BIN_MHZ):
        return y, removed, kept     # other point spacing: shape test not valid
    p = from_db(y)
    for s in spur_freqs(f[0], f[-1]):
        i = int(np.argmin(np.abs(f - s)))
        if abs(f[i] - s) > 0.6 * step or i < 10 or i > f.size - 11:
            continue
        if abs((f[i + 10] - f[i - 10]) - 20 * step) > 0.5 * step:
            continue                # gap in the scan here
        left = float(np.median(p[i - 10:i - 4]))
        right = float(np.median(p[i + 5:i + 11]))
        x = np.arange(-4, 5)
        line = left + (right - left) * (x + 7.5) / 15.0     # straight line under the spur
        seg = p[i - 4:i + 5]
        centre_db = to_db(seg[3:6].max()) - to_db(line[3:6].mean())
        shoulder_db = to_db(np.r_[seg[:2], seg[-2:]] / np.r_[line[:2], line[-2:]]).max()
        mid_db = to_db(np.r_[seg[2], seg[6]] / np.r_[line[2], line[6]]).max()
        if centre_db < SPUR_MIN_DB or shoulder_db > SPUR_SHOULDER_DB:
            continue
        if mid_db > max(SPUR_SHOULDER_DB, centre_db - 3.0):
            continue                # +-50 kHz too high for a tone: something wide
        limit = max(float(limit_db), known.get(round(s, 1), -1e9) + SPUR_CAL_MARGIN_DB)
        if centre_db > limit:
            kept.append([round(float(f[i]), 4), round(float(centre_db), 1)])
            continue                # V20.9: too strong for an own spur
        p[i - 2:i + 3] = line[2:7]
        removed.append(round(float(f[i]), 4))
    return to_db(p), removed, kept


def clean(f_mhz, y_db, start_mhz, period_bins, profile=None, ripple=True,
          spurs=True, flatten=True, default_ripple=None, spur_limit_db=None,
          spur_profile=None):
    """Return (clean_db, info). Ripple and spurs need no calibration; the
    floor shape (flatten) needs the profile of a calibration scan.
    V20.9: ``spur_profile`` (default: ``profile``) only tells how strong the
    dongle's own spurs are without antenna (see hide_spurs)."""
    f = np.asarray(f_mhz, dtype=np.float64)
    y = np.asarray(y_db, dtype=np.float64).copy()
    info = {'ripple': None, 'spurs': False, 'flatten': False}
    if f.size == 0:
        return y, info
    if ripple:
        r, n = measure_ripple(f, y, start_mhz, period_bins)
        src = 'scan'
        if r is None or n < RIPPLE_MIN_PERIODS:
            r, src = None, None
            if profile is not None and int(profile.get('period_bins', 0)) == int(period_bins):
                r, src = np.asarray(profile['ripple_db']), 'calibration'
            elif default_ripple is not None and len(default_ripple) == period_bins:
                r, src = np.asarray(default_ripple, dtype=float), 'default'
        if r is not None:
            y = remove_ripple(f, y, start_mhz, r)
            info['ripple'] = src
            info['ripple_pp_db'] = round(float(np.ptp(r)), 2)
    if spurs:
        if spur_limit_db is None:
            spur_limit_db = SPUR_HIDE_MAX_DB
        y, gone, kept = hide_spurs(f, y, spur_limit_db,
                                   known_spur_levels(spur_profile if spur_profile is not None else profile))
        info['spurs'] = True
        info['spurs_removed'] = gone
        info['spurs_kept'] = kept
        info['spur_limit_db'] = None if np.isinf(spur_limit_db) else float(spur_limit_db)
    if profile is not None and flatten:
        pf = np.asarray(profile['freq'], dtype=np.float64)
        inside = (f >= pf[0] - 0.5 * BIN_MHZ) & (f <= pf[-1] + 0.5 * BIN_MHZ)
        floor = np.interp(f, pf, profile['floor_db'])
        ref = float(profile.get('floor_ref_db', np.median(profile['floor_db'])))
        y = np.where(inside, y - (floor - ref), y)
        info['flatten'] = True
        info['floor_ref_db'] = round(ref, 2)
    return y, info


# ---------------------------------------------------------------- calibration files

CALIBRATION_RANGE_MHZ = (31.0, 1766.0)


def calibration_dir():
    return os.path.join(os.path.expanduser('~'), 'wwb_scanner_data', 'calibration')


def profile_path(gain, sample_rate):
    return os.path.join(calibration_dir(), 'dongle_gain%04.1f_fs%.0fk.npz' % (float(gain), float(sample_rate) / 1e3))


def find_profile(gain, sample_rate, period_bins=None):
    """(profile, '') or (None, reason) for the scan settings."""
    try:
        path = profile_path(gain, sample_rate)
    except (TypeError, ValueError):
        return None, 'gain unknown'
    if not os.path.exists(path):
        return None, 'no calibration for gain %.1f dB (Scan settings > Calibrate dongle)' % float(gain)
    try:
        prof = load_profile(path)
    except Exception as exc:
        logger.warning('calibration file %s can not be read: %s', path, exc)
        return None, 'calibration file damaged, calibrate again'
    why = profile_matches(prof, gain, sample_rate, period_bins)
    if not why and int(prof.get('version', 1)) < 2:
        # V20.9: made before the leak check - the same check now, in memory
        # (the file stays as it is; floor_ref_db too, levels do not move)
        try:
            prof['floor_db'], prof['leaks'] = ignore_leaks(prof['freq'], prof['floor_db'])
        except Exception:
            logger.exception('leak check of %s', path)
    if not why and prof.get('floor_ref_db', -200.) > CAL_FLOOR_MAX_DB:
        # V20.7.3: saved before the check existed (10.11 20:44: floor -65 dB)
        why = ('the calibration of %s is broken (floor %.1f dB, the dongle alone gives about -96 dB): '
               'calibrate again without antenna' % (prof.get('meta', {}).get('created', '?'), prof['floor_ref_db']))
    return (None, why) if why else (prof, '')


def status_text(gain, sample_rate):
    if gain is None:
        return ''
    prof, why = find_profile(gain, sample_rate)
    if prof is None:
        return 'Not calibrated for gain %.1f dB: the floor is not levelled. Spurs and ripple ' \
               'are removed without calibration.' % float(gain)
    m = prof.get('meta', {})
    text = 'Calibrated %s at gain %.1f dB: floor %.1f dB, %d own spurs.' % (
        m.get('created', '?'), float(m.get('gain', gain)), prof.get('floor_ref_db', 0.),
        len(prof.get('spurs', [])))
    ext = prof.get('external') or []
    if ext:
        text += ' %d outside signal(s) were received during the calibration.' % len(ext)
    leaks = prof.get('leaks') or []
    if leaks:
        text += ' Left out of the floor shape: %s MHz.' % ', '.join('%.0f-%.0f' % (lo, hi) for lo, hi, _ in leaks[:6])
    return text


CAL_FLOOR_MAX_DB = -80.     # V20.7.3: the dongle's own floor is about -96 dB at 49.6 dB gain
CAL_CLIP_MAX_SHARE = 0.1    # V20.7.3: share of tiles clipping in a calibration scan


def check_calibration_scan(f, y, prof, clipped_share=None):
    """'' if the scan looks like a scan without antenna, else the reason."""
    if clipped_share is not None and clipped_share > CAL_CLIP_MAX_SHARE:
        return ('%.0f %% of the tiles clipped - the dongle input is overloaded (antenna connected, or a '
                'dongle fault: unplug it and plug it in again)' % (100. * clipped_share))
    if prof.get('floor_ref_db', -200.) > CAL_FLOOR_MAX_DB:
        return ('the floor is %.1f dB, the dongle alone gives about -96 dB - something strong reaches '
                'the input (antenna connected, or a dongle fault: unplug it and plug it in again)'
                % prof['floor_ref_db'])
    f = np.asarray(f)
    fm = (f >= 88.0) & (f <= 108.0)
    if fm.sum() > 100:
        floor = np.interp(f[fm], prof['freq'], prof['floor_db'])
        if np.percentile(np.asarray(y)[fm] - floor, 95) > 6.0:
            return 'FM radio stations are received: the antenna seems to be connected'
    strong = [e for e in prof.get('external') or [] if e[1] >= 6.0]
    if len(strong) > 15:
        return '%d strong signals are received: the antenna seems to be connected' % len(strong)
    if f.size < 1000 or f[-1] - f[0] < 300:
        return 'the calibration scan is too short'
    return ''


def calibrate(f_mhz, y_db, start_mhz, step_mhz, meta, clipped_share=None):
    """Build and save the profile from a scan without antenna.
    Returns (saved, text for the status line). The previous profile of that
    gain is kept as <name>.prev.npz (V20.7.3)."""
    f = np.asarray(f_mhz, dtype=np.float64)
    y = np.asarray(y_db, dtype=np.float64)
    bin_mhz = float(np.median(np.diff(f)))
    period = int(round(step_mhz / bin_mhz))
    prof = build_profile(f, y, start_mhz, period, meta=meta)
    why = check_calibration_scan(f, y, prof, clipped_share)
    if why:
        return False, 'Calibration NOT saved (the previous one is kept): %s. Unscrew the antenna and calibrate again.' % why
    path = profile_path(meta['gain'], meta['sample_rate'])
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path[:-4] + '.prev.npz')
        except (IOError, OSError):
            logger.exception('backup of %s', path)
    path = save_profile(prof, path)
    text = 'Calibration saved (gain %.1f dB): floor %.1f dB, %d own spurs' % (
        meta['gain'], prof['floor_ref_db'], len(prof['spurs']))
    ext = sorted(prof.get('external') or [], key=lambda e: -e[1])
    if ext:
        text += '; received without antenna: %s MHz (own transmitters on? they are NOT removed from scans)' % (
            ', '.join('%.3f' % e[0] for e in ext[:5]))
    leaks = prof.get('leaks') or []
    if leaks:
        # V20.9
        text += '; outside signals left out of the floor shape: %s MHz' % ', '.join(
            '%.0f-%.0f' % (lo, hi) for lo, hi, _ in leaks[:6]) + (
            ' and %d more' % (len(leaks) - 6) if len(leaks) > 6 else '')
    logger.info('calibration profile written to %s', path)
    return True, text
