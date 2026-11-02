import ctypes
import os
import sys

import pytest

from wwb_scanner import scan_dll
from wwb_scanner.scanner import sample_processing as sp

LINUX_SO = '/home/claude/work/dll/out-scan/librtlsdr_wwbscan.so'


@pytest.mark.skipif(not os.path.exists(LINUX_SO), reason='scan edition .so not built')
def test_real_library_exports_scan_api():
    lib = ctypes.CDLL(LINUX_SO)
    assert scan_dll._setup_prototypes(lib)
    assert lib.rtlsdr_wwbscan_version() == b'wwbscan-1'
    # no device: every call must fail cleanly, not crash
    assert lib.rtlsdr_get_tuner_lock(None) == -1
    assert lib.rtlsdr_set_fast_tune(None, 1) == -1
    assert lib.rtlsdr_set_read_timeout(None, 2000) == -1


def test_install_is_a_no_op_off_windows():
    if sys.platform == 'win32':
        pytest.skip('Windows')
    assert scan_dll.install() is None


class FakeLib(object):
    def __init__(self, lock=1):
        self.lock = lock
        self.calls = []
    def rtlsdr_wwbscan_version(self):
        return b'wwbscan-1'
    def rtlsdr_set_fast_tune(self, dev, on):
        self.calls.append(('fast', on)); return 0
    def rtlsdr_set_read_timeout(self, dev, ms):
        self.calls.append(('timeout', ms)); return 0
    def rtlsdr_get_tuner_lock(self, dev):
        return self.lock


class FakeSdr(object):
    dev_p = 12345
    def __init__(self):
        self.tuned = []
    def set_center_freq(self, f):
        self.tuned.append(f)


def test_configure_turns_on_fast_tune_and_timeout(monkeypatch):
    lib = FakeLib()
    monkeypatch.setattr(scan_dll, '_lib', lambda: lib)
    assert scan_dll.configure(FakeSdr())
    assert ('fast', 1) in lib.calls and ('timeout', scan_dll.READ_TIMEOUT_MS) in lib.calls


def test_stock_library_is_not_configured(monkeypatch):
    monkeypatch.setattr(scan_dll, '_lib', lambda: None)
    assert scan_dll.configure(FakeSdr()) is False
    assert scan_dll.tuner_locked(FakeSdr()) is None


def _tile():
    class S(object):
        center_frequency = 600e6
        collection = None
    s = sp.SampleSet.__new__(sp.SampleSet)
    s.center_frequency = 600e6
    s.collection = None
    return s


def test_unlocked_pll_skips_tile_after_retry(monkeypatch):
    monkeypatch.setattr(scan_dll, '_lib', lambda: FakeLib(lock=0))
    sdr = FakeSdr()
    assert _tile().tune(sdr, 600e6) is False
    assert len(sdr.tuned) == 2


def test_locked_pll_and_stock_library_tune_normally(monkeypatch):
    monkeypatch.setattr(scan_dll, '_lib', lambda: FakeLib(lock=1))
    sdr = FakeSdr()
    assert _tile().tune(sdr, 600e6) is True and len(sdr.tuned) == 1
    monkeypatch.setattr(scan_dll, '_lib', lambda: None)
    assert _tile().tune(FakeSdr(), 600e6) is True
