from wwb_scanner import events
import logging
import threading
import traceback

from wwb_scanner import scan_dll
from wwb_scanner.receivers import gate
from wwb_scanner.scanner import watchdog

logger = logging.getLogger(__name__)

# V22.8 (external audit A1): opening or closing waits at most this long for
# another thread that holds the dongle (one that hangs in a USB call)
LOCK_WAIT_S = 15.

# V22.9 (external audit AUD-04): opening (finding the dongle and opening its
# USB device) gives up after this long instead of waiting for ever
OPEN_TIMEOUT_S = 20.

# V22.8 (external audit B3): dongles left open because a USB call on them
# still hangs. Closing frees the handle under that call (memory corruption,
# 0xC0000374 on the PC); pyrtlsdr closes in __del__, so they are kept here.
_ABANDONED = []


def _usb_stuck():
    from wwb_scanner.scanner.sample_processing import stuck_usb_calls
    try:
        return stuck_usb_calls()
    except Exception:
        return 0

# V22.0: pyrtlsdr (and with it the dongle library) is loaded when a dongle
# is opened, not when the scanner is imported: the core, its tests and the
# file tools work on a computer without the library. Tests put a simulated
# dongle class here.
RtlSdr = None


def rtlsdr_class():
    global RtlSdr
    if RtlSdr is None:
        from rtlsdr import RtlSdr as _RtlSdr
        RtlSdr = _RtlSdr
    return RtlSdr

def _is_open(dev):
    """pyrtlsdr: device_opened; a Receiver (V22.3): is_open."""
    v = getattr(dev, 'device_opened', None)
    return getattr(dev, 'is_open', True) if v is None else v


