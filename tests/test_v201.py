"""V20.1: findings of the user's 49.6 dB log (08.11.2026 22:00).

- some clipped tiles stopped at -6 dB: output no longer clipped, internal ADC
  still did -> products +30...+46 dB (632.300 / 631.65 / 631.05);
- the carrier named in the status line was such a product (632.300);
- only 13 of 20 tiles next to the mic were read again (25 % of 55);
- a DVB-T multiplex clipping at 49.6 dB was reported as "22 dB less sensitive";
- the dongle was deaf 2.5-9 MHz from the mic (6-9 dB below the floor);
- images of the mic at +-fs (630.525 / 635.325) and +28.8 MHz (661.725).

Every test here fails on V20 (checked) and passes on V20.1.
"""
import types

import numpy as np
import pytest

import sim
from wwb_scanner.scanner import sample_processing as sp

MIC = 632.925
TILE_STOPS_EARLY = 632.2e6          # tile grid 626 + k * 0.775 MHz


def _air(mic=True, tv=0.0):
    a = sim.Air(noise=0.005)
    if mic:
        a.tone(MIC * 1e6, 20.)
    if tv:
        a.band(646e6, 654e6, tv)
    return a


def _scan(monkeypatch, tmpdir, gain, start=626, end=640, mic=True, tv=0.0, auto_gain=True, clean=None):
    kw = {'air': _air(mic, tv), 'gain_affects': True, 'wide_adc': sim.WideAdc()}
    return sim.run_scan(monkeypatch, tmpdir, start, end, gain=gain, auto_gain=auto_gain, sdr_kwargs=kw,
                        clean=clean)


def _stop_early(monkeypatch, fc=TILE_STOPS_EARLY, at_gain=43.9):
    """One clipped tile looks clean after the first 6 dB step (as on the
    user's dongle: its 8 output bits stop clipping, the internal ADC not)."""
    orig = sp.clip_ratio

    def fake(iq):
        sdr = sim.FakeRtlSdr.instances[-1] if sim.FakeRtlSdr.instances else None
        if sdr is not None and abs(sdr._gain - at_gain) < 0.05 and abs(sdr._fc - fc) < 1e3:
            return 0.0
        return orig(iq)
    monkeypatch.setattr(sp, 'clip_ratio', fake)


def _coll(sets, fs=2.4e6):
    """A SampleCollection with only what the zone methods need."""
    c = object.__new__(sp.SampleCollection)
    c.sample_sets = sets
    c.scanner = types.SimpleNamespace(sample_rate=fs, spectrum=None)
    c.desensed = []
    c.images = []
    return c


def _tile(**kw):
    d = dict(powers=None, powers_low=None, powers_normal=None, powers_orig=None, carrier_hz=None,
             carrier_db=None, low_gain=None, neighbour=False, desense_db=0., signal_mask=None,
             bin_weight=None, product_bins=None, frequencies=None)
    d.update(kw)
    return types.SimpleNamespace(**d)


# ------------------------------------------------------------- zone gain

def test_clipped_tile_that_stopped_early_is_read_with_the_gain_of_its_zone(monkeypatch, tmpdir):
    _stop_early(monkeypatch)
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 49.6)
    coll = sc.sample_collection
    s = coll.sample_sets[TILE_STOPS_EARLY]
    others = [x.low_gain for k, x in coll.sample_sets.items()
              if x.low_gain is not None and not x.neighbour and k != TILE_STOPS_EARLY]
    assert others and s.low_gain == min(others) == 0.0, (s.low_gain, others)
    assert TILE_STOPS_EARLY in coll.zone_rereads
    # no false peak from the half-clipped reading: points 0.3-1.2 MHz from
    # the mic not above the rest of the zone by more than the plateau
    f, y = sim.spectrum_arrays(sc)
    truth, _, _ = _scan(monkeypatch, tmpdir, 49.6, mic=False)
    ft, yt = sim.spectrum_arrays(truth)
    near = (np.abs(f - MIC) > 0.3) & (np.abs(f - MIC) < 1.2)
    assert np.max(y[near] - yt[near]) < 40., np.max(y[near] - yt[near])
    assert sdr.gains_set[-1] == 49.6


