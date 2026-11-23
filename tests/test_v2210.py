"""V22.10 (decisions of 22-23.11.2026).

A4   noise check of "Own gain per band": a higher gain is kept only when the
     floor referred to the input gets at least 1 dB lower.
IF   the IF filter of the tuner is at most 2.4 MHz (above 2.43 MHz the
     tuner went to its 6 MHz mode: 4-6 dB lower output).
WIN  the window size of the settings is for 2.4 MS/s and follows the
     sample rate: a point stays 25 kHz wide.
COMB the 4.16 MHz comb of the own kit is found, hidden like own spurs
     (up to +7 dB), stronger lines kept; raw data untouched.
"""
import numpy as np
import pytest

import sim
from wwb_scanner.scanner import gain_plan as gp, cleanup as C, main
from wwb_scanner.scanner.config import effective_window, ScanConfig
from wwb_scanner.receivers import rtlsdr as rr
from wwb_scanner.receivers import as_receiver

NOMINAL = (lambda g_hi, g_lo, f=None: float(g_hi) - float(g_lo))


def _gains(rx):
    return as_receiver(rx).gains()


def _rx(**kw):
    air = kw.pop('air', None) or sim.Air(noise=0.004).tone(600.0e6, 0.02)
    rx = sim.FakeRtlSdr(air=air, gain_affects=True, **kw)
    rx.sample_rate = 2.4e6
    rx.gain = 33.8
    return rx


# ---------------------------------------------------------------- A4 noise check

def test_floor_db_ignores_carriers():
    rng = np.random.RandomState(0)
    n = 16384
    x = (rng.randn(n) + 1j * rng.randn(n)) * 0.01
    t = np.arange(n)
    y = x + 0.3 * np.exp(2j * np.pi * 0.11 * t)
    assert abs(gp.floor_db(x) - gp.floor_db(y)) < 0.3
    assert abs(gp.floor_db(2 * x) - gp.floor_db(x) - 6.02) < 0.3
    assert gp.floor_db(x[:500]) is None


def test_noise_choice_rules():
    assert gp.noise_choice(33.8, [(49.6, 0.6), (40.2, 0.9)]) == 33.8          # < 1 dB
    assert gp.noise_choice(33.8, [(49.6, 4.0), (40.2, 3.8), (36.4, 1.5)]) == 40.2   # lowest within 0.5
    assert gp.noise_choice(33.8, [(49.6, 1.1), (40.2, 3.0)]) == 40.2          # compression above
    assert gp.noise_choice(33.8, [(49.6, None), (40.2, 2.0)]) == 40.2
    assert gp.noise_choice(33.8, []) == 33.8


def test_noise_gains_spread():
    cands = [36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]
    got = gp.noise_gains(33.8, cands, 49.6)
    assert got[0] == 49.6 and len(got) == gp.NOISE_MAX_GAINS and got == sorted(got, reverse=True)
    assert gp.noise_gains(33.8, cands, 37.2) == [37.2, 36.4]


def test_higher_gain_kept_when_the_floor_gets_lower():
    rx = _rx(rx_noise=0.01)                 # the dongle's own noise dominates
    plan = gp.make_plan(rx, [(560., 700.)], 33.8, _gains(rx), 2.4e6)
    b = plan.bands[0]
    assert b.gain > 36.4, b.why
    assert 'noise:' in b.why and b.noise
    assert max(d for _, d in b.noise if d is not None) >= gp.NOISE_MIN_DB
    assert rx.gain == 33.8


def test_higher_gain_refused_when_the_air_sets_the_floor():
    """With the antenna the air sets the floor: more gain only raises the
    floor on the screen (before V22.10 the probe took 49.6 dB here)."""
    rx = _rx(air=sim.Air(noise=0.05).tone(600.0e6, 0.02))
    plan = gp.make_plan(rx, [(560., 700.)], 33.8, _gains(rx), 2.4e6)
    b = plan.bands[0]
    assert b.gain == 33.8, b.why
    assert 'noise:' in b.why
    assert all(d is None or d < gp.NOISE_MIN_DB for _, d in b.noise)
    assert not plan.banded


