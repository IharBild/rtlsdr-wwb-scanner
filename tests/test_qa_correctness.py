"""QA plan group K: correctness of the whole scan pipeline (Scanner.run_scan
with the dongle simulator). Numbers are printed so the report can quote them."""
import numpy as np
import pytest

import sim
from wwb_scanner.scanner import sample_processing as sp

BIN = 0.025


def scan(monkeypatch, tmpdir, start, end, air, **kw):
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, start, end,
                                    sdr_kwargs={'air': air}, **kw)
    f, y = sim.spectrum_arrays(scanner)
    return f, y, scanner, sdr, dt


def peak_near(f, y, f0, span=0.2):
    m = np.abs(f - f0) <= span
    i = np.argmax(np.where(m, y, -1e9))
    return f[i], y[i]


def floor(y):
    return np.median(y)


# K1 + K2: tone frequency and level at many positions inside tiles
def test_K1_K2_tone_frequency_and_level_anywhere_in_tile(monkeypatch, tmpdir):
    # tile centres: 470 + k*0.775 MHz. Put one tone per tile at a different
    # offset from -0.900 to +0.900 MHz (25 kHz grid), tones >= 3 MHz apart.
    offsets = np.arange(-0.900, 0.901, 0.150)
    tones = []
    air = sim.Air(noise=0.03)
    for k, off in enumerate(offsets):
        fc = 470.0 + 0.775 * (4 * k + 2)
        f0 = round((fc + off) / BIN) * BIN
        tones.append(f0)
        air.tone(f0 * 1e6, 0.02)
    f, y, *_ = scan(monkeypatch, tmpdir, 470, 470 + 0.775 * (4 * len(offsets) + 4), air)
    errs, levels = [], []
    for f0 in tones:
        fp, yp = peak_near(f, y, f0)
        errs.append(abs(fp - f0))
        levels.append(yp)
    print('K1 max freq error %.4f MHz, K2 level spread %.2f dB over %d positions'
          % (max(errs), np.ptp(levels), len(tones)))
    assert max(errs) < 1e-6
    assert np.ptp(levels) <= 0.5


# K3: tone exactly between two tile centres (in the overlap)
def test_K3_tone_in_tile_overlap(monkeypatch, tmpdir):
    air = sim.Air(noise=0.03)
    mid = 470.0 + 0.775 * 10 + 0.775 / 2
    f_on = round(mid / BIN) * BIN
    f_ctr = 470.0 + 0.775 * 20 + 0.4
    air.tone(f_on * 1e6, 0.02).tone(round(f_ctr / BIN) * BIN * 1e6, 0.02)
    f, y, *_ = scan(monkeypatch, tmpdir, 470, 490, air)
    _, y1 = peak_near(f, y, f_on)
    _, y2 = peak_near(f, y, round(f_ctr / BIN) * BIN)
    print('K3 overlap tone %.2f dB vs normal %.2f dB' % (y1, y2))
    assert abs(y1 - y2) <= 0.5
    # one peak, not two
    m = np.abs(f - f_on) <= 0.1
    assert (y[m] > floor(y) + 10).sum() <= 3


# K4: strong DC offset of the dongle must not show up at tile centres
def test_K4_dc_offset_not_in_result(monkeypatch, tmpdir):
    air = sim.Air(noise=0.03)
    air.dc = 0.05 + 0.03j
    f, y, *_ = scan(monkeypatch, tmpdir, 470, 490, air)
    centres = 470.0 + 0.775 * np.arange(0, 26)
    worst = max(peak_near(f, y, c, span=0.03)[1] for c in centres if c <= 490)
    print('K4 worst level at tile centres %.2f dB, floor %.2f dB' % (worst, floor(y)))
    assert worst < floor(y) + 1.5


