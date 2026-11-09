"""V20.5: calibration of the real gain steps of the dongle, by frequency.

Auto gain (V17-V20) reads a clipped tile again with less gain and scales it
back by the NOMINAL gain difference. On the user's RTL-SDR Blog V4 the real
step 49.6 -> 33.8 dB was about 8 dB at 150-300 MHz, 13.6 at 300-470, 15.4 at
470-700 and 18.2 at 700-1000 MHz (scans of 09.11.2026, several minutes
apart - only a hint), against 15.8 nominal. A tile re-measured with less
gain is then off against its neighbours by the difference (DAB 211 MHz was
11 dB off at 49.6 dB).

The calibration measures the steps with the antenna ON, on signals of the
air (FM, DAB, TV, mobile):

1. survey: one tile every SURVEY_STEP_MHZ over 30-1766 MHz at the current
   gain (a normal scan of many narrow ranges, about 15 s);
2. in each frequency group the ANCHORS_PER_GROUP tiles with the most strong
   bins are read at every gain of the tuner, one right after the other
   (about 2 s per tile);
3. for every pair of neighbouring gains the step is the median level change
   of the bins that are well above the noise and not clipped at both gains.
   A step no bin could measure stays nominal.

The table is kept per sample rate in the calibration folder. ``real_drop``
gives the real gain difference between two gains at a frequency (linear
between the groups, the nearest group outside them); without a table it is
the nominal difference, as before.
"""
import json
import logging
import os
import time

import numpy as np
from scipy.signal import welch

logger = logging.getLogger(__name__)

GAIN_CAL_RANGE_MHZ = (30., 1766.)
SURVEY_STEP_MHZ = 4.
SURVEY_WIDTH_MHZ = 0.3
SURVEY_GAIN_DB = 33.8    # the survey finds the signals; the window's gain may be 0 dB
GROUPS_MHZ = ((30., 100.), (100., 170.), (170., 250.), (250., 400.), (400., 550.),
              (550., 700.), (700., 900.), (900., 1200.), (1200., 1766.))
ANCHORS_PER_GROUP = 2
STRONG_DB = 20.          # survey: a bin this much over the floor counts for the choice
MIN_SNR_DB = 15.         # a bin must stand this much over the noise at both gains
MIN_BINS = 5             # bins needed for one step
CLIP_LIMIT = 1e-2        # a reading with more clipped samples is not used (1 %: level off < 0.2 dB)
STEP_TOLERANCE_DB = 6.   # a step further than this above nominal is ignored
STEP_MIN_SHARE = 0.2     # ... and one under 20 % of nominal (minus 0.5 dB)
GROUP_MIN_SHARE = 0.3    # a group whose measured steps add up to under 30 % of nominal is dropped
READ_SAMPLES = 65536
DISCARD_SAMPLES = 16384
NPERSEG = 256
KEEP_HZ = (50e3, 0.9e6)  # bins used: away from DC, inside the flat part of the tile
VERSION = 1

_cache = {}


# ------------------------------------------------------------------ files

def table_path(sample_rate):
    from wwb_scanner.scanner import cleanup
    return os.path.join(cleanup.calibration_dir(), 'gain_steps_fs%.0fk.json' % (float(sample_rate) / 1e3))


def save_table(table, sample_rate):
    path = table_path(sample_rate)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(table, fh, indent=1)
    os.replace(tmp, path)
    _cache.pop(path, None)
    return path


def load_table(sample_rate):
    """The table for this sample rate, or None. Cached by file time."""
    if not sample_rate:
        return None
    path = table_path(sample_rate)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        with open(path) as fh:
            table = json.load(fh)
        if table.get('version') != VERSION or not table.get('groups'):
            raise ValueError('unknown format')
        _prepare(table)
    except Exception as exc:
        logger.warning('gain step table %s can not be used: %s', path, exc)
        table = None
    _cache[path] = (mtime, table)
    return table


