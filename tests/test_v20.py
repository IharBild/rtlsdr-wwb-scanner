"""V20: a strong carrier next to the antenna (user's series 08.11.2026:
EW100 G3 mic on 632.925 MHz, gain 0 / 7 / 21 / 33.8 / 49.6 dB).

1. gain goes down to the lowest value of the table, the least clipped
   reading is used;
2. tiles up to 4 MHz from the carrier are read again with that low gain
   (the dongle's internal ADC clips there, the 8 output bits do not show it);
3. merge min(normal, low gain) where the low-gain reading shows no signal,
   with the usual floor as the lower limit and the deafness of the normal
   reading added back; low-floor-only bins count only where nothing better;
4. the status line names the band that lost sensitivity and by how much.

Every scan test here fails on V19.1 (checked) and passes on V20.
"""
import logging

import numpy as np
import pytest

import sim
from wwb_scanner.scanner import sample_processing as sp
from wwb_scanner.scan_objects.samplearray import SampleArray

MIC = 632.925
WEAK = [631.3, 631.85, 634.2, 635.9]
START, END = 626, 640


def _air(mic=True):
    a = sim.Air(noise=0.005)
    if mic:
        a.tone(MIC * 1e6, 20.)          # ~45 dB above the clipping point at 49.6 dB
    for f in WEAK:
        a.tone(f * 1e6, 0.004)
    return a


def _scan(monkeypatch, tmpdir, gain, mic=True, auto_gain=True, **sdr):
    kw = {'air': _air(mic), 'gain_affects': True, 'wide_adc': sim.WideAdc()}
    kw.update(sdr)
    return sim.run_scan(monkeypatch, tmpdir, START, END, gain=gain, auto_gain=auto_gain, sdr_kwargs=kw)


def _pair(monkeypatch, tmpdir, gain):
    truth, _, _ = _scan(monkeypatch, tmpdir, gain, mic=False)
    sc, sdr, dt = _scan(monkeypatch, tmpdir, gain)
    f, y = sim.spectrum_arrays(sc)
    ft, yt = sim.spectrum_arrays(truth)
    assert np.array_equal(f, ft)
    return sc, sdr, f, y, yt


def _quiet_error(f, y, yt):
    near = (np.abs(f - MIC) > 0.15) & (np.abs(f - MIC) <= 3)
    for w in WEAK:
        near &= np.abs(f - w) > 0.05
    return y[near] - yt[near]


def _at(f, y, mhz):
    return float(y[int(np.argmin(np.abs(f - mhz)))])


# ---------------------------------------------------------------- units

def test_gain_steps_go_down_to_the_lowest_gain():
    gains = [g / 10. for g in sim.R828D_GAINS]
    steps = sp.SampleSet.lower_gain_steps(49.6, gains)
    assert steps[-1] == 0.0
    assert steps == sorted(steps, reverse=True) and len(set(steps)) == len(steps)
    assert all(g < 49.6 for g in steps)
    assert sp.SampleSet.lower_gain_steps(0.0, gains) == []


def test_merge_keeps_signals_of_low_gain_and_removes_plateau_and_false_peaks():
    n = 64
    floor = np.full(n, 1e-10)
    normal = floor.copy()
    normal[10] = 1e-7                        # false peak (clipping) in the normal reading
    low = np.full(n, 1e-8)                   # plateau: floor of the low gain, +20 dB
    low[30] = 1e-5                           # the carrier: only the low gain has its level right
    normal[30] = 1e-7
    merged, sig, low_floor = sp.merge_low_gain(normal, low, floor=floor)
    assert sig[30] and merged[30] == low[30]
    assert merged[10] == low[10]             # false peak cut to the low-gain floor
    assert np.allclose(merged[[0, 5, 50]], floor[[0, 5, 50]])   # plateau gone
    assert low_floor == pytest.approx(-80.0, abs=0.01)
    # a deaf normal reading is not taken below the usual floor
    deaf = normal * 1e-3
    m2, _, _ = sp.merge_low_gain(deaf, low, floor=floor)
    assert np.allclose(m2[[0, 5, 50]], floor[[0, 5, 50]])
    # bins limited by the low-gain floor
    lim = sp.low_limited_bins(normal, low, sig)
    assert lim[10] and not lim[0] and not lim[30]


