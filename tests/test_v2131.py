"""V21.3.1: own Welch PSD (scipy 1.16+ welch loops over segments in Python:
one tile took 88 ms instead of 11-14 ms on the PC with Python 3.13, 15.11.2026)."""
import numpy as np
import pytest
from scipy.signal import welch as scipy_welch, get_window

from wwb_scanner.scanner import psd

FS = 2.4e6


def _ref(x, win, n):
    return scipy_welch(x, fs=FS, window=win, nperseg=n, detrend=False,
                       scaling='density', return_onesided=False)


@pytest.mark.parametrize('n', [96, 128, 256, 1024])
@pytest.mark.parametrize('size', [163840, 163840 + 77, 3 * 8192 + 5])
@pytest.mark.parametrize('complex_input', [True, False])
@pytest.mark.parametrize('wname', ['hann', 'blackmanharris', 'flattop', 'boxcar'])
def test_same_as_scipy_welch(n, size, complex_input, wname):
    rng = np.random.default_rng(n + size)
    x = rng.standard_normal(size)
    if complex_input:
        x = x + 1j * rng.standard_normal(size)
    win = get_window(wname, n)
    f1, p1 = _ref(x, win, n)
    f2, p2 = psd.welch(x, FS, win, n)
    assert np.array_equal(f1, f2)
    assert np.allclose(p2, p1, rtol=1e-12, atol=0)


def test_complex64_input_is_computed_in_double():
    rng = np.random.default_rng(3)
    x = (rng.standard_normal(163840) + 1j * rng.standard_normal(163840)).astype(np.complex64)
    win = get_window('hann', 128)
    _, p = psd.welch(x, FS, win, 128)
    _, ref = _ref(x.astype(np.complex128), win, 128)
    assert p.dtype == np.float64 and np.allclose(p, ref, rtol=1e-12)


def test_one_batched_fft_per_tile(monkeypatch):
    """The whole point: no FFT call per segment (2559 segments here)."""
    calls = []
    real_fft = np.fft.fft
    monkeypatch.setattr(psd.np.fft, 'fft', lambda a, *k, **kw: calls.append(np.shape(a)) or real_fft(a, *k, **kw))
    x = np.ones(163840, complex)
    psd.welch(x, FS, get_window('hann', 128), 128)
    assert len(calls) == 1 and calls[0] == (2559, 128), calls


def test_blocks_give_the_same_result():
    rng = np.random.default_rng(5)
    x = rng.standard_normal(50000) + 1j * rng.standard_normal(50000)
    win = get_window('hann', 128)
    _, a = psd.welch(x, FS, win, 128)
    _, b = psd.welch(x, FS, win, 128, max_bytes=128 * 16 * 7)        # 7 segments per block
    assert np.allclose(a, b, rtol=1e-13)


def test_bad_arguments():
    with pytest.raises(ValueError):
        psd.welch(np.zeros(1000), FS, np.ones(64), 128)
    with pytest.raises(ValueError):
        psd.welch(np.zeros(100), FS, np.ones(128), 128)


def test_tile_psd_and_expected_freqs_unchanged():
    """The scanner's tile PSD and its frequency plan equal the scipy versions
    used up to V21.3."""
    from wwb_scanner.scanner import sample_processing as sp

    class Cfg(object):
        window_type = 'hann'
        tile_keep_ratio = None
        tile_center_gap_hz = None

    class Sc(object):
        sample_rate = FS
        sampling_config = Cfg()
        window_size = 128

    ss = sp.SampleSet.__new__(sp.SampleSet)
    ss.scanner = Sc()
    ss.center_frequency = 600e6
    rng = np.random.default_rng(9)
    x = rng.standard_normal(163840) + 1j * rng.standard_normal(163840)
    freqs, P, keep = ss.tile_psd(x)
    f0, p0 = _ref(x, sp.make_window('hann', 128), 128)
    f0, p0 = sp.sort_psd(f0, p0)
    assert np.array_equal(freqs, np.around(f0)) and np.allclose(P, p0, rtol=1e-12)
    assert np.array_equal(keep, ss.good_bins_mask(np.around(f0), FS))

    Sc.samples_per_sweep, Sc.sweeps_per_scan = 8192, 20
    fz, _ = scipy_welch(np.zeros(163840), fs=FS, nperseg=128, return_onesided=False)
    fz = np.around(sp.sort_psd(fz, fz)[0])
    old = (fz[ss.good_bins_mask(fz, FS)] + 600e6) / 1e6
    assert np.array_equal(ss.calc_expected_freqs(), old)


def test_gain_calibration_psd_unchanged():
    from wwb_scanner.scanner import gain_cal
    rng = np.random.default_rng(11)
    x = rng.standard_normal(65536) + 1j * rng.standard_normal(65536)
    f, p = scipy_welch(x, fs=FS, window='hann', nperseg=gain_cal.NPERSEG,
                       return_onesided=False, detrend=False)
    keep = (np.abs(f) >= gain_cal.KEEP_HZ[0]) & (np.abs(f) <= gain_cal.KEEP_HZ[1])
    ref = p[keep][np.argsort(f[keep])]
    assert np.allclose(gain_cal.tile_psd(x, FS), ref, rtol=1e-12)


def test_version():
    import os, re
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'setup.cfg')
    if not os.path.isfile(p):
        pytest.skip('repository layout only')
    assert re.search(r'^version = (21\.(3\.[1-9]|[4-9]\.\d+)|2[2-9]\.\d+\.\d+)$', open(p).read(), re.M)


def test_qa_hw_e2_accepts_pyrtlsdr_04_with_v7(monkeypatch):
    """HW.E2 said 'keep pyrtlsdr<0.4' on Python 3.13 with the v7 DLL (15.11.2026)."""
    import importlib.util, os, sys, types
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'tools', 'qa_hw.py')
    if not os.path.isfile(path):
        path = os.path.join(root, 'qa_hw.py')
    spec = importlib.util.spec_from_file_location('qa_hw_v2131', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    lib = types.SimpleNamespace(rtlsdr_set_dithering=object())
    pkg = types.ModuleType('rtlsdr')
    sub = types.ModuleType('rtlsdr.librtlsdr')
    sub.librtlsdr = lib
    monkeypatch.setitem(sys.modules, 'rtlsdr', pkg)
    monkeypatch.setitem(sys.modules, 'rtlsdr.librtlsdr', sub)
    assert m.dll_has('rtlsdr_set_dithering')
    del lib.rtlsdr_set_dithering
    assert not m.dll_has('rtlsdr_set_dithering')