def _prepare(table):
    """Cumulative real gain per group (nominal where a step is unknown)."""
    gains = np.asarray(table['gains'], dtype=np.float64)
    nominal = np.diff(gains)
    for grp in table['groups']:
        steps = np.array([np.nan if s is None else float(s) for s in grp['steps']], dtype=np.float64)
        steps = np.where(np.isfinite(steps), steps, nominal)
        grp['_real'] = np.concatenate([[gains[0]], gains[0] + np.cumsum(steps)])
    table['_gains'] = gains
    table['_f'] = np.array([g['f_mhz'] for g in table['groups']], dtype=np.float64)
    order = np.argsort(table['_f'])
    table['groups'] = [table['groups'][i] for i in order]
    table['_f'] = table['_f'][order]


def _real_gain(table, g, f_mhz):
    gains = table['_gains']
    i = int(np.argmin(np.abs(gains - float(g))))
    if abs(gains[i] - float(g)) > 0.6:
        return None                     # a gain the table does not know
    fs = table['_f']
    vals = np.array([grp['_real'][i] for grp in table['groups']])
    return float(np.interp(f_mhz, fs, vals))


def real_drop(g_hi, g_lo, f_hz, sample_rate=None, table=None):
    """Real gain difference g_hi - g_lo (dB) at ``f_hz``. Nominal without
    a table, or for gains the table does not know."""
    nominal = float(g_hi) - float(g_lo)
    if table is None:
        table = load_table(sample_rate)
    if table is None:
        return nominal
    a = _real_gain(table, g_hi, f_hz / 1e6)
    b = _real_gain(table, g_lo, f_hz / 1e6)
    if a is None or b is None:
        return nominal
    return a - b


def status_text(sample_rate):
    table = load_table(sample_rate)
    if table is None:
        return 'Gain steps not calibrated (auto gain uses the nominal steps).'
    worst = table.get('worst') or {}
    text = 'Gain steps calibrated %s: %d frequency group(s), %d of %d steps measured' % (
        table.get('date', '?'), len(table['groups']), table.get('measured', 0), table.get('total', 0))
    if worst:
        text += '; largest difference from nominal %+.1f dB (%.1f -> %.1f dB at %.0f MHz)' % (
            worst['err'], worst['g_hi'], worst['g_lo'], worst['f_mhz'])
    return text + '.'


# ------------------------------------------------------------------ measuring

def survey_ranges(lo=None, hi=None):
    """Narrow ranges, one tile each, every SURVEY_STEP_MHZ."""
    lo = GAIN_CAL_RANGE_MHZ[0] if lo is None else lo
    hi = GAIN_CAL_RANGE_MHZ[1] if hi is None else hi
    out = []
    f = lo
    while f + SURVEY_WIDTH_MHZ <= hi:
        out.append([round(f, 3), round(f + SURVEY_WIDTH_MHZ, 3)])
        f += SURVEY_STEP_MHZ
    return out


def to_db(p):
    return 10. * np.log10(np.maximum(np.asarray(p, dtype=np.float64), 1e-30))


def pick_anchors(collection):
    """Tile centres (Hz) per frequency group: the strongest tile (its signals
    stay above the noise down to the low gains) and the strongest one that
    did not clip in the survey (it can be read at the top gains too)."""
    tiles = [(k, s.powers, float(getattr(s, 'clip_ratio', 0.) or 0.))
             for k, s in collection.sample_sets.items()
             if getattr(s, 'powers', None) is not None and np.size(s.powers)]
    if not tiles:
        return []
    out = []
    for lo, hi in GROUPS_MHZ:
        cand = []
        mine = [p for k, p, _ in tiles if lo <= k / 1e6 < hi]
        if not mine:
            continue
        # the floor of this group: the real gain (and so the floor) differs
        # by frequency
        floor = float(np.percentile(np.concatenate([to_db(p) for p in mine]), 20))
        for k, p, cr in tiles:
            if lo <= k / 1e6 < hi:
                d = to_db(p)
                n = int(np.count_nonzero(d > floor + STRONG_DB))
                if n >= MIN_BINS:
                    cand.append((float(np.percentile(d, 90)), n, cr, k))
        if not cand:
            continue
        cand.sort(reverse=True)
        pick = [cand[0][3]]
        calm = [c for c in cand if c[2] < CLIP_LIMIT and c[3] not in pick]
        if calm:
            pick.append(calm[0][3])
        for c in cand:
            if len(pick) >= ANCHORS_PER_GROUP:
                break
            if c[3] not in pick:
                pick.append(c[3])
        out.extend(pick[:ANCHORS_PER_GROUP])
    return out


