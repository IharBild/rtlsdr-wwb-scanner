import ctypes

from wwb_scanner import scan_dll
from wwb_scanner.scanner import sample_processing as sp

SV4_SO = '/home/claude/work/dll/out-sv4/librtlsdr_sv4.so'


class FakeFn(object):
    def __init__(self, fn):
        self.fn = fn
        self.restype = self.argtypes = None
    def __call__(self, *a):
        return self.fn(*a)


class FakeLib(object):
    def __init__(self, lock=1, late=0, unlocks=0):
        def pll(dev, late_p, unl_p):
            late_p._obj.value = late
            unl_p._obj.value = unlocks
            return lock
        self.rtlsdr_get_tuner_pll_lock = FakeFn(pll)
        self.rtlsdr_get_build_info = FakeFn(lambda: b'rtl-sdr-blog aed0ea1 + scanner-v4')


class FakeSdr(object):
    dev_p = 12345
    device_opened = True
    def __init__(self):
        self.tuned = []
    def set_center_freq(self, f):
        self.tuned.append(f)


def use(monkeypatch, lib):
    monkeypatch.setitem(scan_dll._cache, 'lib', lib)


def _tile():
    s = sp.SampleSet.__new__(sp.SampleSet)
    s.center_frequency = 600e6
    s.collection = None
    return s


def test_stock_library_changes_nothing(monkeypatch):
    use(monkeypatch, None)
    assert scan_dll.configure(FakeSdr()) is False
    assert scan_dll.tuner_locked(FakeSdr()) is None
    sdr = FakeSdr()
    assert _tile().tune(sdr, 600e6) is True and len(sdr.tuned) == 1


def test_scanner_build_is_reported(monkeypatch, capsys):
    use(monkeypatch, FakeLib())
    assert scan_dll.configure(FakeSdr()) is True
    assert 'scanner-v4' in capsys.readouterr().out


def test_unlocked_pll_retunes_once_then_skips_tile(monkeypatch):
    use(monkeypatch, FakeLib(lock=0))
    sdr = FakeSdr()
    assert _tile().tune(sdr, 600e6) is False
    assert len(sdr.tuned) == 2


def test_locked_pll_tunes_once(monkeypatch):
    use(monkeypatch, FakeLib(lock=1))
    sdr = FakeSdr()
    assert _tile().tune(sdr, 600e6) is True and len(sdr.tuned) == 1


def test_pll_stats(monkeypatch):
    use(monkeypatch, FakeLib(lock=1, late=3, unlocks=1))
    assert scan_dll.pll_stats(FakeSdr()) == (3, 1)


def test_closed_device_is_not_queried(monkeypatch):
    use(monkeypatch, FakeLib(lock=0))
    sdr = FakeSdr()
    sdr.device_opened = False
    assert scan_dll.tuner_locked(sdr) is None


def test_real_scanner_v4_library_on_linux():
    import os, pytest
    if not os.path.exists(SV4_SO):
        pytest.skip('scanner-v4 .so not built')
    lib = ctypes.CDLL(SV4_SO)
    lib.rtlsdr_get_build_info.restype = ctypes.c_char_p
    assert b'scanner-v4' in lib.rtlsdr_get_build_info()
    f = lib.rtlsdr_get_tuner_pll_lock
    f.restype = ctypes.c_int
    f.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    assert f(None, None, None) == -1