class SdrWrapper(object):
    def __init__(self, **kwargs):
        self.sdr = None
        self.scanner = kwargs.get('scanner')
        self.enable_scanner_updates = kwargs.get('enable_scanner_updates', True)
        self.device_open = threading.Event()
        self.device_wait = threading.Event()
        self.device_lock = threading.RLock()
        self._rx = (None, None)       # V22.3: (device, its Receiver)
        self.serial = None            # V22.4: serial number of the open dongle (None: unknown)
    def set_sdr_values(self):
        if not self.enable_scanner_updates:
            return
        scanner = self.scanner
        if scanner is None:
            return
        sdr = self.sdr
        if sdr is None:
            return
        keys = ['sample_rate', 'gain', 'freq_correction']
        scanner_vals = {key: getattr(scanner, key, None) for key in keys}
        for key, scanner_val in scanner_vals.items():
            if key == 'gain':
                sdr_val = None
            elif key == 'freq_correction' and scanner_val in [0, None]:
                continue
            else:
                sdr_val = getattr(sdr, key)
            if sdr_val == scanner_val:
                continue
            setattr(sdr, key, scanner_val)
            if key == 'gain' and scanner_val == 0:
                sdr_val = 0.
            else:
                sdr_val = getattr(sdr, key)
            if sdr_val != scanner_val:
                setattr(scanner, key, sdr_val)
        # V22.10: the IF filter follows the sample rate, at most 2.4 MHz
        from wwb_scanner.receivers.rtlsdr import set_if_bandwidth
        set_if_bandwidth(sdr)
    def _lock(self, what):
        if self.device_lock.acquire(timeout=LOCK_WAIT_S):
            return True
        logger.error('%s: the dongle is still busy in another part of the program after %.0f s '
                     '(a USB call hangs)', what, LOCK_WAIT_S)
        return False

    def open_sdr(self):
        """The open dongle (None: not found or busy).

        V22.8 (external audit A1): when setting the dongle up fails (sample
        rate, gain), it is closed again and the error raised. Before, its
        handle stayed open without ``device_open`` and the next open_sdr
        waited for ever under the lock - also the reopen after a USB error
        in a scan. A half set-up dongle left by anything else is closed and
        opened anew, and nothing waits without a time limit."""
        if not self._lock('opening'):
            raise IOError('The dongle is still busy (a USB call hangs). Unplug it, plug it back in '
                          'and start again; if this message comes back, restart the program')
        try:
            if self.sdr is not None:
                if _is_open(self.sdr) and self.device_open.is_set():
                    return self.sdr
                # closed after a USB error, or never set up: not used again
                self._drop_sdr()
            watchdog.beat()
            sdr = self._open_sdr_local()
            watchdog.beat()
            if sdr is None:
                return None
            self.sdr = sdr
            try:
                # V18.1 (audit 1.1): gain table read here, in the scan thread
                learn = getattr(self.scanner, 'learn_gains', None)
                if learn is not None and self.enable_scanner_updates:
                    learn(self.sdr)
                self.set_sdr_values()
            except Exception:
                logger.warning('setting up the dongle failed, it is closed again', exc_info=True)
                self._drop_sdr()
                raise
            self.device_open.set()
            return self.sdr
        finally:
            self.device_lock.release()

    def _drop_sdr(self):
        """Forget the dongle and close it - unless a USB call on it still
        hangs (V22.8, audit B3): then it stays open until the program ends."""
        sdr, self.sdr = self.sdr, None
        self.device_open.clear()
        self._rx = (None, None)
        if sdr is None:
            return
        if _usb_stuck():
            _ABANDONED.append(sdr)
            logger.error('The dongle is not closed: a USB call on it still hangs (closing it now '
                         'could crash the program). Unplug it and plug it back in.')
            return
        # V22.9 (external audit P3-01): a call that runs now (a read in the
        # scan thread) ends first; no new one starts while it is closed
        watchdog.beat()
        with gate.quiet(sdr) as ok:
            if not ok:
                _ABANDONED.append(sdr)
                logger.error('The dongle is not closed: a USB call on it did not end in %.0f s '
                             '(closing it now could crash the program). Unplug it and plug it '
                             'back in.', gate.CLOSE_WAIT_S)
                return
            try:
                sdr.close()
            except Exception:
                logger.exception('closing the dongle failed')
        watchdog.beat()
    def _open_sdr_local(self):
        """V22.9 (external audit AUD-04): in a helper thread with a time
        limit - a dongle that does not answer while it is found or opened
        gives an error instead of a program that waits for ever."""
        from wwb_scanner.scanner.sample_processing import run_with_timeout, SdrTimeout
        try:
            return run_with_timeout(self._open_sdr_now, OPEN_TIMEOUT_S)
        except SdrTimeout:
            raise IOError('The dongle did not answer within %.0f s while it was opened. Unplug it, '
                          'plug it back in and start again; if this message comes back, restart '
                          'the program' % OPEN_TIMEOUT_S)
    def _open_sdr_now(self):
        from wwb_scanner import dongles
        dc = self.scanner.device_config
        serial_number = dc.serial_number
        cls = rtlsdr_class()
        # V22.4: which dongle this opens (its calibrations are used); two
        # dongles with one serial number are opened by the chosen index
        serial, kwargs = dongles.resolve(cls, serial_number, dc.get('device_index'))
        try:
            sdr = cls(**kwargs)
        except IOError:
            sdr = None
        if sdr is not None:
            self.serial = serial
            if serial is not None:
                dongles.use(serial)
            try:
                scan_dll.configure(sdr)
            except Exception:
                traceback.print_exc()
        return sdr
    @property
    def receiver(self):
        """V22.3: the open dongle as a Receiver (wwb_scanner/receivers)."""
        dev = self.sdr
        if dev is None:
            return None
        cached_dev, rx = getattr(self, '_rx', (None, None))
        if cached_dev is not dev:
            from wwb_scanner.receivers import as_receiver
            rx = as_receiver(dev)
            if getattr(rx, 'serial', None) is None and self.serial is not None:
                try:
                    rx.serial = self.serial
                except AttributeError:
                    pass
            self._rx = (dev, rx)
        return rx
    def close_sdr(self):
        if not self._lock('closing'):
            return          # V22.8: never wait for ever (the opening thread hangs)
        try:
            if self.sdr is not None:
                if not _usb_stuck():
                    report_i2c_at_close(self.sdr)
                self._drop_sdr()
        finally:
            self.device_lock.release()
    def __enter__(self):
        self.open_sdr()
    def __exit__(self, *args):
        self.close_sdr()


def report_i2c_at_close(sdr):
    """V21.4.1: tuner I2C counters (since open) once more just before close.
    The scan prints them after its last tile, but the PC showed
    "r82xx: tuner registers restored" while every scan said "0 retried":
    the retries happened in calibrations, the peak check or other work
    after the report. Printed only when there were any."""
    try:
        i2c = scan_dll.i2c_stats(sdr)
    except Exception:
        return None
    if i2c is not None and (i2c[0] or i2c[1]):
        events.console('tuner I2C before close: %d transfer(s) retried, %d failed after retries '
              '(since the dongle was opened)' % i2c)
    return i2c