def tile_psd(x, fs):
    f, p = welch(np.asarray(x), fs=fs, window='hann', nperseg=NPERSEG,
                 return_onesided=False, detrend=False)
    keep = (np.abs(f) >= KEEP_HZ[0]) & (np.abs(f) <= KEEP_HZ[1])
    o = np.argsort(f[keep])
    return p[keep][o]


def sweep(sdr, fc, gains, fs, run=None, budget_s=None):
    """Read one tile at every gain. Returns [(gain_set, psd, clip_ratio)]."""
    from wwb_scanner.scanner.sample_processing import clip_ratio, run_with_timeout, \
        TUNE_TIMEOUT, READ_TIMEOUT
    call = run or (lambda f, *a: run_with_timeout(f, READ_TIMEOUT, *a))
    run_with_timeout(sdr.set_center_freq, TUNE_TIMEOUT, fc)
    rows = []
    t0 = time.monotonic()
    for g in gains:
        run_with_timeout(setattr, TUNE_TIMEOUT, sdr, 'gain', g)
        try:
            gs = float(sdr.gain)
        except Exception:
            gs = float(g)
        call(sdr.read_samples, DISCARD_SAMPLES)
        x = call(sdr.read_samples, READ_SAMPLES)
        rows.append((gs, tile_psd(x, fs), float(clip_ratio(x))))
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            raise RuntimeError('the dongle answers too slowly (%.0f s for one tile)' % (time.monotonic() - t0))
    return rows


def noise_floors(sweeps, gains):
    """Per gain: the 10th percentile of all bins of all anchors (dB)."""
    out = []
    for i in range(len(gains)):
        vals = [to_db(rows[i][1]) for rows in sweeps if len(rows) > i]
        out.append(float(np.percentile(np.concatenate(vals), 10)) if vals else np.nan)
    return np.array(out)


def anchor_steps(rows, floors):
    """Steps (dB) between neighbouring gains measured on one tile; NaN where
    too few bins were usable at both gains."""
    n = len(rows)
    steps = np.full(n - 1, np.nan)
    for i in range(n - 1):
        (_, pa, ca), (_, pb, cb) = rows[i], rows[i + 1]
        if ca >= CLIP_LIMIT or cb >= CLIP_LIMIT:
            continue
        da, db_ = to_db(pa), to_db(pb)
        v = (da >= floors[i] + MIN_SNR_DB) & (db_ >= floors[i + 1] + MIN_SNR_DB)
        if np.count_nonzero(v) < MIN_BINS:
            continue
        steps[i] = float(np.median(db_[v] - da[v]))
    return steps


