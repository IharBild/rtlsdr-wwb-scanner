"""V22.3: RTL-SDR (RTL-SDR Blog V4 and others) through pyrtlsdr and our DLL.

pyrtlsdr lists gains in tenths of a dB (get_gains) and takes and gives the
gain in dB (the ``gain`` property). Our DLL (scanner-v4 and newer) adds the
PLL lock check and the I2C counters (wwb_scanner/scan_dll).

V22.9 (external audit P3-01): every call on the device goes through the
gate (gate.py), so the dongle is never closed under a running call.
"""
from wwb_scanner import scan_dll
from wwb_scanner.receivers import Receiver
from wwb_scanner.receivers.gate import call


class RtlSdrReceiver(Receiver):
    name = 'rtlsdr'
    def __init__(self, dev):
        self.dev = dev
    def tune(self, freq_hz):
        with call(self.dev) as d:
            d.set_center_freq(freq_hz)
    def read(self, n):
        with call(self.dev) as d:
            return d.read_samples(n)
    @property
    def gain(self):
        with call(self.dev) as d:
            return d.gain
    @gain.setter
    def gain(self, db):
        with call(self.dev) as d:
            d.gain = db
    def gains(self):
        with call(self.dev) as d:
            return [g / 10. for g in d.get_gains()]
    @property
    def sample_rate(self):
        with call(self.dev) as d:
            return d.sample_rate
    @sample_rate.setter
    def sample_rate(self, hz):
        with call(self.dev) as d:
            d.sample_rate = hz
    @property
    def is_open(self):
        return getattr(self.dev, 'device_opened', True)
    def tuner_locked(self):
        with call(self.dev) as d:
            return scan_dll.tuner_locked(d)
    def pll_stats(self):
        with call(self.dev) as d:
            return scan_dll.pll_stats(d)
    def i2c_stats(self):
        with call(self.dev) as d:
            return scan_dll.i2c_stats(d)
    def close(self):
        self.dev.close()
    def __repr__(self):
        return '<RtlSdrReceiver %r>' % (self.dev,)
