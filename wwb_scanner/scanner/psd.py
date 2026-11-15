"""Welch power spectral density without the per-segment Python loop.

V21.3.1: scipy 1.16+ computes ``scipy.signal.welch`` through ShortTimeFFT,
which calls the FFT once per segment from Python. With our short segments
(128 bins, ~2560 segments per tile) one tile took 21.5 ms with scipy 1.18
against 2.9 ms with scipy 1.7 (Linux; 88 ms against 11-14 ms on the PC with
Python 3.13): a full scan would take about twice as long.

``welch`` here gives the same result as ``scipy.signal.welch(x, fs,
window=win, nperseg=n, noverlap=n // 2, detrend=False, scaling='density',
return_onesided=False, average='mean')`` (tested against it), with one
batched FFT over all segments.
"""
import numpy as np
from numpy.lib.stride_tricks import as_strided


def welch_freqs(nperseg, fs):
    """Bin frequencies of a two-sided Welch PSD (FFT order, as scipy)."""
    return np.fft.fftfreq(int(nperseg), 1. / fs)


def welch(x, fs, window, nperseg=None, max_bytes=64 << 20):
    """Two-sided Welch PSD, density scaling, no detrend, 50 % overlap.

    ``window``: the window array (its length is the segment length).
    Returns (freqs, Pxx) in FFT order, like scipy.signal.welch.
    Segments are processed in blocks of at most ``max_bytes`` of FFT data."""
    win = np.asarray(window)
    n = int(nperseg if nperseg is not None else win.size)
    if win.size != n:
        raise ValueError('window length %d != nperseg %d' % (win.size, n))
    x = np.ascontiguousarray(x)
    if x.ndim != 1:
        x = x.ravel()
    if x.size < n:
        raise ValueError('need at least %d samples, got %d' % (n, x.size))
    if not np.iscomplexobj(x):
        x = x.astype(np.float64, copy=False)
    elif x.dtype != np.complex128:
        x = x.astype(np.complex128)
    noverlap = n // 2
    step = n - noverlap
    nseg = (x.size - noverlap) // step
    item = x.strides[0]
    segs = as_strided(x, shape=(nseg, n), strides=(item * step, item), writeable=False)
    win = win.astype(np.float64, copy=False)
    acc = np.zeros(n, dtype=np.float64)
    block = max(1, int(max_bytes // (n * 16)))
    for a in range(0, nseg, block):
        spec = np.fft.fft(segs[a:a + block] * win, axis=1)
        acc += (spec.real * spec.real + spec.imag * spec.imag).sum(axis=0)
    scale = 1. / (fs * float((win * win).sum()))
    return welch_freqs(n, fs), acc * (scale / nseg)
