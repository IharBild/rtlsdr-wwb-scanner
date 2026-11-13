"""V21.2: second-gain check of narrow peaks next to strong carriers.
Products the tuner makes itself (LNA / mixer, no ADC clipping) fall with the
gain about three times as fast as a real signal; they are marked, never
removed."""
import numpy as np
import pytest

import sim
from wwb_scanner.scanner import sample_processing as sp

F1, F2 = 600.0, 600.4                 # two strong carriers (mics / IEMs near the antenna)
IM_LO, IM_HI = 599.6, 600.8           # 2 f1 - f2, 2 f2 - f1
WEAK = 601.5                          # a real weak carrier, as strong as the products


def _air(weak=True, extra=()):
    a = sim.Air(noise=0.01)
    a.tone(F1 * 1e6, 0.15)
    a.tone(F2 * 1e6, 0.15)
    if weak:
        a.tone(WEAK * 1e6, 0.15 * 10 ** (-25 / 20.))
    for mhz, amp in extra:
        a.tone(mhz * 1e6, amp)
    return a


def _scan(monkeypatch, tmpdir, air=None, k3=1.0, verify=True, gain=33.8, start=597., end=604., **kw):
    sk = {'air': air if air is not None else _air(), 'gain_affects': True}
    if k3:
        sk['lna'] = sim.Lna(k3=k3)
    return sim.run_scan(monkeypatch, tmpdir, start, end, gain=gain, sdr_kwargs=sk,
                        verify_peaks=verify, **kw)


def _by_f(checks, mhz):
    hit = [c for c in checks if abs(c['f'] - mhz) < 0.02]
    return hit[0] if hit else None


def test_products_found_real_signal_kept(monkeypatch, tmpdir, capsys):
    sc, sdr, _ = _scan(monkeypatch, tmpdir)
    coll = sc.sample_collection
    checks = coll.peak_checks
    print(checks)
    for m in (IM_LO, IM_HI):
        c = _by_f(checks, m)
        assert c is not None and c['kind'] == 'product', (m, checks)
        assert c['delta'] >= 0.7 * c['drop']
    assert set(_by_f(checks, m)['pair'] for m in (IM_LO, IM_HI)) == {(F1, F2), (F2, F1)}
    w = _by_f(checks, WEAK)
    assert w is not None and w['kind'] == 'real' and abs(w['delta']) <= 3., checks
    assert 'products of the dongle (not in the air) at 599.600, 600.800 MHz' in coll.overload_note
    assert '(checked with 11 dB less gain; marked, not removed)' in coll.overload_note
    marks = sc.spectrum.product_marks
    assert sorted(round(m['f'], 3) for m in marks) == [IM_LO, IM_HI]
    out = capsys.readouterr().out
    assert 'checking 3 peak(s) next to strong carriers with 10.9 dB less gain' in out
    assert 'peak check: 2 product(s) of the dongle, 1 real, 0 not clear' in out


def test_scan_data_are_not_changed(monkeypatch, tmpdir):
    on = _scan(monkeypatch, tmpdir)[0]
    off = _scan(monkeypatch, tmpdir, verify=False)[0]
    f1, y1 = sim.spectrum_arrays(on)
    f0, y0 = sim.spectrum_arrays(off)
    assert np.array_equal(f1, f0) and np.array_equal(y1, y0)
    assert off.sample_collection.peak_checks == [] and off.spectrum.product_marks == []
    assert on.spectrum.product_marks


def test_switched_off_reads_nothing_more(monkeypatch, tmpdir):
    sc_on, sdr_on, _ = _scan(monkeypatch, tmpdir)
    sc_off, sdr_off, _ = _scan(monkeypatch, tmpdir, verify=False)
    n_tiles = len(sc_off.sample_collection.sample_sets)
    checked = len(set(c['f'] for c in sc_on.sample_collection.peak_checks))
    assert sdr_off.calls['read_samples'] == 2 * n_tiles
    # 3 tiles, 3 readings each (discarded samples + data)
    assert sdr_on.calls['read_samples'] - sdr_off.calls['read_samples'] == 2 * 3 * 3, checked


def test_gain_is_set_back_and_low_readings_were_made(monkeypatch, tmpdir):
    sc, sdr, _ = _scan(monkeypatch, tmpdir)
    assert sdr.gain == pytest.approx(33.8)
    assert 22.9 in sdr.read_gains and sdr.gains_set[-1] == pytest.approx(33.8)