# K5: flat noise, no steps, no ripple at the tile period
def test_K5_flat_noise_no_ripple(monkeypatch, tmpdir):
    air = sim.Air(noise=0.03)
    f, y, *_ = scan(monkeypatch, tmpdir, 470, 570, air)
    d = np.abs(np.diff(y))
    period = 31  # 0.775 MHz / 25 kHz
    n = (y.size // period) * period
    ph = (y[:n] - np.convolve(y, np.ones(201) / 201, 'same')[:n]).reshape(-1, period).mean(0)
    print('K5 std %.3f dB, max neighbour step %.2f dB, tile-period ripple %.3f dB'
          % (np.std(y), d.max(), np.ptp(ph)))
    assert np.std(y) <= 0.5
    assert d.max() <= 1.5
    assert np.ptp(ph) <= 0.3


# K7: every window type: flat floor and the tone at the right place
@pytest.mark.parametrize('window_type', sorted(set(sp.WINDOW_TYPES) | {'hanning'}))
def test_K7_all_window_types(monkeypatch, tmpdir, window_type):
    air = sim.Air(noise=0.03).tone(480.000e6, 0.02)
    f, y, *_ = scan(monkeypatch, tmpdir, 470, 490, air, window_type=window_type)
    fp, yp = peak_near(f, y, 480.0)
    far = np.abs(f - 480.0) > 0.5
    yf = y[far]
    period = 31
    n = (yf.size // period) * period
    ph = (yf[:n] - np.median(yf)).reshape(-1, period).mean(0)
    print('K7 %-16s peak %.3f MHz (+%.1f dB), floor std %.3f dB, tile ripple %.3f dB'
          % (window_type, fp, yp - np.median(yf), np.std(yf), np.ptp(ph)))
    assert abs(fp - 480.0) < 1e-6
    assert np.std(yf) <= 0.6
    assert np.ptp(ph) <= 0.5


# K8: 7.6 MHz TV channel: flat top, sharp edges
def test_K8_tv_channel_shape(monkeypatch, tmpdir):
    lo, hi = 474.19, 481.81
    air = sim.Air(noise=0.01).band(lo * 1e6, hi * 1e6, 0.05)
    f, y, *_ = scan(monkeypatch, tmpdir, 468, 488, air)
    top = y[(f > lo + 0.3) & (f < hi - 0.3)]
    mid = (np.median(top) + floor(y[(f < lo - 1) | (f > hi + 1)])) / 2
    rise = f[np.flatnonzero((y[:-1] < mid) & (y[1:] >= mid))[0] + 1]
    fall = f[np.flatnonzero((y[:-1] >= mid) & (y[1:] < mid))[-1]]
    print('K8 top std %.2f dB, edges %.3f / %.3f MHz (true %.2f / %.2f)'
          % (np.std(top), rise, fall, lo, hi))
    assert np.std(top) <= 0.6
    assert abs(rise - lo) <= 2 * BIN and abs(fall - hi) <= 2 * BIN


# K9: FM stations on the 100 kHz raster
def test_K9_fm_raster(monkeypatch, tmpdir):
    rng = np.random.RandomState(5)
    stations = sorted(set(np.round(rng.uniform(88.0, 108.0, 20), 1)))
    air = sim.Air(noise=0.03)
    for s in stations:
        air.tone(s * 1e6, 0.03)
    f, y, *_ = scan(monkeypatch, tmpdir, 87.5, 108.5, air)
    hits = sum(abs(peak_near(f, y, s, span=0.04)[0] - s) < 1e-6 for s in stations)
    print('K9 %d / %d stations exactly on their frequency' % (hits, len(stations)))
    assert hits == len(stations)


# K10: two close tones
@pytest.mark.parametrize('sep_khz,resolved', [(25, False), (50, True), (75, True), (100, True)])
def test_K10_two_close_tones(monkeypatch, tmpdir, sep_khz, resolved):
    f1 = 480.0
    f2 = f1 + sep_khz / 1000.
    air = sim.Air(noise=0.01).tone(f1 * 1e6, 0.02).tone(f2 * 1e6, 0.02)
    f, y, *_ = scan(monkeypatch, tmpdir, 478, 482, air)
    y1 = peak_near(f, y, f1, 0.001)[1]
    y2 = peak_near(f, y, f2, 0.001)[1]
    between = y[(f > f1 + 0.001) & (f < f2 - 0.001)]
    dip = min(y1, y2) - (between.min() if between.size else min(y1, y2))
    print('K10 %3d kHz: dip between peaks %.1f dB' % (sep_khz, dip))
    if resolved:
        assert dip >= 3.0
    else:
        assert dip < 3.0


# K11: weak tones near the floor, and level linearity
@pytest.mark.parametrize('snr_db', [3, 6, 10, 20, 30])
def test_K11_weak_tone_level(monkeypatch, tmpdir, snr_db):
    noise = 0.03
    # hann ENBW 1.5 bins: noise power in one bin
    bin_noise = noise ** 2 * (1.5 * 25e3 / 2.4e6)
    amp = np.sqrt(bin_noise * 10 ** (snr_db / 10.))
    air = sim.Air(noise=noise).tone(480.0e6, amp)
    f, y, *_ = scan(monkeypatch, tmpdir, 476, 484, air)
    yp = peak_near(f, y, 480.0, 0.001)[1]
    fl = np.median(y[np.abs(f - 480.0) > 0.3])
    got = 10 * np.log10(10 ** ((yp - fl) / 10.) - 1) if yp - fl > 0.05 else -99
    print('K11 expected +%d dB above floor, measured tone %.2f dB above floor' % (snr_db, got))
    assert abs(got - snr_db) <= 1.5


# K12: weak tone 1 MHz from a strong one
def test_K12_dynamic_range(monkeypatch, tmpdir):
    # noise of a few ADC steps, as on the real dongle (below 1 step the
    # quantisation itself makes spurs)
    air = sim.Air(noise=0.01).tone(480.0e6, 0.3).tone(481.0e6, 0.003)
    f, y, *_ = scan(monkeypatch, tmpdir, 476, 485, air)
    ys = peak_near(f, y, 480.0, 0.001)[1]
    yw = peak_near(f, y, 481.0, 0.001)[1]
    fl = np.median(y[(np.abs(f - 480.0) > 0.5) & (np.abs(f - 481.0) > 0.3)])
    lin = lambda v: 10 ** (v / 10.)
    yw_net = 10 * np.log10(lin(yw) - lin(fl))
    ys_net = 10 * np.log10(lin(ys) - lin(fl))
    print('K12 strong %.1f, weak %.1f dB (net difference %.1f dB, should be 40), floor %.1f dB'
          % (ys, yw, ys_net - yw_net, fl))
    assert abs((ys_net - yw_net) - 40) <= 1.5
    assert yw > fl + 6


# K13: 8-bit clipping: record what happens (spurious peaks)
def test_K13_clipping_behaviour(monkeypatch, tmpdir):
    air = sim.Air(noise=0.02).tone(480.0e6, 1.2)
    f, y, *_ = scan(monkeypatch, tmpdir, 470, 490, air)
    fl = np.median(y)
    far = np.abs(f - 480.0) > 1.2
    spurs = (y[far] > fl + 10).sum()
    print('K13 overloaded tone: %d points >10 dB above floor more than 1.2 MHz away; worst %.1f dB above floor'
          % (spurs, y[far].max() - fl))
    assert np.isfinite(y).all()


# K15: averaging, std of the floor ~ 1/sqrt(N)
def test_K15_averaging_law(monkeypatch, tmpdir):
    stds = {}
    for sweeps in (10, 40, 160):
        air = sim.Air(noise=0.03)
        f, y, *_ = scan(monkeypatch, tmpdir, 470, 490, air, sweeps=sweeps)
        stds[sweeps] = np.std(y)
    r = stds[10] / stds[160]
    print('K15 floor std: %s, ratio 10/160 = %.2f (theory 4.0)' % (
        ', '.join('%d: %.3f dB' % kv for kv in sorted(stds.items())), r))
    assert 3.0 <= r <= 5.0


# K17/K20: low edge of the range, 0-35 MHz (V4 HF upconverter area)
@pytest.mark.parametrize('rng', [(0, 35), (24, 31), (28, 30), (0, 1)])
def test_K20_low_edge(monkeypatch, tmpdir, rng):
    air = sim.Air(noise=0.03).tone(28.8e6, 0.02)
    f, y, *_ = scan(monkeypatch, tmpdir, rng[0], rng[1], air)
    n_exp = int(round((rng[1] - rng[0]) / BIN)) + 1
    print('K20 %s: %d points (expected %d), first %.3f last %.3f, NaN %d'
          % (rng, f.size, n_exp, f.min(), f.max(), np.isnan(y).sum()))
    assert np.isfinite(y).all()
    assert abs(f.min() - rng[0]) < 1e-6 and abs(f.max() - rng[1]) < 1e-6
    assert f.size == n_exp


# K21: other sample rates
@pytest.mark.parametrize('fs', [1.024e6, 2.048e6, 2.4e6,
    pytest.param(2.56e6, marks=pytest.mark.xfail(reason='fs/nfft = 25.098 kHz: tiles do not share bins, near-duplicate points (QA finding)', strict=True)),
    3.2e6])
def test_K21_sample_rates(monkeypatch, tmpdir, fs):
    window = int(round(fs / 25e3))
    air = sim.Air(noise=0.03).tone(480.0e6, 0.02)
    f, y, scanner, sdr, dt = scan(monkeypatch, tmpdir, 470, 490, air, fs=fs, window=window)
    df = np.diff(f)
    fp = peak_near(f, y, 480.0)[0]
    print('K21 fs %.3f MHz win %d: %d points, step min/max %.4f/%.4f MHz, peak %.4f, %d tiles, %.2f s'
          % (fs / 1e6, window, f.size, df.min(), df.max(), fp, len(sdr.tuned), dt))
    assert np.all(df > 0.5 * fs / window / 1e6)   # no near-duplicate points
    assert df.max() <= fs / window / 1e6 * 1.01
    assert abs(fp - 480.0) <= fs / window / 1e6


# K22: window size and overlap ratio grid: no holes, no duplicates
@pytest.mark.parametrize('window', [48, 96, 192])
@pytest.mark.parametrize('overlap', [0.3, 0.5, 0.65, 0.8, 0.9])
def test_K22_window_overlap_grid(monkeypatch, tmpdir, window, overlap):
    air = sim.Air(noise=0.03)
    f, y, scanner, sdr, dt = scan(monkeypatch, tmpdir, 470, 480, air, window=window, overlap=overlap)
    res = 2.4 / window
    df = np.diff(f)
    print('K22 win %3d overlap %.2f: %d points, step %.4f..%.4f (res %.4f), %d tiles, std %.2f'
          % (window, overlap, f.size, df.min(), df.max(), res, len(sdr.tuned), np.std(y)))
    assert np.all(df > 0)
    assert df.max() <= res * 1.01 + 1e-9
    assert np.isfinite(y).all()


# K23: real IQ recordings through the whole pipeline, regression vs stored result
def test_K23_real_iq_regression(monkeypatch, tmpdir):
    rep = sim.IQReplay()
    if not rep.available():
        pytest.skip('real IQ recordings not available')
    import os
    ref = os.path.join(os.path.dirname(__file__), 'reference', 'qa_real_iq_595_605.npz')
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 595, 605, sdr_kwargs={'air': rep})
    f, y = sim.spectrum_arrays(scanner)
    assert np.isfinite(y).all()
    if not os.path.exists(ref):
        np.savez(ref, f=f, y=y)
        pytest.skip('reference created')
    r = np.load(ref)
    assert np.array_equal(np.round(r['f'], 6), np.round(f, 6))
    d = np.abs(r['y'] - y)
    print('K23 real IQ 595-605: %d points, max diff vs reference %.4f dB' % (f.size, d.max()))
    assert d.max() <= 0.05
