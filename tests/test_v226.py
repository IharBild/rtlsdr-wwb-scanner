"""V22.6: three FFT windows of the scanner's own (wwb_scanner/scanner/windows.py),
no scipy in the program."""
import logging
import os
import re
import subprocess
import sys

import numpy as np
import pytest

from wwb_scanner.scanner import windows
from wwb_scanner.scanner.sample_processing import WINDOW_TYPES, make_window

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize('name', ['hann', 'blackmanharris', 'flattop'])
@pytest.mark.parametrize('n', [1, 2, 3, 7, 64, 96, 128, 1000, 2048])
def test_same_as_scipy_to_the_bit(name, n):
    """A scan with hann measures exactly what it measured with scipy."""
    sig = pytest.importorskip('scipy.signal')
    a = sig.get_window(name, n)
    b = windows.get_window(name, n)
    assert a.dtype == b.dtype and a.shape == b.shape
    assert np.array_equal(a, b)


def test_three_windows_offered():
    assert WINDOW_TYPES == ['hann', 'blackmanharris', 'flattop']
    import wwb_scanner          # the installed package (a qa-kit has no wwb_scanner folder)
    pkg = os.path.dirname(os.path.abspath(wwb_scanner.__file__))
    qml = open(os.path.join(pkg, 'ui', 'pyside', 'qml', 'ScanControlsDialog.qml'),
               encoding='utf-8').read()
    m = re.search(r"model:\s*\[([^\]]*)\]\s*\n\s*onTextValueChanged: \{ windowCombo", qml)
    assert m, 'window list not found'
    assert re.findall(r"'([^']+)'", m.group(1)) == WINDOW_TYPES


def test_old_windows_become_hann(caplog):
    windows._warned.discard('hamming')
    with caplog.at_level(logging.WARNING, logger='wwb_scanner.status'):
        assert windows.resolve('hamming') == 'hann'
        assert windows.resolve('hamming') == 'hann'
    assert caplog.text.count("'hamming' is no longer offered") == 1
    assert np.array_equal(make_window('hamming', 96), windows.get_window('hann', 96))
    assert windows.resolve('Hanning') == 'hann' and windows.resolve(None) == 'hann'
    assert windows.resolve('Blackman-Harris') == 'blackmanharris'
    assert windows.resolve('flat-top') == 'flattop'
    with pytest.raises(ValueError):
        windows.get_window('hann', 0)


def test_window_parameters():
    """The numbers given to the user (25 kHz bin): ENBW and level error between bins."""
    def enbw(w):
        return w.size * np.sum(w ** 2) / np.sum(w) ** 2

    def scallop(w):
        n = w.size
        h = np.abs(np.sum(w * np.exp(-1j * np.pi * np.arange(n) / n)))
        return -20 * np.log10(h / np.sum(w))
    N = 4096
    assert enbw(windows.get_window('hann', N)) == pytest.approx(1.50, abs=0.01)
    assert enbw(windows.get_window('blackmanharris', N)) == pytest.approx(2.00, abs=0.01)
    assert enbw(windows.get_window('flattop', N)) == pytest.approx(3.77, abs=0.01)
    assert scallop(windows.get_window('hann', N)) == pytest.approx(1.42, abs=0.01)
    assert scallop(windows.get_window('flattop', N)) < 0.02


def test_the_program_runs_without_scipy(tmp_path):
    """No module of the scanner imports scipy (the portable build is ~70 MB
    smaller without it)."""
    code = r'''
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name == 'scipy' or name.startswith('scipy.'):
            raise ImportError('scipy blocked by the test')
sys.meta_path.insert(0, Block())
import numpy as np
from wwb_scanner.scanner import main, sample_processing, gain_cal, cleanup, psd, windows
from wwb_scanner import service
from wwb_scanner.file_handlers import exporters, importers
w = sample_processing.make_window('hann', 96)
f, p = psd.welch(np.ones(4096, complex), 2.4e6, w, 96)
assert 'scipy' not in sys.modules
print('ok')
'''
    env = dict(os.environ, WWB_SCANNER_DATA=str(tmp_path))
    r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith('ok'), r.stderr[-2000:]
