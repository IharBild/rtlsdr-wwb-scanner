"""V20.6: "Suggest gain" from a gain survey (tools/gain_survey.py).

A gain profile holds, per frequency band and per gain step of the tuner:

* ``loss``  - sensitivity lost against the best step of that band (dB): the
  floor of quiet tiles with the antenna, referred to the input with the
  real gain measured on strong signals;
* ``clip``  - share of the band's tiles whose samples clipped (>= 0.1 %).

``build_profile(ant, noant)`` makes it from the two survey archives (antenna
on, own transmitters off / antenna off); ``suggest(ranges, auto_gain)``
picks a step for the ranges of the next scan:

* auto gain on  -> the step with the least sensitivity loss (auto gain
  re-measures the clipped tiles); within 0.3 dB the lower step (more
  headroom);
* auto gain off -> the least loss among the steps where at most
  MAX_CLIP_NO_AUTO of the tiles clip; if none qualifies, the step with the
  least clipping.

Bands are weighted by how much of them the scan covers. Without a profile
of the user's own the built-in one (the survey of 10.11.2026 in Warsaw,
default_gain_profile.json) is used and the text says so.
"""
import io
import json
import logging
import os
import time
import zipfile

import numpy as np

logger = logging.getLogger(__name__)

VERSION = 1
BANDS_MHZ = ((30, 88), (88, 108), (108, 174), (174, 240), (240, 400), (400, 470), (470, 560),
             (560, 700), (700, 790), (790, 960), (960, 1190), (1190, 1766))
MIN_SNR_DB = 15.        # strong bins for the real gain: over the dongle's own noise at both steps
PASS_AGREE_DB = 1.
MAX_CLIP_NO_AUTO = 0.05
TIE_DB = 0.3
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'default_gain_profile.json')


# ------------------------------------------------------------------ files

def profile_path():
    from wwb_scanner.scanner import cleanup
    return os.path.join(cleanup.calibration_dir(), 'gain_profile.json')


def save_profile(profile, path=None):
    path = path or profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(profile, fh, indent=1)
    os.replace(tmp, path)
    return path


def load_profile():
    """The user's profile, else the built-in one. None if neither loads."""
    for path, own in ((profile_path(), True), (DEFAULT_PATH, False)):
        try:
            with io.open(path, encoding='utf-8') as fh:
                p = json.load(fh)
            if p.get('version') == VERSION and p.get('bands'):
                p['_own'] = own
                return p
        except (IOError, OSError, ValueError):
            continue
        except Exception:
            logger.exception('gain profile %s', path)
    return None


# ------------------------------------------------------------------ building

def _open_survey(path):
    """(meta, psd npz, levels rows) of a survey folder or its zip."""
    import csv
    if os.path.isdir(path):
        def rd(name, mode='r'):
            return open(os.path.join(path, name), mode)
        names = os.listdir(path)
    else:
        z = zipfile.ZipFile(path)
        names = z.namelist()

        def rd(name, mode='r'):
            full = [n for n in names if n.endswith('/' + name) or n == name][0]
            data = z.read(full)
            return io.BytesIO(data) if 'b' in mode else io.StringIO(data.decode('utf-8'))
    meta = json.load(rd('meta.json'))
    npz = np.load(rd('psd.npz', 'rb'))
    psd = {k: npz[k] for k in npz.files}
    rows = list(csv.DictReader(rd('levels.csv')))
    return meta, psd, rows


