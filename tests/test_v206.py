"""V20.6: DLL scanner-v5 (tuner I2C retries) - the scanner side; calibration
tolerance 4 dB (gain survey 10.11.2026: real steps 3.1-3.9 dB off nominal)."""
import ctypes
import io
import os
import subprocess
import sys
import types

import numpy as np
import pytest

from wwb_scanner import scan_dll
from wwb_scanner.scanner import gain_cal

HERE = os.path.dirname(os.path.abspath(__file__))
V5 = os.path.join(os.path.dirname(HERE), 'drivers', 'rtlsdr-scanner-v5')
GAINS = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0,
         29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]


class _FakeLib(object):
    def __init__(self, with_stats=True):
        self.calls = 0
        if with_stats:
            self.rtlsdr_get_i2c_stats = self._stats

    def rtlsdr_get_tuner_pll_lock(self, dev, late, unl):
        return 1

    def _stats(self, dev, retries, failures):
        self.calls += 1
        retries._obj.value = 7
        failures._obj.value = 1
        return 0


def _sdr():
    return types.SimpleNamespace(dev_p=ctypes.c_void_p(1234), device_opened=True)


def test_i2c_stats_from_the_v5_library(monkeypatch):
    lib = _FakeLib()
    monkeypatch.setitem(scan_dll._cache, 'lib', lib)
    assert scan_dll.i2c_stats(_sdr()) == (7, 1)


def test_older_library_has_no_i2c_stats(monkeypatch):
    # safety: scanner-v4 (and the stock DLL) keep working
    monkeypatch.setitem(scan_dll._cache, 'lib', _FakeLib(with_stats=False))
    assert scan_dll.i2c_stats(_sdr()) is None
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    assert scan_dll.i2c_stats(_sdr()) is None


def test_closed_device_is_not_asked(monkeypatch):
    lib = _FakeLib()
    monkeypatch.setitem(scan_dll._cache, 'lib', lib)
    s = _sdr()
    s.device_opened = False
    assert scan_dll.i2c_stats(s) is None and lib.calls == 0


def test_scan_end_prints_the_i2c_line(monkeypatch, capsys):
    from wwb_scanner.scanner import sample_processing as sp
    monkeypatch.setitem(scan_dll._cache, 'lib', _FakeLib())
    c = object.__new__(sp.SampleCollection)
    c.scanner = types.SimpleNamespace(sdr=_sdr())
    monkeypatch.setattr(scan_dll, 'pll_stats', lambda sdr: (0, 0))
    c.report_pll()
    out = capsys.readouterr().out
    assert 'tuner I2C: 7 transfer(s) retried, 1 failed after retries' in out


# --- calibration: real steps up to ~3.9 dB off nominal are kept ---------------

def _rows(level_of_gain):
    rows = []
    for g in GAINS:
        p = np.full(400, 1e-9)
        p[100:140] = 1e-9 * 10 ** (level_of_gain(g) / 10.)
        rows.append((g, p, 0.0))
    return rows


def _rows_clip(level_of_gain, clip_of_gain):
    return [(g, p, clip_of_gain(g)) for g, p, _ in _rows(level_of_gain)]


def test_compressed_anchor_does_not_shorten_the_table():
    """V20.6.1, calibration of 10.11 17:21: the 534 MHz anchor grew 1.4 dB per
    2.6 dB step from 33.8 dB (0.2 % clipping, under the 1 % limit) - every
    step above came out short and 49.6 -> 29.7 gave 6.3 dB instead of 19.9."""
    def lvl(g):                     # linear up to 32.8, then half the growth
        return 30 + (g if g <= 32.8 else 32.8 + (g - 32.8) / 2.)
    clip = lambda g: 0. if g < 33.8 else 0.002 if g < 36.4 else 0.009
    rows = _rows_clip(lvl, clip)
    st = gain_cal.anchor_steps(rows, gain_cal.noise_floors([[rows]], GAINS))
    first_clip = GAINS.index(33.8)
    assert np.all(np.isnan(st[first_clip - 3:]))                  # 28.0 -> 29.7 and up: not used
    assert np.isfinite(st[first_clip - 4])                         # 25.4 -> 28.0 is
    t = gain_cal.build_table([480e6], [[rows, rows]], GAINS)
    assert abs(t['worst']['err']) < 0.1
    gain_cal._prepare(t)
    drop = gain_cal.real_drop(49.6, 29.7, 480e6, table=t)
    assert drop == pytest.approx(19.9, abs=0.01)                  # nominal where not measured, not 6.3


