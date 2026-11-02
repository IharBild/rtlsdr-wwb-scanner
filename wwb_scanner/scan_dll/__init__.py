"""Extra functions of the scanner build of rtlsdr.dll ("scanner-v4").

The scanner-v4 build (RTL-SDR Blog aed0ea1 + osmocom fast retune + scanner
patches) is installed by replacing the rtlsdr.dll that pyrtlsdr loads (see
README_RU.txt of rtlsdr-scanner-v4). This module only detects it and uses
its extra functions; with the stock library everything here returns None
and the scanner works as before.

* rtlsdr_get_build_info()      - which library is loaded
* rtlsdr_get_tuner_pll_lock()  - did the tuner PLL lock after the last
  retune (the stock library reports success even when it did not)
"""
import ctypes
import logging

logger = logging.getLogger(__name__)

_cache = {}


def _lib():
    """pyrtlsdr's library if it is the scanner build, else None."""
    if 'lib' in _cache:
        return _cache['lib']
    lib = None
    try:
        from rtlsdr.librtlsdr import librtlsdr
        if hasattr(librtlsdr, 'rtlsdr_get_tuner_pll_lock'):
            f = librtlsdr.rtlsdr_get_tuner_pll_lock
            f.restype = ctypes.c_int
            f.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                          ctypes.POINTER(ctypes.c_uint32)]
            if hasattr(librtlsdr, 'rtlsdr_get_build_info'):
                g = librtlsdr.rtlsdr_get_build_info
                g.restype = ctypes.c_char_p
                g.argtypes = []
            lib = librtlsdr
    except Exception:
        lib = None
    _cache['lib'] = lib
    return lib


def _dev(sdr):
    dev_p = getattr(sdr, 'dev_p', None)
    if dev_p is None:
        return None
    return getattr(dev_p, 'value', dev_p)


def build_info():
    lib = _lib()
    if lib is None:
        return None
    if not hasattr(lib, 'rtlsdr_get_build_info'):
        return 'scanner build (no build info)'
    return lib.rtlsdr_get_build_info().decode('ascii', 'replace')


def is_scanner_build():
    return _lib() is not None


def configure(sdr):
    """Report which library is in use. Returns True for the scanner build."""
    info = build_info()
    if info is None:
        print('rtlsdr library: stock (no PLL lock check)', flush=True)
        return False
    print('rtlsdr library: %s' % info, flush=True)
    return True


def _call(sdr):
    lib = _lib()
    dev = _dev(sdr)
    if lib is None or dev is None or not getattr(sdr, 'device_opened', True):
        return None
    late = ctypes.c_uint32(0)
    unlocks = ctypes.c_uint32(0)
    r = lib.rtlsdr_get_tuner_pll_lock(dev, ctypes.byref(late), ctypes.byref(unlocks))
    return r, late.value, unlocks.value


def tuner_locked(sdr):
    """True / False after the last retune, None if the library cannot tell."""
    res = _call(sdr)
    if res is None or res[0] < 0:
        return None
    return bool(res[0])


def pll_stats(sdr):
    """(late_locks, unlocks) since the device was opened, or None."""
    res = _call(sdr)
    if res is None or res[0] < 0:
        return None
    return res[1], res[2]