def test_merge_points_with_one_weight_each():
    a = SampleArray.create(frequency=np.array([1.0, 1.025, 1.05]), magnitude=np.array([1.0, 1.0, 1.0]))
    b = SampleArray.create(frequency=np.array([1.025, 1.05, 1.075]), magnitude=np.array([100., 100., 100.]))
    a.insert_sorted(b, np.array([0.01, 1.0, 1.0]))
    m = np.asarray(a.magnitude)
    assert m[1] == pytest.approx((1.0 + 0.01 * 100.) / 1.01)
    assert m[2] == pytest.approx(50.5)
    assert m[3] == 100.
    assert np.asarray(a._get_counts()).tolist() == pytest.approx([1., 1.01, 2., 1.])


# ---------------------------------------------------------------- scans

def test_49_6_db_carrier_level_right_and_weak_signals_not_hidden(monkeypatch, tmpdir):
    """V19.1: the mic 14.5 dB too low (30 dB drop not enough), weak signals
    up to 3 MHz away 24-28 dB too low (dongle deaf, levels looked "free")."""
    sc, sdr, f, y, yt = _pair(monkeypatch, tmpdir, 49.6)
    ref0, _, _ = _scan(monkeypatch, tmpdir, 0.0)                 # no clipping at 0 dB
    f0, y0 = sim.spectrum_arrays(ref0)
    mic = float(y[np.abs(f - MIC) <= 0.05].max())
    want = float(y0[np.abs(f0 - MIC) <= 0.05].max()) + 49.6     # levels are scaled to the set gain
    assert abs(mic - want) < 1.5, (mic, want)
    assert 0.0 in sdr.read_gains                                  # went down to the lowest gain
    for w in WEAK:
        assert _at(f, y, w) - _at(f, yt, w) > -8., (w, _at(f, y, w), _at(f, yt, w))
    err = _quiet_error(f, y, yt)
    assert np.percentile(err, 10) > -3.                            # nothing pushed below the floor
    assert sdr.gains_set[-1] == 49.6                               # gain set back


def test_moderate_overload_no_plateau(monkeypatch, tmpdir):
    """V19.1 at 20.7 dB: a +20 dB plateau around the mic (mean error +6.5 dB)."""
    sc, sdr, f, y, yt = _pair(monkeypatch, tmpdir, 20.7)
    err = _quiet_error(f, y, yt)
    assert np.mean(err) < 3.0 and np.percentile(err, 90) < 8.0, (np.mean(err), np.percentile(err, 90))
    assert np.percentile(err, 10) > -3.


def test_neighbour_tiles_read_again_with_low_gain(monkeypatch, tmpdir, caplog):
    caplog.set_level(logging.INFO, logger='wwb_scanner')
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8)
    coll = sc.sample_collection
    assert coll.neighbours, 'no neighbour tile read again'
    carriers = [s.carrier_hz for s in coll.sample_sets.values() if s.carrier_hz is not None]
    assert carriers and all(abs(c / 1e6 - MIC) < 0.05 for c in carriers)
    for k in coll.neighbours:
        s = coll.sample_sets[k]
        assert s.neighbour and s.powers_low is not None
        assert min(abs(k - c) for c in carriers) <= sp.NEIGHBOUR_HZ
        assert s.clip_ratio < sp.OVERLOAD_RATIO              # its own output did not clip
    far = [k for k in coll.sample_sets if min(abs(k - c) for c in carriers) > sp.NEIGHBOUR_HZ]
    assert far and all(coll.sample_sets[k].powers_low is None for k in far)
    assert sdr.gains_set[-1] == 33.8
    # status line: band, dB and the signal
    done = [r.getMessage() for r in caplog.records
            if r.name == 'wwb_scanner.status' and 'done in' in r.getMessage()]
    assert done and 'dB less sensitive' in done[-1] and '632.925' in done[-1], done
    lo, hi, loss, c = coll.sensitivity[0]
    assert lo < MIC < hi and loss > 10 and c == pytest.approx(MIC, abs=0.05)


