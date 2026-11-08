"""V20.2: readability of the graph next to strong mics (user's V20.1 series
08.11.2026 23:34).

- points where no tile has a real measurement show the plateau of the
  low-gain reading, flat, instead of spikes of the clipped normal reading
  (631.05 MHz +31 dB at 49.6 dB);
- "lost sensitivity" only up to the plateau of the zone (dips of DVB-T);
- images (+-fs) of strong carriers that did not clip are removed too
  (635.325 MHz +12 dB at 0 dB), real signals at that frequency are kept;
- the graph gets the bands and the image marks.

The tests fail on V20.1 (checked) and pass on V20.2, except the safety ones
(marked) which pass on both.
"""
import threading
import types

import numpy as np
import pytest

import sim
from wwb_scanner.scanner import sample_processing as sp

MIC = 632.925


def _air(mic=20., extra=()):
    a = sim.Air(noise=0.005)
    if mic:
        a.tone(MIC * 1e6, mic)
    for f, amp in extra:
        a.tone(f * 1e6, amp)
    return a


def _scan(monkeypatch, tmpdir, gain, auto_gain=True, **kw):
    sk = {'air': _air(**kw), 'gain_affects': True, 'wide_adc': sim.WideAdc()}
    return sim.run_scan(monkeypatch, tmpdir, 626, 640, gain=gain, auto_gain=auto_gain, sdr_kwargs=sk)


class _SD(object):
    def __init__(self, f, y, w):
        self.data = np.zeros(f.size, dtype=[('frequency', float), ('iq', complex), ('magnitude', float),
                                             ('dbFS', float)])
        self.data['frequency'] = f
        self.data['dbFS'] = y
        self.data['magnitude'] = 10 ** (y / 10.)
        self._w = w

    def __getitem__(self, k):
        return self.data[k]

    def _get_counts(self):
        return self._w


def test_unknown_points_show_the_plateau_not_spikes():
    s = object.__new__(sp.SampleSet)
    n = 64
    low = np.full(n, 1e-6)                    # plateau of the low gain
    normal = np.full(n, 1e-10)                # clean normal reading ...
    normal[20:30] = 1e-6 * 10 ** (-3. / 10)   # ... spoiled: 3 dB under the plateau, spiky
    normal[22] = 1e-6 * 10 ** (-1. / 10)
    merged = s.merge_readings(normal, low)
    lim = s.bin_weight < 1.
    assert lim[20:30].all() and not lim[:20].any()
    assert np.allclose(merged[20:30], low[20:30])         # flat: the plateau
    assert np.allclose(merged[:20], normal[:20])           # clean reading kept


def test_dips_of_a_multiplex_are_not_lost_sensitivity():
    f = np.round(np.arange(644.0, 656.0, 0.025), 3)
    y = np.full(f.size, -100.)
    w = np.ones(f.size)
    tv = (f > 646) & (f < 654)
    y[tv] = -78.
    dips = tv & (np.abs(f - 648.4) < 0.1)
    y[dips] = -88.                            # dip of the multiplex: +12 over floor
    w[dips] = 0.01
    lost = np.abs(f - 655.0) < 0.1            # really lost: at the plateau (-96)
    y[lost] = -96.
    w[lost] = 0.01
    sets = {}
    for k in np.arange(640e6, 662e6, 0.775e6):
        sets[float(k)] = types.SimpleNamespace(powers=np.full(64, 1e-10), powers_low=None, powers_orig=None,
                                               carrier_hz=None, carrier_db=None, low_gain=None,
                                               neighbour=False, desense_db=0.)
    k0 = min(sets, key=lambda k: abs(k - 650.125e6))
    sets[k0].powers_low = np.full(64, 1e-10)
    sets[k0].carrier_hz, sets[k0].carrier_db, sets[k0].low_gain = 650.125e6, -60., 43.9
    c = object.__new__(sp.SampleCollection)
    c.sample_sets = sets
    c.scanner = types.SimpleNamespace(sample_rate=2.4e6, config=types.SimpleNamespace(scan_range=[640., 662.]),
                                      spectrum=types.SimpleNamespace(data_update_lock=threading.RLock(),
                                                                     sample_data=_SD(f, y, w)))
    zone = c.carrier_zones()[0]
    c.zone_floors = [(zone, -97.)]            # plateau of the zone
    loss = c.sensitivity_loss()
    assert any(lo <= 655.0 <= hi for lo, hi, _, _ in loss), loss
    assert not any(lo <= 648.4 <= hi for lo, hi, _, _ in loss), loss


def test_image_of_a_carrier_that_did_not_clip_is_removed(monkeypatch, tmpdir):
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 0.0)
    coll = sc.sample_collection
    assert not coll.overload                  # nothing clipped at 0 dB
    got = [p for p, d, c in coll.images]
    assert 635.325 in got, coll.images
    f, y = sim.spectrum_arrays(sc)
    ring = (np.abs(f - 635.325) > 0.15) & (np.abs(f - 635.325) < 0.4)
    assert y[np.abs(f - 635.325) <= 0.05].max() < np.median(y[ring]) + 3.
    assert sc.spectrum.image_marks and sc.spectrum.image_marks[0]['of'] == pytest.approx(MIC, abs=0.05)


def test_real_signal_at_the_image_frequency_is_kept(monkeypatch, tmpdir):
    # safety (passes on V20.1 too): a real mic 20 dB under ours, exactly
    # +2.4 MHz away, is far above where the dongle's image would be
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 0.0, extra=[(635.325, 2.0)])
    assert 635.325 not in [p for p, d, c in sc.sample_collection.images]
    f, y = sim.spectrum_arrays(sc)
    assert y[np.abs(f - 635.325) <= 0.05].max() > -80.


def test_no_image_removal_without_auto_gain_and_spur_cleaning(monkeypatch, tmpdir):
    # safety (passes on V20.1 too): raw scans stay raw
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 0.0, auto_gain=False)
    assert sc.sample_collection.images == []


def test_graph_gets_bands_and_image_marks(monkeypatch, tmpdir):
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8)
    bands = sc.spectrum.loss_bands
    assert bands and set(bands[0]) == {'lo', 'hi', 'db', 'signal'}
    assert any(b['lo'] < MIC < b['hi'] and b['signal'] == pytest.approx(MIC, abs=0.05) for b in bands)
    assert isinstance(sc.spectrum.image_marks, list)