def build_profile(ant_path, noant_path, bands=BANDS_MHZ):
    """Profile from two gain surveys (antenna on / antenna off)."""
    ma, A, ra = _open_survey(ant_path)
    mn, N, _ = _open_survey(noant_path)
    gains = [float(g) for g in A['gains']]
    nom = np.diff(gains)
    fa = [float(f) for f in A['fc_mhz']]
    fn = {float(f): i for i, f in enumerate(N['fc_mhz'])}
    da = A['db'].astype(np.float64)
    dn = N['db'].astype(np.float64)
    clip = {}
    for r in ra:
        clip[(float(r['fc_mhz']), int(r['pass']), float(r['gain']))] = float(r['clip_ratio'])
    out = []
    for lo, hi in bands:
        tl = [i for i, f in enumerate(fa) if lo <= f < hi and f in fn]
        if len(tl) < 2:
            continue
        dongle = np.median([np.nanmedian(dn[fn[fa[i]], 0], axis=1) for i in tl], axis=0)
        steps = [[] for _ in nom]
        for i in tl:
            f = fa[i]
            per_pass = []
            for p in range(da.shape[1]):
                s = np.full(len(nom), np.nan)
                for k in range(len(nom)):
                    if clip.get((f, p, gains[k]), 1.) >= 1e-3 or clip.get((f, p, gains[k + 1]), 1.) >= 1e-3:
                        continue
                    a, b = da[i, p, k], da[i, p, k + 1]
                    v = (a >= dongle[k] + MIN_SNR_DB) & (b >= dongle[k + 1] + MIN_SNR_DB)
                    if np.count_nonzero(v) >= 5:
                        s[k] = float(np.median(b[v] - a[v]))
                per_pass.append(s)
            if len(per_pass) > 1:
                ok = np.isfinite(per_pass[0]) & np.isfinite(per_pass[1]) & \
                    (np.abs(per_pass[0] - per_pass[1]) <= PASS_AGREE_DB)
                st = np.where(ok, (per_pass[0] + per_pass[1]) / 2., np.nan)
            else:
                st = per_pass[0]
            for k in np.flatnonzero(np.isfinite(st)):
                steps[k].append(st[k])
        med = np.array([np.median(s) if len(s) >= 2 else np.nan for s in steps])
        real = np.concatenate([[0.], np.cumsum(np.where(np.isfinite(med), med, nom))])
        floor = np.percentile([np.nanpercentile(da[i, 0], 10, axis=1) for i in tl], 25, axis=0)
        sens = floor - real
        loss = sens - np.nanmin(sens)
        cl = np.mean([[clip.get((fa[i], 0, g), 0.) >= 1e-3 for g in gains] for i in tl], axis=0)
        out.append({'lo': lo, 'hi': hi, 'tiles': len(tl), 'loss': [round(float(v), 2) for v in loss],
                    'clip': [round(float(v), 3) for v in cl],
                    'steps_measured': int(np.count_nonzero(np.isfinite(med)))})
    return {'version': VERSION, 'date': time.strftime('%Y-%m-%d %H:%M:%S'), 'gains': gains, 'bands': out,
            'source': 'gain survey %s / %s' % (ma.get('started', '?')[:16], mn.get('started', '?')[:16]),
            'tiles': {'antenna': ma.get('tiles_done'), 'no_antenna': mn.get('tiles_done')}}


# ------------------------------------------------------------------ suggestion

def _weights(profile, ranges):
    bands = profile['bands']
    w = np.zeros(len(bands))
    for lo, hi in ranges:
        for j, b in enumerate(bands):
            w[j] += max(0., min(hi, b['hi']) - max(lo, b['lo']))
    if w.sum() <= 0:
        # outside every band: the nearest one
        mid = np.mean([np.mean(r) for r in ranges])
        j = int(np.argmin([min(abs(mid - b['lo']), abs(mid - b['hi'])) for b in bands]))
        w[j] = 1.
    return w / w.sum()


def suggest(ranges, auto_gain, gains=None, profile=None):
    """(gain, text) for scanning ``ranges`` [(lo_mhz, hi_mhz), ...], or
    (None, reason)."""
    profile = profile if profile is not None else load_profile()
    if profile is None:
        return None, 'no gain profile (run gain_survey.py --analyze)'
    pg = [float(g) for g in profile['gains']]
    w = _weights(profile, ranges)
    loss = np.array([b['loss'] for b in profile['bands']], dtype=np.float64)
    clip = np.array([b['clip'] for b in profile['bands']], dtype=np.float64)
    L = (w[:, None] * loss).sum(axis=0)
    C = (w[:, None] * clip).sum(axis=0)
    ok = np.ones(len(pg), bool)
    if gains:
        ok &= np.array([any(abs(g - x) < 0.05 for x in gains) for g in pg])
    if auto_gain:
        cand = np.flatnonzero(ok)
    else:
        cand = np.flatnonzero(ok & (C <= MAX_CLIP_NO_AUTO))
        if not cand.size:
            cand = np.flatnonzero(ok)
            cand = cand[C[cand] <= C[cand].min() + 1e-9]
    if not cand.size:
        return None, 'no gain of this dongle is in the profile'
    best = L[cand].min()
    near = [i for i in cand if L[i] <= best + TIE_DB]
    i = min(near, key=lambda k: pg[k])            # within TIE_DB: more headroom
    g = pg[i]
    text = '%.1f dB: %.1f dB under the best sensitivity, %d %% of tiles clip%s' % (
        g, L[i], round(100. * C[i]), ' (Overload re-measures them)' if auto_gain and C[i] > 0 else '')
    if not auto_gain and C[i] > MAX_CLIP_NO_AUTO:
        text += ' - even the lowest step clips: turn Overload on or move the antenna'
    if not auto_gain and L[i] > 3.:
        ga, _ = suggest(ranges, True, gains, profile)
        if ga is not None and ga != g:
            ia = pg.index(ga)
            text += '; with Overload on: %.1f dB, %.1f dB more sensitive' % (ga, L[i] - L[ia])
    lo_all, hi_all = min(r[0] for r in ranges), max(r[1] for r in ranges)
    if lo_all < profile['bands'][0]['lo'] or hi_all > profile['bands'][-1]['hi']:
        text += '; the profile covers %d-%d MHz, beyond it the nearest band is used' % (
            profile['bands'][0]['lo'], profile['bands'][-1]['hi'])
    text += ' \u00b7 %s' % ('your gain survey ' + profile.get('date', '?')[:10] if profile.get('_own')
                       else 'built-in profile, Warsaw')
    return g, text