def test_carrier_of_a_zone_is_its_strongest_level():
    sets = {
        631.425e6: _tile(carrier_hz=632.300e6, carrier_db=-40., low_gain=43.9),
        632.200e6: _tile(carrier_hz=632.925e6, carrier_db=-3., low_gain=0.0),
        632.975e6: _tile(carrier_hz=632.925e6, carrier_db=-2.5, low_gain=0.0),
        646.000e6: _tile(carrier_hz=646.225e6, carrier_db=-60., low_gain=43.9),
    }
    zones = _coll(sets).carrier_zones()
    assert len(zones) == 2
    assert zones[0]['carrier'] == 632.925e6 and zones[0]['gain'] == 0.0
    assert zones[1]['carrier'] == 646.225e6 and zones[1]['gain'] == 43.9


def test_status_names_the_mic_not_a_product():
    # user's log: "628.9-636.2 MHz up to 14 dB less sensitive (signal 632.300)"
    sets = {626e6 + i * 0.775e6: _tile(powers=np.full(64, 1e-10)) for i in range(30)}
    a, b = sets[631.425e6], sets[632.975e6]
    a.carrier_hz, a.carrier_db, a.low_gain, a.powers_low = 632.300e6, -40., 43.9, np.full(64, 1e-10)
    b.carrier_hz, b.carrier_db, b.low_gain, b.powers_low = 632.925e6, -3., 0.0, np.full(64, 1e-10)
    a.desense_db = 10.
    c = _coll(sets)
    c.scanner.config = types.SimpleNamespace(scan_range=[626., 650.])
    labels = [cc for _, _, _, cc in c.sensitivity_loss()]
    assert labels == [pytest.approx(MIC)], labels


def test_at_least_30_tiles_read_again(monkeypatch, tmpdir):
    assert sp.MIN_NEIGHBOUR_TILES >= 30
    monkeypatch.setattr(sp, 'NEIGHBOUR_HZ', 8e6)        # ~20 tiles of 55, the user's case
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8, start=626, end=668)
    coll = sc.sample_collection
    assert len(coll.sample_sets) >= 50
    assert coll.neighbours_skipped == 0 and 'not checked' not in coll.overload_note
    assert len(coll.neighbours) > 13


# ------------------------------------------------------------- products

def test_signal_of_one_tile_only_is_taken_from_the_other_tile():
    f_a = np.array([1., 2., 3., 4.]) * 0.025 + 632.0
    f_b = np.array([3., 4., 5., 6.]) * 0.025 + 632.0
    low_a = np.array([1e-10, 1e-10, 1e-6, 1e-7])     # 632.075: product (+40 dB), 632.1: real (+30)
    low_b = np.array([1e-10, 1e-7, 1e-10, 1e-10])    # 632.075 quiet here, 632.1 real here too
    a = _tile(frequencies=f_a, powers_low=low_a, powers=low_a.copy(), signal_mask=np.array([0, 0, 1, 1], bool),
              bin_weight=np.ones(4))
    b = _tile(frequencies=f_b, powers_low=low_b, powers=low_b.copy(), signal_mask=np.array([0, 1, 0, 0], bool),
              bin_weight=np.ones(4))
    c = _coll({1: a, 2: b})
    c._mark_tile_products([1, 2])
    assert a.powers[2] == b.powers[0] and a.bin_weight[2] == sp.PRODUCT_WEIGHT
    assert a.powers[3] == 1e-7 and a.bin_weight[3] == 1.0          # real signal kept
    assert c.product_points == 1


def test_wide_signal_is_not_lost_sensitivity():
    # zone of a DVB-T multiplex: tile full of the signal has no floor of its
    # own; with the floor of the zone the multiplex is a signal
    pl = np.full(64, 1e-7)
    pl[:4] = 1e-10
    merged, sig, low_floor = sp.merge_low_gain(np.full(64, 1e-8), pl)
    assert not sig[10]                                  # V20: no signal at all
    merged, sig, low_floor = sp.merge_low_gain(np.full(64, 1e-8), pl, low_floor_db=-100.)
    assert sig[10] and merged[10] == pl[10]             # V20.1: the multiplex level, weight 1
    assert not sp.low_limited_bins(np.full(64, 1e-8), pl, sig)[10]


# ------------------------------------------------------------- deafness

