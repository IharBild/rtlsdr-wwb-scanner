"""Merging of overlapping scan tiles and cropping (plan steps A3, A4, A7)."""
import sys
import types

import numpy as np
import pytest

from wwb_scanner.scan_objects import Spectrum, SampleArray
from wwb_scanner.utils import dbmath


def mag_from_db(db):
    return 10 ** (np.asarray(db, dtype=np.float64) / 10.)


def test_three_tiles_are_averaged_with_equal_weights():
    freqs = np.array([470.0, 470.025])
    mags = [np.array([1., 2.]), np.array([3., 4.]), np.array([8., 9.])]
    a = SampleArray()
    for m in mags:
        a.insert_sorted(SampleArray.create(frequency=freqs, magnitude=m))
    assert a.size == 2
    # a pairwise running mean would give 5.0 for the first point
    assert np.allclose(a.magnitude, [4., 5.])
    assert np.allclose(a.dbFS, 10 * np.log10([4., 5.]))


def test_overlap_is_averaged_as_power_not_as_db():
    freqs = np.array([470.0])
    a = SampleArray.create(frequency=freqs, magnitude=mag_from_db([-100.]))
    a.insert_sorted(SampleArray.create(frequency=freqs, magnitude=mag_from_db([-160.])))
    assert a.size == 1
    # the plain dB average would be -130 dB
    assert a.dbFS[0] == pytest.approx(-103.0, abs=0.05)


def test_frequencies_equal_in_whole_hz_are_merged():
    base = np.arange(470., 470.5, 0.025)
    a = SampleArray.create(frequency=base, magnitude=np.full(base.size, 1e-6))
    b = SampleArray.create(frequency=base + 1e-10, magnitude=np.full(base.size, 1e-6))
    a.insert_sorted(b)
    assert a.size == base.size


def test_duplicates_inside_one_set_are_merged():
    freqs = np.array([470.0, 470.0, 470.025])
    s = Spectrum()
    s.add_sample_set(frequency=freqs, magnitude=np.array([1., 3., 5.]))
    assert s.sample_data.size == 2
    assert np.allclose(s.sample_data['magnitude'], [2., 5.])


def test_crop_keeps_range_inclusive_and_samples_in_sync():
    freqs = np.around(np.arange(468.8, 471.2, 0.025), 3)
    s = Spectrum()
    s.add_sample_set(frequency=freqs, magnitude=np.linspace(1e-6, 1e-3, freqs.size))
    s.crop(470.0, 471.0)
    f = s.sample_data['frequency']
    assert f[0] == 470.0 and f[-1] == 471.0
    assert f.size == 41
    assert sorted(s.samples.keys()) == list(f)


def fake_rtlsdr():
    try:
        import rtlsdr  # noqa: F401
    except Exception:
        stub = types.ModuleType('rtlsdr')
        stub.RtlSdr = object
        sys.modules['rtlsdr'] = stub


def test_scan_centre_frequencies_are_whole_hz_and_unique(tmp_db_store):
    fake_rtlsdr()
    from wwb_scanner.scanner.main import ScannerBase

    class FakeScanner(ScannerBase):
        step_size = 0.225   # MHz

    scanner = FakeScanner(config={'scan_range': [470., 1000.]})
    scanner.build_sample_sets()
    centres = np.array(sorted(scanner.sample_collection.sample_sets.keys()))
    assert np.array_equal(centres, np.round(centres))
    assert np.unique(centres).size == centres.size
    assert centres[0] == 470e6
    assert centres[-1] <= 1000e6 < centres[-1] + 225e3
    assert np.all(np.diff(centres) == 225e3)


def test_simulated_full_scan_has_one_point_per_25khz_step(tmp_db_store):
    """Same arithmetic as the scanner: 2.4 MHz tiles of 96 bins, step 225 kHz."""
    rs, nfft, step_hz = 2400000, 96, 225000
    start_hz, end_hz = 470000000, 1000000000
    offsets = np.around(np.fft.fftshift(np.fft.fftfreq(nfft, 1. / rs)))
    s = Spectrum()
    fc = start_hz
    rng = np.random.RandomState(1)
    while fc <= end_hz:
        freqs = (offsets + float(fc)) / 1e6
        s.add_sample_set(frequency=freqs, magnitude=rng.uniform(1e-6, 2e-6, nfft),
                         center_frequency=float(fc), force_lower_freq=True)
        fc += step_hz
    s.crop(470., 1000.)
    f = s.sample_data['frequency']
    assert f.size == (end_hz - start_hz) // 25000 + 1 == 21201
    assert f[0] == 470.0 and f[-1] == 1000.0
    assert np.all(np.diff(np.rint(f * 1e6).astype(np.int64)) == 25000)


def test_fast_tail_merge_equals_full_merge():
    """QA P2: merging only the overlapping tail gives the same result as
    merging the whole array, also when a tile goes back in frequency."""
    from wwb_scanner.scan_objects.samplearray import SampleArray
    rng = np.random.RandomState(7)
    fast, ref = SampleArray(), SampleArray()
    f0 = 470.0
    for k in range(120):
        f = np.round(f0 + np.arange(-37, 38) * 0.025, 6)
        m = 10 ** (rng.randn(f.size) - 10)
        fast.insert_sorted(SampleArray.create(frequency=f, magnitude=m))
        tile = SampleArray.create(frequency=f, magnitude=m)
        if ref.data.size:
            _full_merge(ref, tile)
        else:
            ref.insert_sorted(tile)
        f0 = round(f0 + (0.775 if k % 17 else -1.55), 6)
    assert np.array_equal(np.round(fast.data['frequency'], 6), np.round(ref.data['frequency'], 6))
    assert np.allclose(fast.data['magnitude'], ref.data['magnitude'], rtol=1e-12)
    assert np.allclose(fast._get_counts(), ref._get_counts())

def _full_merge(arr, other):
    """The original whole-array merge (reference)."""
    from wwb_scanner.utils import dbmath
    data = other.data
    everything = np.concatenate([arr.data, data])
    weights = np.concatenate([arr._get_counts(), np.ones(data.size)])
    keys = arr._freq_keys(everything['frequency'])
    uniq, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    n = uniq.size
    wsum = np.bincount(inverse, weights=weights, minlength=n)
    merged = np.bincount(inverse, minlength=n) > 1
    out = everything[first]
    mag = np.bincount(inverse, weights=weights * everything['magnitude'], minlength=n) / wsum
    out['magnitude'][merged] = mag[merged]
    out['dbFS'][merged] = dbmath.to_dB(mag[merged])
    arr.data = out
    arr._counts = wsum
