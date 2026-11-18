"""V22.3: receivers (audit 16.11.2026, item 8).

The scanner core talks to a receiver only through the ``Receiver``
interface below; before V22.3 it called pyrtlsdr's RtlSdr object directly
in 27 places of four modules. A second RTL-SDR, another kind of receiver
(SoapySDR: Airspy, SDRplay) or a receiver in another process (so that a USB
call that never returns can be ended) is then one more class here.

Units: frequencies in Hz, gain in dB, samples as complex numbers with full
scale 1.0. A method that fails raises; ``is_open`` tells whether the device
can still be used after an error (pyrtlsdr closes it after any library
error).
"""


class Receiver(object):
    """What the scanner needs from a receiver."""
    name = 'receiver'
    def tune(self, freq_hz):
        """Set the centre frequency."""
        raise NotImplementedError
    def read(self, n):
        """``n`` complex samples (blocking)."""
        raise NotImplementedError
    @property
    def gain(self):
        """The gain in use, dB."""
        raise NotImplementedError
    @gain.setter
    def gain(self, db):
        raise NotImplementedError
    def gains(self):
        """The gain steps of the receiver, dB, as it lists them."""
        raise NotImplementedError
    @property
    def sample_rate(self):
        raise NotImplementedError
    @sample_rate.setter
    def sample_rate(self, hz):
        raise NotImplementedError
    @property
    def is_open(self):
        return True
    def tuner_locked(self):
        """True / False after the last tune, None when the receiver can not tell."""
        return None
    def pll_stats(self):
        """(late locks, unlocks) since open, or None."""
        return None
    def i2c_stats(self):
        """(retried, failed) control transfers since open, or None."""
        return None
    def close(self):
        pass


def as_receiver(dev):
    """``dev`` as a Receiver: a Receiver is returned as it is; anything else
    is taken for a pyrtlsdr RtlSdr (or an object with its methods, like the
    dongle simulator of the tests)."""
    if dev is None or isinstance(dev, Receiver):
        return dev
    from wwb_scanner.receivers.rtlsdr import RtlSdrReceiver
    return RtlSdrReceiver(dev)


def receiver_of(scanner):
    """The receiver of a scanner (its own one, or one around scanner.sdr)."""
    rx = getattr(scanner, 'receiver', None)
    if rx is not None:
        return rx
    return as_receiver(getattr(scanner, 'sdr', None))