def test_one_strong_carrier_makes_no_products_nothing_checked(monkeypatch, tmpdir):
    a = sim.Air(noise=0.01)
    a.tone(F1 * 1e6, 0.15)
    a.tone(WEAK * 1e6, 0.15 * 10 ** (-25 / 20.))
    sc_on, sdr_on, _ = _scan(monkeypatch, tmpdir, air=a)
    a2 = sim.Air(noise=0.01)
    a2.tone(F1 * 1e6, 0.15)
    a2.tone(WEAK * 1e6, 0.15 * 10 ** (-25 / 20.))
    sc_off, sdr_off, _ = _scan(monkeypatch, tmpdir, air=a2, verify=False)
    assert sc_on.sample_collection.peak_checks == []
    assert sdr_on.calls['read_samples'] == sdr_off.calls['read_samples']


def test_linear_dongle_real_signals_only(monkeypatch, tmpdir):
    # the same air, no tuner nonlinearity: nothing is a product
    sc, sdr, _ = _scan(monkeypatch, tmpdir, k3=0)
    checks = sc.sample_collection.peak_checks
    assert all(c['kind'] != 'product' for c in checks), checks
    assert _by_f(checks, WEAK)['kind'] == 'real'
    assert sc.spectrum.product_marks == []
    assert 'products of the dongle' not in sc.sample_collection.overload_note


def test_real_carrier_on_a_product_frequency_is_not_called_a_product(monkeypatch, tmpdir):
    """A weak microphone on 2 f1 - f2 (10 kHz off, the same 25 kHz point):
    the product adds to it, but the real carrier keeps the peak up at the
    lower gain."""
    a = _air(extra=[(IM_LO + 0.01, 0.15 * 10 ** (-12 / 20.))])
    sc, sdr, _ = _scan(monkeypatch, tmpdir, air=a)
    c = _by_f(sc.sample_collection.peak_checks, IM_LO)
    assert c is not None and c['kind'] in ('real', 'unclear'), c
    assert _by_f(sc.sample_collection.peak_checks, IM_HI)['kind'] == 'product'


def test_gain_too_low_for_a_check(monkeypatch, tmpdir):
    # the same air 26 dB stronger, scanned at 3.7 dB: only 0 dB is lower
    a = sim.Air(noise=0.2)
    a.tone(F1 * 1e6, 3.0)
    a.tone(F2 * 1e6, 3.0)
    a.tone(WEAK * 1e6, 3.0 * 10 ** (-25 / 20.))
    sc, sdr, _ = _scan(monkeypatch, tmpdir, air=a, gain=3.7)
    coll = sc.sample_collection
    assert coll.peak_checks == []
    assert 'is too low for a reading with 10 dB less' in coll.peak_check_info
    assert 2.7 not in sdr.read_gains and 0.0 not in sdr.read_gains


def test_tile_limit(monkeypatch, tmpdir):
    # many carriers: many products in a wide range; at most VERIFY_MAX_TILES tiles
    monkeypatch.setattr(sp, 'VERIFY_MAX_TILES', 2)
    monkeypatch.setattr(sp, 'VERIFY_MIN_TILES', 2)
    a = sim.Air(noise=0.01)
    for m in (600.0, 600.4, 603.0, 603.5):
        a.tone(m * 1e6, 0.12)
    sc, sdr, _ = _scan(monkeypatch, tmpdir, air=a, start=596., end=608.)
    coll = sc.sample_collection
    assert coll.peak_checks and len(set(c['tile'] for c in coll.peak_checks)) <= 2
    assert coll.peak_check_skipped > 0
    assert 'peak(s) next to strong carriers not checked (tile limit)' in coll.overload_note


def test_dongle_closed_during_the_check(monkeypatch, tmpdir):
    """A USB error in the check: the scan keeps what it measured, the closed
    handle is not used again (no gain set back on it)."""
    n_scan = {}

    def late(call, freq):
        # the first check reading (after all tiles of the scan)
        return call == n_scan.get('n', 10 ** 9)
    sk_air = _air()
    off = _scan(monkeypatch, tmpdir, verify=False)
    n_scan['n'] = off[1].calls['read_samples'] + 1
    fault = sim.Fault('read_samples', 'close', late)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 597., 604., gain=33.8, verify_peaks=True,
                              sdr_kwargs={'air': sk_air, 'gain_affects': True, 'lna': sim.Lna(),
                                          'faults': [fault]})
    assert fault.hits == 1 and not sdr.device_opened
    f, y = sim.spectrum_arrays(sc)
    f0, y0 = sim.spectrum_arrays(off[0])
    assert np.array_equal(y, y0)                    # the scan as measured
    assert sdr.closed_calls == 0
    assert all(c['kind'] != 'product' for c in sc.sample_collection.peak_checks)