def test_deaf_tiles_near_the_carrier_are_raised():
    rng = np.random.RandomState(1)
    sets = {}
    for i in range(30):
        k = 626e6 + i * 0.775e6
        p = 1e-10 * (1 + 0.1 * rng.rand(64))
        sets[k] = _tile(powers=p)
    sets[632.975e6].carrier_hz = MIC * 1e6
    sets[632.975e6].carrier_db = -3.
    sets[632.975e6].low_gain = 0.0
    sets[632.975e6].powers_low = sets[632.975e6].powers.copy()
    deaf = [k for k in sets if 2.5e6 < k - MIC * 1e6 < 9e6]
    for k in deaf:
        sets[k].powers = sets[k].powers * 10 ** (-8. / 10)      # 8 dB below the floor
    c = _coll(sets)
    zones = c.carrier_zones()
    c._raise_deaf_tiles(zones, c.far_tiles())
    assert deaf and sorted(c.desensed) == sorted(deaf)
    for k in deaf:
        assert sets[k].desense_db == pytest.approx(8., abs=0.5)
        assert np.median(sp.to_db(sets[k].powers)) == pytest.approx(-100., abs=0.6)
    ok = [k for k in sets if k not in deaf and sets[k].powers_low is None]
    assert all(sets[k].desense_db == 0. for k in ok)


def test_deaf_band_is_named_in_the_status(monkeypatch, tmpdir):
    sets = {}
    for i in range(30):
        sets[626e6 + i * 0.775e6] = _tile(powers=np.full(64, 1e-10))
    sets[632.975e6].carrier_hz = MIC * 1e6
    sets[632.975e6].carrier_db = -3.
    sets[632.975e6].low_gain = 0.0
    sets[632.975e6].powers_low = np.full(64, 1e-10)
    sets[637.625e6].powers = np.full(64, 1e-10 * 10 ** (-7. / 10))
    c = _coll(sets)
    c.scanner.config = types.SimpleNamespace(scan_range=[626., 650.])
    c._raise_deaf_tiles(c.carrier_zones(), c.far_tiles())
    loss = c.sensitivity_loss()
    assert any(lo < 637.625 < hi and l == pytest.approx(7., abs=0.5) and cc == pytest.approx(MIC)
               for lo, hi, l, cc in loss), loss


# ------------------------------------------------------------- images

def test_images_of_the_mic_at_plus_minus_fs_are_replaced(monkeypatch, tmpdir):
    # V20.9: only with "Hide images of strong signals" (off: test_v209)
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 20.7, clean={'clean_images': True})
    coll = sc.sample_collection
    got = dict((p, d) for p, d, c in coll.images)
    assert 635.325 in got and 630.525 in got, coll.images
    f, y = sim.spectrum_arrays(sc)
    for p in (635.325, 630.525):
        ring = (np.abs(f - p) > 0.15) & (np.abs(f - p) < 0.4)
        assert y[np.abs(f - p) <= 0.05].max() < np.median(y[ring]) + 3.
    assert 'images of the strong signal removed at' in coll.overload_note
    # the mic itself is untouched
    assert y[np.abs(f - MIC) <= 0.05].max() > -35.


def test_image_inside_a_wide_signal_is_left_alone():
    sd_f = np.arange(640.0, 660.0, 0.025)
    y = np.full(sd_f.size, -100.)
    tv = (sd_f > 646) & (sd_f < 654)
    y[tv] = -80. + 4. * np.sin(sd_f[tv] * 40)            # ripple of a multiplex
    data = np.zeros(sd_f.size, dtype=[('frequency', float), ('iq', complex), ('magnitude', float),
                                      ('dbFS', float)])
    data['frequency'] = sd_f
    data['dbFS'] = y
    data['magnitude'] = 10 ** (y / 10)
    import threading
    spectrum = types.SimpleNamespace(data_update_lock=threading.RLock(), set_data_updated=lambda: None)

    class SD(object):
        def __init__(self, d):
            self.data = d

        def __getitem__(self, key):
            return self.data[key]
    spectrum.sample_data = SD(data)
    sets = {640e6 + i * 0.775e6: _tile(powers=np.full(64, 1e-10)) for i in range(4)}
    sets[647.0e6] = _tile(powers=np.full(64, 1e-8), powers_low=np.full(64, 1e-8),
                          carrier_hz=646.225e6, carrier_db=-60., low_gain=43.9)
    c = _coll(sets)
    c.scanner.spectrum = spectrum
    before = data['dbFS'].copy()
    c._replace_images(c.carrier_zones())
    assert np.array_equal(data['dbFS'], before) and c.images == []