def test_nothing_changes_without_overload(monkeypatch, tmpdir):
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8, mic=False)
    coll = sc.sample_collection
    assert not coll.neighbours and not coll.sensitivity and coll.overload_note == ''
    assert set(sdr.read_gains) == {33.8}
    assert all(s.powers_low is None for s in coll.sample_sets.values())


def test_auto_gain_off_reads_nothing_again(monkeypatch, tmpdir):
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8, auto_gain=False)
    coll = sc.sample_collection
    assert set(sdr.read_gains) == {33.8} and not coll.neighbours
    assert coll.overload                                   # clipping still reported


def test_neighbour_limit_is_reported(monkeypatch, tmpdir):
    monkeypatch.setattr(sp, 'MAX_NEIGHBOUR_SHARE', 0.0)
    monkeypatch.setattr(sp, 'NEIGHBOUR_HZ', 7e6)            # more neighbours than the minimum 8
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8)
    coll = sc.sample_collection
    assert len(coll.neighbours) == 8 and coll.neighbours_skipped > 0
    assert 'not checked' in coll.overload_note


def test_dongle_error_in_neighbour_pass_sets_gain_back(monkeypatch, tmpdir):
    state = {}

    def when(n, freq):
        # the data read of the first neighbour tile and everything after it
        return bool(state.get('armed')) and n > state['armed']
    sc0, sdr0, _ = _scan(monkeypatch, tmpdir, 33.8)
    reads_main = len(sdr0.reads) - 2 * len(sc0.sample_collection.neighbours)
    state['armed'] = reads_main + 1
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8,
                        faults=[sim.Fault('read_samples', 'raise', when)])
    assert sdr.gains_set[-1] == 33.8
    assert not sc.sample_collection.neighbours or len(sc.sample_collection.neighbours) < \
        len(sc0.sample_collection.neighbours)
    assert sc.spectrum.sample_data.size > 0                # scan kept


def test_rebuild_gives_the_same_points_as_tile_by_tile(monkeypatch, tmpdir):
    sc, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 500, 520, gain=29.7,
                               sdr_kwargs={'air': sim.Air(noise=0.01).tone(510.3e6, 0.05)})
    # the scan was cropped: rebuild from the tiles of the requested range only
    sd = sc.spectrum.sample_data
    f0 = np.asarray(sd['frequency']).copy()
    sc.rebuild_spectrum()
    sc.crop_spectrum()
    sd2 = sc.spectrum.sample_data
    assert np.array_equal(np.asarray(sd2['frequency']), f0)
    assert np.allclose(np.asarray(sd2['dbFS']), np.asarray(sd['dbFS']), atol=1e-9)
    # one merge of all points = tile by tile with per-point weights
    coll = sc.sample_collection
    tiles = [sc._tile_kwargs(coll.sample_sets[k]) for k in sorted(coll.sample_sets)]
    rng = np.random.RandomState(3)
    seq = SampleArray()
    for t in tiles:
        t['weight'] = np.where(rng.rand(np.size(t['magnitude'])) < 0.3, 0.01, 1.0)
        kw = dict(t)
        w = kw.pop('weight')
        seq.insert_sorted(SampleArray.create(**kw), w)
    sc.spectrum.rebuild(tiles)
    one = sc.spectrum.sample_data
    assert np.array_equal(np.asarray(one['frequency']), np.asarray(seq['frequency']))
    assert np.allclose(np.asarray(one['magnitude']), np.asarray(seq['magnitude']), rtol=1e-9)
    assert np.allclose(one._get_counts(), seq._get_counts())
