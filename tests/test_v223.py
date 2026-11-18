"""V22.3: the receiver interface (audit 16.11.2026, item 8)."""
import io
import os
import re

import numpy as np
import pytest

import sim
from test_v221 import store  # noqa: F401  (fixture)


class SimReceiver(object):
    """A receiver that is not pyrtlsdr: only the Receiver methods (tune,
    read, gain, gains, sample_rate, is_open, close), no set_center_freq,
    read_samples or get_gains."""
    def __init__(self, air=None):
        from wwb_scanner.receivers import Receiver
        self.__class__ = type('SimReceiverImpl', (SimReceiver, Receiver), {})
        self._dev = sim.FakeRtlSdr(air=air)
        self.calls = {'tune': 0, 'read': 0}
        self.closed = False
    def tune(self, freq_hz):
        self.calls['tune'] += 1
        self._dev.set_center_freq(freq_hz)
    def read(self, n):
        self.calls['read'] += 1
        return self._dev.read_samples(n)
    @property
    def gain(self):
        return self._dev.gain
    @gain.setter
    def gain(self, db):
        self._dev.gain = db
    def gains(self):
        return [g / 10. for g in sim.R828D_GAINS]
    @property
    def sample_rate(self):
        return self._dev.sample_rate
    @sample_rate.setter
    def sample_rate(self, hz):
        self._dev.sample_rate = hz
    @property
    def is_open(self):
        return not self.closed
    def close(self):
        self.closed = True


def test_a_whole_scan_through_a_receiver_that_is_not_pyrtlsdr(store, monkeypatch):
    from wwb_scanner.scanner import sdrwrapper, main
    from wwb_scanner.receivers import Receiver
    made = []
    def factory(serial_number=None):
        made.append(SimReceiver(air=sim.Air(noise=0.02).tone(475.0e6, 0.05)))
        return made[-1]
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', factory)
    sc = main.Scanner(config=sim.make_config(470, 480, auto_gain=True), history=None)
    sc.run_scan()
    rx = made[-1]
    assert isinstance(rx, Receiver) and sc.error is None
    f, y = sim.spectrum_arrays(sc)
    assert f.size == 401 and rx.calls['tune'] >= 13 and rx.calls['read'] >= 26
    assert abs(f[np.argmax(y)] - 475.0) <= 0.025
    assert rx.closed                                  # closed at the end of the scan
    assert sc.gains and sc.gains[-1] == 49.6          # gain table read through the interface


def test_rtlsdr_receiver_units():
    from wwb_scanner.receivers import as_receiver
    from wwb_scanner.receivers.rtlsdr import RtlSdrReceiver
    dev = sim.FakeRtlSdr()
    rx = as_receiver(dev)
    assert isinstance(rx, RtlSdrReceiver) and as_receiver(rx) is rx and as_receiver(None) is None
    assert rx.gains()[-1] == 49.6 and rx.gains()[0] == 0.0      # pyrtlsdr lists tenths of a dB
    rx.gain = 33.8
    assert dev.gain == rx.gain
    rx.sample_rate = 2.4e6
    assert dev.sample_rate == 2.4e6
    rx.tune(600e6)
    assert np.iscomplexobj(rx.read(1024)) and rx.is_open
    assert rx.tuner_locked() is None and rx.pll_stats() is None and rx.i2c_stats() is None


def test_the_core_talks_to_the_dongle_only_through_the_receiver():
    """No pyrtlsdr method is called in the scanner core outside the RTL-SDR
    receiver and the dongle opener (sdrwrapper.py)."""
    import wwb_scanner
    root = os.path.dirname(wwb_scanner.__file__)
    bad = []
    pat = re.compile(r'\.(set_center_freq|read_samples|get_gains|read_samples_async|cancel_read_async|'
                     r'get_tuner_type|device_opened)\b')
    for sub in ('scanner', 'service.py'):
        base = os.path.join(root, sub)
        files = [base] if base.endswith('.py') else [
            os.path.join(base, f) for f in os.listdir(base) if f.endswith('.py')]
        for p in files:
            if os.path.basename(p) == 'sdrwrapper.py':
                continue
            for i, line in enumerate(io.open(p, encoding='utf-8'), 1):
                code = line.split('#')[0]
                code = code.replace('self.get_gains', '').replace('sample_set.read_samples', '')  # own methods
                if pat.search(code):
                    bad.append('%s:%d %s' % (os.path.basename(p), i, line.strip()))
    assert not bad, bad