def test_calm_anchor_is_one_that_never_clipped():
    class S(object):
        def __init__(self, db, cr):
            self.powers = 10 ** (np.full(300, -100.) / 10.)
            self.powers[100:120] = 10 ** (db / 10.)
            self.clip_ratio = cr

    class C(object):
        sample_sets = {480e6: S(-40., 0.5), 484e6: S(-50., 0.002), 488e6: S(-60., 0.), 492e6: S(-100., 0.)}
    assert gain_cal.pick_anchors(C()) == [480e6, 488e6]


def test_real_step_3_6_db_off_nominal_is_kept():
    # survey 10.11: 240-400 MHz, 43.4 -> 43.9 dB gave +3.6 dB for 0.5 nominal
    lvl = lambda g: 30 + g + (3.1 if g >= 43.9 else 0.)
    t = gain_cal.build_table([300e6], [[_rows(lvl), _rows(lvl)]], GAINS)
    i = GAINS.index(43.9) - 1
    assert t['groups'][0]['steps'][i] == pytest.approx(3.6, abs=0.01)


def test_lte_like_step_is_still_dropped():
    lvl = lambda g: 30 + g + (4.3 if g >= 37.2 else 0.)
    t = gain_cal.build_table([600e6], [[_rows(lvl), _rows(lvl)]], GAINS)
    assert t['groups'][0]['steps'][GAINS.index(37.2) - 1] is None


# --- install_dll.py ----------------------------------------------------------

FAKE_RTLSDR = '''
import os
class _F(object):
    def __call__(self):
        data = open(self.path, 'rb').read()
        return b'... scanner-v5 ...' if b'scanner-v5' in data else b'... scanner-v4 ...'
class _Lib(object):
    pass
librtlsdr = _Lib()
librtlsdr._name = os.environ['FAKE_DLL']
f = _F(); f.path = os.environ['FAKE_DLL']
librtlsdr.rtlsdr_get_build_info = f
'''


@pytest.mark.skipif(sys.platform == 'win32', reason='uses a fake pyrtlsdr (Linux path lookup)')
def test_install_and_rollback(tmpdir):
    pkg = tmpdir.mkdir('fake').mkdir('rtlsdr')
    pkg.join('__init__.py').write('from .librtlsdr import librtlsdr\n')   # as in pyrtlsdr
    pkg.join('librtlsdr.py').write(FAKE_RTLSDR)
    dll = tmpdir.join('librtlsdr.dll')
    dll.write_binary(b'old dll scanner-v4')
    env = dict(os.environ, PYTHONPATH=str(tmpdir.join('fake')), FAKE_DLL=str(dll))
    script = os.path.join(V5, 'install_dll.py')

    def run(*a):
        r = subprocess.run([sys.executable, script] + list(a), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=env, timeout=60)
        return r.returncode, r.stdout.decode('utf-8', 'replace')
    rc, out = run()
    assert rc == 0 and 'OK - scanner-v5 installed' in out, out
    assert b'scanner-v5' in dll.read_binary()
    assert tmpdir.join('librtlsdr.dll.before-v5').read_binary() == b'old dll scanner-v4'
    rc, out = run()
    assert rc == 0 and 'already installed' in out, out
    rc, out = run('rollback')
    assert rc == 0 and dll.read_binary() == b'old dll scanner-v4', out


def test_v5_dll_is_the_built_one():
    p = os.path.join(V5, 'x64', 'rtlsdr.dll')
    data = open(p, 'rb').read()
    assert b'scanner-v5' in data and b'rtlsdr_get_i2c_stats' in data


# ------------------------------------------------------------------ Suggest gain

from wwb_scanner.scanner import gain_profile  # noqa: E402


def _profile(bands):
    """bands: {(lo, hi): (loss list, clip list)} over GAINS."""
    return {'version': 1, 'gains': GAINS, 'date': '2026-11-10 12:00:00', '_own': True,
            'bands': [{'lo': lo, 'hi': hi, 'tiles': 10, 'loss': l, 'clip': c}
                      for (lo, hi), (l, c) in sorted(bands.items())]}


def _curve(best_db, slope=1.):
    return [round(abs(g - best_db) * slope / 4., 2) for g in GAINS]


def test_builtin_profile_ships_and_loads(tmpdir, monkeypatch):
    monkeypatch.setattr(gain_profile, 'profile_path', lambda: str(tmpdir.join('none.json')))
    p = gain_profile.load_profile()
    assert p is not None and not p['_own'] and p['gains'] == GAINS
    assert [b['lo'] for b in p['bands']][:2] == [30, 88]
    g, text = gain_profile.suggest([(470., 700.)], True, GAINS)
    assert g == 49.6 and 'built-in' in text            # survey 10.11.2026: 470-700 best at 49.6
    g, _ = gain_profile.suggest([(30., 88.)], True, GAINS)
    assert g == 36.4                                    # VHF compresses at 49.6