def test_compression_keeps_a_middle_gain():
    """Above the knee the floor rises faster than the gain (200 MHz on the
    user's dongle): the gain below the knee is taken, not 49.6."""
    rx = _rx(rx_noise=0.01, compress_above=40.5)
    plan = gp.make_plan(rx, [(560., 700.)], 33.8, _gains(rx), 2.4e6)
    b = plan.bands[0]
    assert 36.0 < b.gain <= 40.5, b.why
    assert dict(b.noise).get(49.6, 0) is None or dict(b.noise)[49.6] < dict(b.noise)[b.gain]


def test_every_band_is_probed_at_the_gain_of_the_scan():
    """Before V22.10 the check of a band left its gain set, and the next band
    was probed at it."""
    rx = _rx(rx_noise=0.01)
    log = []
    real = sim.FakeRtlSdr._read_samples

    def rec(self, n):
        log.append((self._fc, self._gain, n))
        return real(self, n)
    sim.FakeRtlSdr._read_samples = rec
    try:
        plan = gp.make_plan(rx, [(560., 700.), (700., 960.)], 33.8, _gains(rx), 2.4e6)
    finally:
        sim.FakeRtlSdr._read_samples = real
    first_700 = [g for fc, g, n in log if fc >= 700e6 and n == gp.PROBE_SAMPLES]
    assert plan.bands[0].gain > 33.8
    assert first_700 and first_700[0] == 33.8


def test_noise_check_is_short():
    rx = _rx(rx_noise=0.01)
    plan = gp.make_plan(rx, [(470., 960.)], 33.8, _gains(rx), 2.4e6)
    assert plan.seconds < 20


# ---------------------------------------------------------------- IF filter

@pytest.mark.parametrize('fs,bw', [(2.4e6, 2400000), (3.2e6, 2400000), (2.56e6, 2400000),
                                   (2.048e6, 2048000), (1.024e6, 1024000), (None, 0)])
def test_if_bandwidth_for(fs, bw):
    assert rr.if_bandwidth_for(fs) == bw


def test_open_sets_the_if_filter(monkeypatch, tmpdir):
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 476., fs=3.2e6)
    assert sc.error is None
    assert sdr.get_bandwidth() == 2400000
    rx = rr.RtlSdrReceiver(sdr)
    sdr.device_opened = True
    rx.sample_rate = 1.024e6
    assert sdr.get_bandwidth() == 1024000


def test_old_library_without_bandwidth_is_fine():
    class Old(object):
        sample_rate = 3.2e6
    assert rr.set_if_bandwidth(Old()) is False


# ---------------------------------------------------------------- window follows the rate

@pytest.mark.parametrize('fs,w', [(2.4e6, 96), (3.2e6, 128), (2.048e6, 82), (2.56e6, 102),
                                  (1.024e6, 41)])
def test_effective_window(fs, w):
    assert effective_window(96, fs) == w
    assert effective_window(96, fs, follows=False) == 96


def test_scan_at_3_2_ms_has_25_khz_points(monkeypatch, tmpdir):
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 480., fs=3.2e6)
    assert sc.window_size == 128
    f = np.asarray(sc.spectrum.sample_data['frequency'], float)
    assert np.median(np.diff(f)) == pytest.approx(0.025, abs=1e-4)


def test_window_follows_can_be_off():
    conf = sim.make_config(470, 480)
    conf['sampling']['sample_rate'] = 3.2e6
    conf['sampling']['window_follows_rate'] = False
    sc = main.Scanner(config=conf, history=None)
    assert sc.window_size == 96
    conf['sampling']['window_follows_rate'] = True
    assert main.Scanner(config=conf, history=None).window_size == 128


def test_tested_default_follows():
    assert ScanConfig().sampling.get('window_follows_rate') is True


# ---------------------------------------------------------------- comb of the own kit

def _floor(lo, hi, seed=3):
    rng = np.random.RandomState(seed)
    f = np.round(np.arange(lo, hi + 1e-9, 0.025), 4)
    return f, -91. + rng.randn(f.size) * 0.15