def build_table(anchors, sweeps, gains, meta=None):
    """Table from the sweeps of the anchors (Hz -> rows)."""
    gains = [float(g) for g in gains]
    nominal = np.diff(gains)
    floors = noise_floors(sweeps, gains)
    per_anchor = [anchor_steps(rows, floors) for rows in sweeps]
    groups = []
    measured = 0
    worst = None
    for lo, hi in GROUPS_MHZ:
        idx = [i for i, k in enumerate(anchors) if lo <= k / 1e6 < hi]
        if not idx:
            continue
        st = np.array([per_anchor[i] for i in idx])
        with np.errstate(all='ignore'):
            med = np.nanmedian(st, axis=0) if st.size else np.full(len(nominal), np.nan)
        ok = np.isfinite(med) & (med >= STEP_MIN_SHARE * nominal - 0.5) & \
            (med <= nominal + STEP_TOLERANCE_DB)
        steps = [float(round(m, 2)) if o else None for m, o in zip(med, ok)]
        if not any(s is not None for s in steps):
            continue
        got = float(np.sum(med[ok]))
        if got < GROUP_MIN_SHARE * float(np.sum(nominal[ok])):
            # the level hardly follows the gain: gain setting without effect
            # (AGC?) - such a table would spoil every re-measured tile
            logger.warning('gain steps near %.0f MHz: %.1f dB for %.1f dB nominal - group not used',
                           np.mean([anchors[i] for i in idx]) / 1e6, got, float(np.sum(nominal[ok])))
            continue
        measured += sum(s is not None for s in steps)
        f_mhz = float(np.mean([anchors[i] for i in idx]) / 1e6)
        groups.append({'f_mhz': round(f_mhz, 3), 'anchors_mhz': [round(anchors[i] / 1e6, 3) for i in idx],
                       'steps': steps})
    table = {'version': VERSION, 'date': time.strftime('%Y-%m-%d %H:%M:%S'), 'gains': gains,
             'groups': groups, 'measured': measured, 'total': len(nominal) * len(groups)}
    if meta:
        table.update(meta)
    if groups:
        _prepare(table)
        # the largest error a re-measure from the top gain would have
        top = gains[-1]
        for grp in table['groups']:
            for lo_g in gains[:-1]:
                err = (_real_gain(table, top, grp['f_mhz']) - _real_gain(table, lo_g, grp['f_mhz'])) - (top - lo_g)
                if worst is None or abs(err) > abs(worst['err']):
                    worst = {'err': round(err, 1), 'g_hi': top, 'g_lo': lo_g, 'f_mhz': grp['f_mhz']}
        table['worst'] = worst
        for grp in table['groups']:
            grp.pop('_real', None)
        for k in ('_gains', '_f'):
            table.pop(k, None)
    return table


def calibrate(scanner, running=None):
    """Run after the survey scan, with the dongle still open. Returns
    (ok, text). Saves the table on success."""
    coll = scanner.sample_collection
    sdr = scanner.sdr
    if sdr is None:
        return False, 'Gain step calibration: no dongle'
    if getattr(coll, 'aborted', False):
        return False, 'Gain step calibration not saved: the survey did not finish'
    anchors = pick_anchors(coll)
    if not anchors:
        return False, ('Gain step calibration not saved: no strong signals found. Connect the antenna '
                       '(it needs FM, DAB, TV or mobile signals on the air)')
    try:
        gains = sorted(float(g) for g in (scanner.gains or []))
    except Exception:
        gains = []
    if len(gains) < 2:
        gains = sorted(g / 10. for g in sdr.get_gains())
    fs = float(scanner.sample_rate)
    try:
        g0 = float(sdr.gain)
    except Exception:
        g0 = float(scanner.gain or gains[-1])
    print('measuring the gain steps on %d tile(s): %s MHz' % (
        len(anchors), ', '.join('%.1f' % (k / 1e6) for k in anchors)), flush=True)
    sweeps, used = [], []
    from wwb_scanner.scanner.sample_processing import run_with_timeout, TUNE_TIMEOUT
    try:
        for k in anchors:
            if running is not None and not running.is_set():
                return False, 'Gain step calibration stopped'
            sweeps.append(sweep(sdr, k, gains, fs, budget_s=60.))
            used.append(k)
    finally:
        try:
            if getattr(sdr, 'device_opened', True):
                run_with_timeout(setattr, TUNE_TIMEOUT, sdr, 'gain', g0)
        except Exception:
            logger.exception('could not set the gain back to %s dB', g0)
    meta = {'sample_rate': fs}
    try:
        meta['serial'] = scanner.device_config.serial_number
    except Exception:
        pass
    table = build_table(used, sweeps, gains, meta)
    if not table['groups']:
        return False, ('Gain step calibration not saved: the signals were too weak to measure '
                       'the steps (move the antenna to a window / outside)')
    path = save_table(table, fs)
    logger.info('gain step table written to %s', path)
    return True, status_text(fs)
