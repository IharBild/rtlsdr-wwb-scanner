"""V22.3: RTL-SDR (RTL-SDR Blog V4 and others) through pyrtlsdr and our DLL.

pyrtlsdr lists gains in tenths of a dB (get_gains) and takes and gives the
gain in dB (the ``gain`` property). Our DLL (scanner-v4 and newer) adds the
PLL lock check and the I2C counters (wwb_scanner/scan_dll).
"""
from wwb_scanner import scan_dll
from wwb_scanner.receivers import Receiver


class RtlSdrReceiver(Receiver):
    name = 'rtlsdr'
    def __init__(self, dev):
        self.dev = dev
    def tune(self, freq_hz):
        self.dev.set_center_freq(freq_hz)
    def read(self, n):
        return self.dev.read_samples(n)
    @property
    def gain(self):
        return self.dev.gain
    @gain.setter
    def gain(self, db):
        self.dev.gain = db
    def gains(self):
        return [g / 10. for g in self.dev.get_gains()]
    @property
    def sample_rate(self):
        return self.dev.sample_rate
    @sample_rate.setter
    def sample_rate(self, hz):
        self.dev.sample_rate = hz
    @property
    def is_open(self):
        return getattr(self.dev, 'device_opened', True)
    def tuner_locked(self):
        return scan_dll.tuner_locked(self.dev)
    def pll_stats(self):
        return scan_dll.pll_stats(self.dev)
    def i2c_stats(self):
        return scan_dll.i2c_stats(self.dev)
    def close(self):
        self.dev.close()
    def __repr__(self):
        return '<RtlSdrReceiver %r>' % (self.dev,)