def _add_line(f, y, mhz, db):
    i = int(np.argmin(np.abs(f - mhz)))
    p = C.from_db(y)
    base = p[i]
    for k, frac in ((-1, 0.35), (0, 1.0), (1, 0.35)):
        p[i + k] += base * (10 ** (db / 10.) - 1) * frac
    y[:] = C.to_db(p)
    return f[i]


def _comb(f, y, db=4.0):
    out = []
    for n in range(int(np.ceil(f[0] / C.COMB_F0_MHZ)), int(f[-1] / C.COMB_F0_MHZ) + 1):
        m = n * C.COMB_F0_MHZ
        if f[0] + 0.5 < m < f[-1] - 0.5:
            out.append(_add_line(f, y, m, db))
    return out


def test_comb_is_found_and_hidden_raw_untouched():
    f, y = _floor(470., 700.)
    lines = _comb(f, y, 4.0)
    raw = y.copy()
    y2, info = C.hide_comb(f, y)
    assert info and info['f0_mhz'] == pytest.approx(C.COMB_F0_MHZ, abs=2e-4)
    assert len(info['hidden']) >= len(lines) - 2
    assert np.array_equal(y, raw)                          # input not changed
    for m in lines:
        i = int(np.argmin(np.abs(f - m)))
        assert y2[i] - np.median(y2[i - 20:i + 20]) < 0.8


def test_strong_line_on_the_comb_is_kept():
    f, y = _floor(470., 700.)
    _comb(f, y, 4.0)
    m = 116 * C.COMB_F0_MHZ              # 482.555
    _add_line(f, y, m, 15.0)
    y2, info = C.hide_comb(f, y)
    assert any(abs(k[0] - m) < 0.03 for k in info['kept'])
    i = int(np.argmin(np.abs(f - m)))
    assert y2[i] - np.median(y2[i - 20:i + 20]) > 10


def test_no_comb_in_plain_noise_with_random_peaks():
    rng = np.random.RandomState(5)
    for seed in range(30):
        f, y = _floor(31., 1766., seed)
        for c in rng.uniform(32, 1765, 300):
            _add_line(f, y, c, rng.uniform(1, 6))
        assert C.find_comb(f, y)[0] is None


def test_short_scan_uses_the_known_comb():
    f, y = _floor(470., 480.)
    _comb(f, y, 4.0)                          # 474.235, 478.395
    y2, info = C.hide_comb(f, y)
    assert info and sorted(round(x, 1) for x in info['hidden']) == [474.2, 478.4]


def test_clean_hides_the_comb_under_the_spur_switch():
    f, y = _floor(470., 700.)
    _comb(f, y, 4.0)
    _, info = C.clean(f, y, 470., 31, ripple=False, spurs=True, flatten=False)
    assert info.get('comb') and info['comb']['hidden']
    _, info = C.clean(f, y, 470., 31, ripple=False, spurs=False, flatten=False)
    assert 'comb' not in info
    note = main.comb_note_parts({'comb': {'f0_mhz': 4.15996, 'hidden': [474.2, 478.4],
                                          'kept': [[482.55, 15.0]]}})
    assert '2 line(s) of the 4.160 MHz comb' in note[0] and '482.550' in note[1]


def test_band_infos_merge_the_comb():
    a = {'spurs': True, 'comb': {'f0_mhz': 4.15996, 'hidden': [474.2], 'kept': []}}
    b = {'spurs': True, 'comb': {'f0_mhz': 4.15996, 'hidden': [599.0], 'kept': [[603.2, 9.0]]}}
    out = main.merge_band_infos([a, b])
    assert out['comb']['hidden'] == [474.2, 599.0] and out['comb']['kept'] == [[603.2, 9.0]]


def test_graph_marks_the_comb():
    pytest.importorskip('PySide6')
    from wwb_scanner.ui.pyside.graph import SpectrumGraphData

    class S(object):
        cleanup_info = {'spurs': True, 'spurs_removed': [480.0],
                        'comb': {'f0_mhz': 4.16, 'hidden': [474.235], 'kept': [[478.395, 9.0]]}}
    marks = SpectrumGraphData.spur_marks_of(S())
    assert [m['f'] for m in marks] == [474.235, 478.395, 480.0]
    assert [m['kept'] for m in marks] == [False, True, False]
