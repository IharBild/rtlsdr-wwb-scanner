from wwb_scanner import events
import threading
import traceback

from wwb_scanner import scan_dll

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

class SdrWrapper(object):
    def __init__(self, **kwargs):
        self.sdr = None
        self.scanner = kwargs.get('scanner')
        self.enable_scanner_updates = kwargs.get('enable_scanner_updates', True)
        self.device_open = threading.Event()
        self.device_wait = threading.Event()
        self.device_lock = threading.RLock()
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
    def open_sdr(self):
        with self.device_lock:
            if self.sdr is not None:
                if not self.sdr.device_opened:
                    self.sdr.close()
                    self.sdr = None
                    self.device_open.clear()
                else:
                    self.device_open.wait()
            if self.sdr is None:
                self.sdr = self._open_sdr_local()
                if self.sdr is not None:
                    # V18.1 (audit 1.1): gain table read here, in the scan thread
                    learn = getattr(self.scanner, 'learn_gains', None)
                    if learn is not None and self.enable_scanner_updates:
                        learn(self.sdr)
                    self.set_sdr_values()
                    self.device_open.set()
        return self.sdr
    def _open_sdr_local(self):
        serial_number = self.scanner.device_config.serial_number
        try:
            sdr = rtlsdr_class()(serial_number=serial_number)
        except IOError:
            sdr = None
        if sdr is not None:
            try:
                scan_dll.configure(sdr)
            except Exception:
                traceback.print_exc()
        return sdr
    def close_sdr(self):
        with self.device_lock:
            if self.sdr is not None:
                report_i2c_at_close(self.sdr)
                self.sdr.close()
                self.sdr = None
                self.device_open.clear()
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