def test_clipping_mic_and_check_together(monkeypatch, tmpdir):
    """The V20 path (a mic next to the antenna clips the ADC) and the check
    run in the same scan: tiles read again with low gain are not checked."""
    a = sim.Air(noise=0.005)
    a.tone(632.925e6, 20.)
    a.tone(630.0e6, 0.2)
    a.tone(630.6e6, 0.2)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 626, 640, gain=33.8, auto_gain=True, verify_peaks=True,
                              sdr_kwargs={'air': a, 'gain_affects': True, 'wide_adc': sim.WideAdc()})
    coll = sc.sample_collection
    low = set(k for k, s in coll.sample_sets.items() if s.powers_low is not None)
    assert low
    for c in coll.peak_checks:
        assert c['kind'] in ('product', 'real', 'varying', 'unclear')
    assert sdr.gain == pytest.approx(33.8)


# --------------------------------------------------------------- classify_peak

@pytest.mark.parametrize('a,n,lows,floor,drop,kind', [
    (-80., -80.5, [-80.2, -79.9], -110., 10.9, 'real'),
    (-80., -80., [-101., -100.], -110., 10.9, 'product'),        # fell 20 dB (third order)
    (-80., -80., [-90.5, -90.], -110., 10.9, 'product'),         # fell ~10 dB (second order)
    (-90., -90., [-108., -108.5], -107., 10.9, 'product'),       # under the low floor: >= 14 dB
    (-90., -90., [-95., -95.], -107., 10.9, 'unclear'),          # fell 5 dB: neither
    (-80., -80., [-87., -87.], -110., 10.9, 'unclear'),          # 7 dB < 0.7 x 10.9
    (-100., -100., [-108., -108.], -107., 10.9, 'unclear'),      # too weak to tell
    (-80., -88., [-100., -100.], -110., 10.9, 'varying'),        # normal readings differ
    (-80., -80., [-80., -100.], -110., 10.9, 'varying'),         # low readings differ
    (-80., None, [-80., -80.], -110., 10.9, 'unclear'),          # a reading missing
])
def test_classify_peak(a, n, lows, floor, drop, kind):
    assert sp.classify_peak(a, n, lows, floor, drop)[0] == kind


def test_classify_bursty_real_signal_is_not_a_product():
    # on in the scan, off in the second normal reading
    assert sp.classify_peak(-80., -100., [-100., -100.], -110., 10.9)[0] == 'varying'


def test_narrow_peaks_ignores_wide_signals():
    f = np.round(np.arange(590., 610., 0.025), 4)
    y = np.full(f.size, -100.)
    y[(f > 595.) & (f < 603.)] = -80.                  # a DVB-T multiplex
    i = int(np.argmin(np.abs(f - 605.)))
    y[i] = -70.                                        # a narrow carrier
    y[i - 1] = y[i + 1] = -80.
    wide = (f > 595.) & (f < 603.)
    got = sp.narrow_peaks(f, y, -90., exclude=wide)
    assert [round(f[j], 3) for j, _ in got] == [605.0]


def test_usb_timeout_in_the_check_keeps_the_scan_complete(monkeypatch, tmpdir):
    """The dongle hangs during the check: the scan was fully measured, so it
    is not reported as stopped; the hanging dongle gets no more calls."""
    calls = {'n': 0}

    def stuck(self, sdr):
        calls['n'] += 1
        raise sp.SdrTimeout('read_samples did not return in 40 s')
    monkeypatch.setattr(sp.SampleSet, 'read_check', stuck)
    sc, sdr, _ = _scan(monkeypatch, tmpdir)
    coll = sc.sample_collection
    assert calls['n'] == 1
    assert not coll.aborted and not coll.missing_tiles
    assert coll.peak_check_info == 'peak check stopped: the dongle did not answer in time'
    assert 'peak check stopped' in coll.overload_note
    assert sdr.gains_set[-1] == pytest.approx(22.9)        # gain not sent again to a hanging dongle
    assert all(c['kind'] == 'unclear' for c in coll.peak_checks)
    assert sc.spectrum.product_marks == []
