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

# V22.10 (decision of 23.11.2026): the IF filter of the R82xx tuner is at most
# this wide. The driver sets it to the sample rate; above 2.43 MHz the tuner
# goes to its 6 MHz mode (IF 3.57 MHz) and the output is 4-6 dB lower
# (measured on the user's V4, 23.11: 2.56 MS/s -96.3 -> -90.3 dBFS/Hz,
# 2.8 and 3.2 MS/s 4.4 dB). Only the centre third of a tile is used, so the
# narrow filter cuts nothing that is kept. At 2.4 MS/s and below nothing
# changes (the same filter as before).
IF_BW_MAX_HZ = 2400000


def if_bandwidth_for(sample_rate):
    """The IF filter for ``sample_rate`` (Hz): min(sample rate, IF_BW_MAX_HZ)."""
    try:
        fs = int(round(float(sample_rate)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(fs, IF_BW_MAX_HZ))


def set_if_bandwidth(dev, sample_rate=None):
    """Set the IF filter of ``dev`` for its sample rate; False when the
    library can not (old pyrtlsdr / DLL)."""
    fn = getattr(dev, 'set_bandwidth', None)
    if fn is None:
        return False
    if sample_rate is None:
        sample_rate = dev.sample_rate
    try:
        fn(if_bandwidth_for(sample_rate))
        return True
    except Exception:
        return False


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
            set_if_bandwidth(d, hz)       # V22.10
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
