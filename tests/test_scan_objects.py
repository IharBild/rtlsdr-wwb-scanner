"""SampleArray and Spectrum (V22.0: points have frequency, magnitude, dbFS;
no iq field and no Sample object per point)."""
import numpy as np


def _power(random_samples, **kw):
    freqs, sig, ff = random_samples(**kw)
    return freqs, np.abs(ff)


def test_sample_array(random_samples):
    from wwb_scanner.scan_objects import SampleArray

    freqs, p = _power(random_samples, rs=2.048e6, fc=600e6)
    a = SampleArray()
    b = SampleArray.create(frequency=freqs, magnitude=p)
    a.insert_sorted(b)
    assert np.array_equal(a.data, b.data)
    assert a.dtype.names == ('frequency', 'magnitude', 'dbFS')
    assert a.dtype.itemsize == 24
    assert np.array_equal(a.frequency, freqs)
    assert np.array_equal(a.magnitude, p)
    assert np.array_equal(a.dbFS, 10 * np.log10(p))
    assert np.array_equal(a.dbFS, a['dbFS'])

    freqs2, p2 = _power(random_samples, rs=2.048e6, fc=900e6)
    a.set_fields(frequency=freqs2, magnitude=p2)
    c = SampleArray.create(frequency=freqs2, magnitude=p2)
    ix = np.flatnonzero(np.isin(a.frequency, c.frequency))
    assert np.array_equal(a.data[ix], c.data)


def test_dbfs_only_gives_magnitude():
    from wwb_scanner.scan_objects import SampleArray
    a = SampleArray.create(frequency=np.array([470., 470.025]), dbFS=np.array([-90., -60.]))
    assert np.allclose(a.magnitude, [1e-9, 1e-6])


def test_old_points_with_iq_are_converted():
    """Arrays saved before V22.0 (history npz, npz exports) have an iq field."""
    from wwb_scanner.scan_objects import SampleArray, Spectrum
    old_dtype = np.dtype([('frequency', np.float64), ('iq', np.complex128),
                          ('magnitude', np.float64), ('dbFS', np.float64)])
    old = np.zeros(3, dtype=old_dtype)
    old['frequency'] = [470.05, 470.0, 470.025]
    old['magnitude'] = [1e-9, 1e-8, 1e-7]
    old['dbFS'] = [-90., -80., -70.]
    a = SampleArray(old)
    assert a.data.dtype == SampleArray.dtype
    assert list(a.frequency) == [470.0, 470.025, 470.05]
    assert list(a.dbFS) == [-80., -70., -90.]
    s = Spectrum()
    s.add_sample_set(data=old)
    assert s.sample_data.data.dtype == SampleArray.dtype and s.sample_data.size == 3


def test_spectrum_points(random_samples):
    from wwb_scanner.scan_objects import Spectrum
    freqs, p = _power(random_samples, rs=2.048e6, fc=600e6)
    spectrum = Spectrum()
    spectrum.add_sample_set(frequency=freqs, magnitude=p)
    assert np.array_equal(spectrum.sample_data['frequency'], freqs)
    assert np.array_equal(spectrum.sample_data['magnitude'], p)
    assert np.array_equal(spectrum.sample_data['dbFS'], 10 * np.log10(p))
    assert not hasattr(spectrum, 'samples')


def test_add_sample_set(random_samples):
    """Overlapping tiles: shared points are averaged as linear power."""
    from wwb_scanner.scan_objects import Spectrum

    def build(fc):
        return _power(random_samples, n=256, rs=2.000e6, fc=fc)

    spectrum = Spectrum()
    f1, p1 = build(600e6)
    spectrum.add_sample_set(frequency=f1, magnitude=p1, center_frequency=600e6, force_lower_freq=True)
    f2, p2 = build(601e6)
    assert np.any(np.isin(f1, f2))
    spectrum.add_sample_set(frequency=f2, magnitude=p2, center_frequency=601e6, force_lower_freq=True)
    sd = spectrum.sample_data
    assert np.unique(sd['frequency']).size == sd['frequency'].size
    assert sd.size == np.union1d(f1, f2).size
    for f in np.intersect1d(f1, f2):
        i = np.searchsorted(sd['frequency'], f)
        want = (p1[np.searchsorted(f1, f)] + p2[np.searchsorted(f2, f)]) / 2.
        assert np.isclose(sd['magnitude'][i], want)
        assert np.isclose(sd['dbFS'][i], 10 * np.log10(want))
    for f in np.setdiff1d(f2, f1):
        i = np.searchsorted(sd['frequency'], f)
        assert sd['magnitude'][i] == p2[np.searchsorted(f2, f)]
