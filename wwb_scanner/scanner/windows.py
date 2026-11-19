"""V22.6: the FFT windows of the scanner, without scipy.

Three windows (decision of 19.11.2026; parameters for a 25 kHz bin):

* ``hann`` (default) - ENBW 1.50 bins, highest side lobe -31 dB, level error
  between bins up to 1.42 dB; leakage 200 kHz away -62 dB. All calibrations
  and checks of the scanner were made with it.
* ``blackmanharris`` (4 terms) - ENBW 2.00 bins, side lobes -92 dB: a weak
  carrier right next to a strong one.
* ``flattop`` - level error between bins 0.01 dB, main lobe 2.5 times as wide
  as hann: the level of a carrier (the future dBm stage), not for finding
  frequencies.

The arrays are those of ``scipy.signal.get_window(name, n)`` (periodic,
``fftbins=True``) to the last bit (tests/test_v226.py compares them), so
a scan with hann measures exactly what it measured with scipy.

Windows of older versions (hamming, nuttall, kaiser, ...) are no longer
offered; a scan or a setting that names one uses hann (``resolve``).
"""
import logging

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT = 'hann'
WINDOW_TYPES = ['hann', 'blackmanharris', 'flattop']
ALIASES = {'hanning': 'hann', 'blackman-harris': 'blackmanharris', 'flat-top': 'flattop',
           'flat_top': 'flattop'}

# coefficients as in scipy.signal.windows (general_cosine)
_COEFFS = {
    'hann': (0.5, 0.5),
    'blackmanharris': (0.35875, 0.48829, 0.14128, 0.01168),
    'flattop': (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368),
}

_warned = set()


def resolve(name):
    """The window that is used for ``name``: a known name (or alias) as it
    is, anything else ``hann`` (said once in the log and the status line)."""
    n = (name or DEFAULT)
    n = ALIASES.get(str(n).strip().lower(), str(n).strip().lower())
    if n in _COEFFS:
        return n
    if n not in _warned:
        _warned.add(n)
        logging.getLogger('wwb_scanner.status').warning(
            'The FFT window %r is no longer offered (V22.6: hann, blackmanharris, flattop): '
            'hann is used.', name)
    return DEFAULT


def _general_cosine(m, a):
    # scipy.signal.windows.general_cosine with sym=False: a symmetric window
    # of m + 1 points without its last point
    if m == 1:
        return np.ones(1)
    m1 = m + 1
    fac = np.linspace(-np.pi, np.pi, m1)
    w = np.zeros(m1)
    for k in range(len(a)):
        w += a[k] * np.cos(k * fac)
    return w[:-1]


def get_window(name, n):
    """Periodic window ``name`` of ``n`` points (float64)."""
    n = int(n)
    if n < 1:
        raise ValueError('window size must be at least 1, got %d' % n)
    return _general_cosine(n, _COEFFS[resolve(name)])