def test_own_profile_wins_over_builtin(tmpdir, monkeypatch):
    path = str(tmpdir.join('gain_profile.json'))
    monkeypatch.setattr(gain_profile, 'profile_path', lambda: path)
    zero = [0.] * len(GAINS)
    gain_profile.save_profile(_profile({(400, 800): (_curve(29.7), zero)}), path)
    g, text = gain_profile.suggest([(470., 700.)], True, GAINS)
    assert g == 29.7 and 'your gain survey 2026-11-10' in text


def test_auto_gain_off_avoids_clipping_and_says_what_auto_gain_gives():
    clip = [0. if g <= 29.7 else 0.4 for g in GAINS]
    p = _profile({(470, 700): (_curve(49.6), clip)})
    g_on, _ = gain_profile.suggest([(470., 700.)], True, GAINS, p)
    g_off, text = gain_profile.suggest([(470., 700.)], False, GAINS, p)
    assert g_on == 49.6 and g_off == 29.7
    assert 'with Overload on: 49.6 dB' in text


def test_everything_clips_without_auto_gain():
    p = _profile({(470, 700): (_curve(49.6), [0.1 if g <= 7.7 else 0.5 for g in GAINS])})
    g, text = gain_profile.suggest([(470., 700.)], False, GAINS, p)
    assert g == 7.7 and 'even the lowest step clips' in text     # least clipping, then most sensitive


def test_ranges_weighted_by_coverage_and_tie_prefers_lower_gain():
    zero = [0.] * len(GAINS)
    p = _profile({(30, 88): (_curve(36.4, 4.), zero), (470, 700): (_curve(49.6, 4.), zero)})
    assert gain_profile.suggest([(470., 700.)], True, GAINS, p)[0] == 49.6
    assert gain_profile.suggest([(40., 80.)], True, GAINS, p)[0] == 36.4
    # mostly UHF: UHF wins
    assert gain_profile.suggest([(40., 80.), (470., 700.)], True, GAINS, p)[0] == 49.6
    flat = _profile({(470, 700): ([0.2 if g >= 44.5 else 1. for g in GAINS], zero)})
    assert gain_profile.suggest([(470., 700.)], True, GAINS, flat)[0] == 44.5


def test_only_gains_of_the_dongle_and_outside_the_profile():
    zero = [0.] * len(GAINS)
    p = _profile({(470, 700): (_curve(49.6), zero)})
    assert gain_profile.suggest([(470., 700.)], True, [0., 20.7, 40.2], p)[0] == 40.2
    g, text = gain_profile.suggest([(1300., 1700.)], True, GAINS, p)
    assert g == 49.6 and 'nearest band' in text
    assert gain_profile.suggest([(470., 700.)], True, [3.3], p)[0] is None


def test_profile_from_simulated_surveys(tmpdir):
    """gain_survey --analyze on two simulated surveys: one band per 150 MHz."""
    kit = tmpdir.mkdir('kit')
    script = str(kit.join('gain_survey.py'))
    with io.open(os.path.join(os.path.dirname(HERE), 'tools', 'gain_survey.py'), encoding='utf-8') as src, \
            io.open(script, 'w', encoding='utf-8') as dst:
        dst.write(src.read())
    # a copy: os.symlink needs a privilege an ordinary Windows user does not have
    import sim
    sim.copy_tests(str(kit.join('tests')))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.dirname(HERE)] + [p for p in [os.environ.get('PYTHONPATH')] if p]))
    for mode in ('--antenna', '--no-antenna'):
        r = subprocess.run([sys.executable, script, mode, '--simulate', '--step', '50', '--lo', '470', '--hi', '700',
                            '--passes', '1'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=600)
        assert r.returncode == 0, r.stdout[-2000:]
    sims = sorted(str(p) for p in kit.listdir() if p.isdir() and 'gain_survey_' in p.basename)
    ant = [p for p in sims if '_ant_' in p][0]
    noant = [p for p in sims if '_noant_' in p][0]
    out = str(tmpdir.join('profile.json'))
    r = subprocess.run([sys.executable, script, '--analyze', ant, noant + '.zip', '--out', out],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=300)
    text = r.stdout.decode('utf-8', 'replace')
    assert r.returncode == 0, text[-2000:]
    import json
    p = json.load(io.open(out, encoding='utf-8'))
    assert [(b['lo'], b['hi']) for b in p['bands']] == [(470, 560), (560, 700)]
    for b in p['bands']:
        assert len(b['loss']) == len(p['gains']) == len(b['clip'])
        assert min(b['loss']) == 0.
    assert 'Saved:' in text and '470-560' in text
