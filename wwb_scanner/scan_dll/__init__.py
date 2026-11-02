"""Scan edition of the RTL-SDR driver library (rtlsdr.dll "wwbscan").

The files in ``win64/`` are built from the RTL-SDR Blog sources (V4 support)
with three additions for scanning:

* fast retune: unchanged tuner registers are not written again, the PLL
  registers go out in one I2C transfer (about 25 -> 11 USB control
  transfers per retune);
* PLL lock state after each retune (the stock library reports success
  even when the PLL did not lock);
* a timeout for blocking reads, so a dongle that stops streaming returns
  an error instead of hanging.

``install()`` runs when the ``wwb_scanner`` package is imported, before
pyrtlsdr is imported, and makes pyrtlsdr use this library. Set the
environment variable ``WWBSCAN_STOCK_DLL=1`` to use the library that was
installed before (the original behaviour).
"""
import os
import sys
import ctypes
import logging
import types

logger = logging.getLogger(__name__)

DLL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'win64')
FAST_TUNE = os.environ.get('WWBSCAN_FAST_TUNE', '1') != '0'
READ_TIMEOUT_MS = 2000

LOADED = None      # ctypes library loaded by install(), or None
STATUS = 'not installed'


def _setup_prototypes(lib):
    if not hasattr(lib, 'rtlsdr_wwbscan_version'):
        return False
    lib.rtlsdr_wwbscan_version.restype = ctypes.c_char_p
    lib.rtlsdr_wwbscan_version.argtypes = []
    for name in ('rtlsdr_set_fast_tune', 'rtlsdr_set_read_timeout'):
        f = getattr(lib, name)
        f.restype = ctypes.c_int
        f.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    for name in ('rtlsdr_get_fast_tune', 'rtlsdr_get_tuner_lock'):
        f = getattr(lib, name)
        f.restype = ctypes.c_int
        f.argtypes = [ctypes.c_void_p]
    return True


def install():
    """Load the scan edition DLL and hand it to pyrtlsdr (Windows only)."""
    global LOADED, STATUS
    if LOADED is not None:
        return LOADED
    if sys.platform != 'win32':
        STATUS = 'not Windows, using system librtlsdr'
        return None
    if os.environ.get('WWBSCAN_STOCK_DLL', '0') not in ('', '0'):
        STATUS = 'disabled by WWBSCAN_STOCK_DLL'
        return None
    if 'rtlsdr' in sys.modules:
        STATUS = 'pyrtlsdr was imported first, using its library'
        return None
    rtl_path = os.path.join(DLL_DIR, 'rtlsdr.dll')
    usb_path = os.path.join(DLL_DIR, 'libusb-1.0.dll')
    if not os.path.exists(rtl_path):
        STATUS = 'scan edition DLL not found'
        return None
    try:
        # libusb first, so rtlsdr.dll finds it by name (works on Python 3.7 too)
        if os.path.exists(usb_path):
            ctypes.CDLL(usb_path)
        lib = ctypes.CDLL(rtl_path)
        if not _setup_prototypes(lib):
            STATUS = 'DLL is not the scan edition'
            return None
    except OSError as exc:
        STATUS = 'could not load scan edition DLL: %s' % exc
        return None
    # pyrtlsdr asks the optional "pyrtlsdrlib" package for a library first
    shim = types.ModuleType('pyrtlsdrlib')
    shim.load_librtlsdr = lambda: lib
    sys.modules['pyrtlsdrlib'] = shim
    LOADED = lib
    STATUS = 'loaded %s from %s' % (lib.rtlsdr_wwbscan_version().decode(), DLL_DIR)
    return lib


def _lib():
    try:
        from rtlsdr.librtlsdr import librtlsdr
    except Exception:
        return None
    if getattr(librtlsdr, '_wwbscan_ready', None) is None:
        try:
            librtlsdr._wwbscan_ready = _setup_prototypes(librtlsdr)
        except Exception:
            librtlsdr._wwbscan_ready = False
    return librtlsdr if librtlsdr._wwbscan_ready else None


def _dev(sdr):
    dev_p = getattr(sdr, 'dev_p', None)
    if dev_p is None:
        return None
    return getattr(dev_p, 'value', dev_p)


def is_scan_edition():
    return _lib() is not None


def configure(sdr):
    """Turn on fast retune and the read timeout for an opened RtlSdr."""
    lib = _lib()
    dev = _dev(sdr)
    if lib is None or dev is None:
        print('rtlsdr library: stock (%s)' % STATUS, flush=True)
        return False
    lib.rtlsdr_set_fast_tune(dev, 1 if FAST_TUNE else 0)
    lib.rtlsdr_set_read_timeout(dev, READ_TIMEOUT_MS)
    print('rtlsdr library: %s, fast tune %s, read timeout %d ms' % (
        lib.rtlsdr_wwbscan_version().decode(), 'on' if FAST_TUNE else 'off',
        READ_TIMEOUT_MS), flush=True)
    return True


def tuner_locked(sdr):
    """True / False after the last retune, None if the library cannot tell."""
    lib = _lib()
    dev = _dev(sdr)
    if lib is None or dev is None:
        return None
    r = lib.rtlsdr_get_tuner_lock(dev)
    if r < 0:
        return None
    return bool(r)
