# -*- coding: utf-8 -*-
"""Clean scan (V17): remove what the dongle adds by itself.

Three corrections, all done after the scan in a fraction of a second
(no extra measuring time):

1. Tile ripple. Every tile is 2.4 MHz wide and the dongle's filter is not
   flat inside it, so the floor has a small wave (about +-0.2 dB) that
   repeats with the tile step (0.775 MHz = 31 points). It is measured from
   the scan itself (median over all tiles) and removed.

2. Own spurs. The dongle makes its own carriers at multiples of 28.8 MHz
   (its crystal). A calibration scan without antenna records them; their
   power is subtracted (as power, not dB), so a real signal on the same
   frequency stays.

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
import time

import numpy as np

logger = logging.getLogger('wwb_scanner.cleanup')

PROFILE_VERSION = 1
BIN_MHZ = 0.025
FLOOR_BINS = 81            # running median for the floor shape: 2 MHz
SPUR_DB = 0.5              # a calibration point this much over the floor is a spur
SPUR_WIDEN = 2             # spur area +-2 points (window leakage)
SPUR_STEP_MHZ = 28.8       # the dongle's crystal: spurs only at its multiples
SPUR_NEAR_MHZ = 0.05
EXTRA_SPURS_MHZ = [480.0]  # USB 2.0 clock of the dongle
EXTERNAL_DB = 3.0          # other peaks in the calibration = transmitters nearby
SPUR_MIN_DB = 0.5          # spur centre at least this much over its surroundings
SPUR_SHOULDER_DB = 1.0     # points 75-100 kHz away at most this much over them
RIPPLE_MIN_PERIODS = 40    # shorter scans use the ripple of the calibration
RIPPLE_CLIP_DB = 3.0       # points further than this from the floor are signals
GAIN_TOLERANCE_DB = 0.6

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


def remove_spurs(f_mhz, y_db):
    """Remove the dongle's own carriers (k x 28.8 MHz, 480 MHz).

    Such a carrier is one pure tone: exactly 3 points wide (centre and one
    point on each side, the window of the FFT), at a known frequency. With
    the antenna on, the dongle also receives its own carrier through the
    antenna, so its level can not be known in advance; the shape and the
    frequency can. Where the points 50-100 kHz away are at the level of
    the surroundings (nothing wide there), the 5 centre points get the
    level of the surroundings. A real transmitter on that frequency is
    wider than 100 kHz and stays untouched.
    Returns (y_db, list of removed frequencies)."""
    f = np.asarray(f_mhz, dtype=np.float64)
    y = np.asarray(y_db, dtype=np.float64).copy()
    removed = []
    if f.size < 25:
        return y, removed
    step = float(np.median(np.diff(f)))
    if not (0.6 * BIN_MHZ <= step <= 1.4 * BIN_MHZ):
        return y, removed           # other point spacing: shape test not valid
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
        p[i - 2:i + 3] = line[2:7]
        removed.append(round(float(f[i]), 4))
    return to_db(p), removed


def clean(f_mhz, y_db, start_mhz, period_bins, profile=None, ripple=True,
          spurs=True, flatten=True, default_ripple=None):
    """Return (clean_db, info). Ripple and spurs need no calibration; the
    floor shape (flatten) needs the profile of a calibration scan."""
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
        y, gone = remove_spurs(f, y)
        info['spurs'] = True
        info['spurs_removed'] = gone
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
    return text


def check_calibration_scan(f, y, prof):
    """'' if the scan looks like a scan without antenna, else the reason."""
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


def calibrate(f_mhz, y_db, start_mhz, step_mhz, meta):
    """Build and save the profile from a scan without antenna.
    Returns (saved, text for the status line)."""
    f = np.asarray(f_mhz, dtype=np.float64)
    y = np.asarray(y_db, dtype=np.float64)
    bin_mhz = float(np.median(np.diff(f)))
    period = int(round(step_mhz / bin_mhz))
    prof = build_profile(f, y, start_mhz, period, meta=meta)
    why = check_calibration_scan(f, y, prof)
    if why:
        return False, 'Calibration NOT saved: %s. Unscrew the antenna and calibrate again.' % why
    path = save_profile(prof, profile_path(meta['gain'], meta['sample_rate']))
    text = 'Calibration saved (gain %.1f dB): floor %.1f dB, %d own spurs' % (
        meta['gain'], prof['floor_ref_db'], len(prof['spurs']))
    ext = sorted(prof.get('external') or [], key=lambda e: -e[1])
    if ext:
        text += '; received without antenna: %s MHz (own transmitters on? they are NOT removed from scans)' % (
            ', '.join('%.3f' % e[0] for e in ext[:5]))
    logger.info('calibration profile written to %s', path)
    return True, text
