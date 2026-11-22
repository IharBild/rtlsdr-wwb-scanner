"""V22.9 (external audit P2-C1 / P3-01): no dongle is closed under a USB call.

Closing a dongle while another thread is inside a call on it (a read, a
retune, a gain change) frees the libusb handle under a live transfer:
libusb stops the whole program (``Assertion failed: refcnt >= 2``, exit
code 3 on the PC). V22.8 only knew calls that hang (``stuck_usb_calls``).

Every call of a Receiver on its device goes through ``call(dev)``;
closing goes through ``quiet(dev)``, which

* lets no new call start on that device,
* waits until the calls that run have returned (at most ``wait_s``),
* tells whether the device is quiet - only then may it be closed.

A call that starts after the device was closed raises ``DongleClosed``
instead of reaching the freed handle (pyrtlsdr does not check it).
One condition for the whole process: calls on two dongles only wait for
each other while one of them is being closed (a few ms).
"""
import contextlib
import threading
import time

CLOSE_WAIT_S = 5.     # closing waits this long for calls that run (a read takes ~0.05 s)
CALL_WAIT_S = 10.     # a call waits this long while its dongle is being closed

_cond = threading.Condition()
_active = {}          # id(device) -> number of calls inside the device now
_closing = set()      # id(device) of devices being closed


class DongleClosed(IOError):
    """A USB call on a dongle that is closed (or being closed)."""


def _opened(dev):
    v = getattr(dev, 'device_opened', None)
    return True if v is None else bool(v)


@contextlib.contextmanager
def call(dev):
    key = id(dev)
    deadline = time.monotonic() + CALL_WAIT_S
    with _cond:
        while key in _closing:
            left = deadline - time.monotonic()
            if left <= 0:
                raise DongleClosed('the dongle is being closed')
            _cond.wait(left)
        if not _opened(dev):
            raise DongleClosed('the dongle was closed (use after close)')
        _active[key] = _active.get(key, 0) + 1
    try:
        yield dev
    finally:
        with _cond:
            n = _active.get(key, 1) - 1
            if n > 0:
                _active[key] = n
            else:
                _active.pop(key, None)
            _cond.notify_all()


def active(dev):
    """How many calls are inside ``dev`` now."""
    with _cond:
        return _active.get(id(dev), 0)


@contextlib.contextmanager
def quiet(dev, wait_s=None):
    """``with quiet(dev) as ok:`` - no new call on ``dev`` starts inside the
    block; ``ok`` is True when no call runs on it (it may be closed then)."""
    key = id(dev)
    wait_s = CLOSE_WAIT_S if wait_s is None else wait_s
    deadline = time.monotonic() + wait_s
    with _cond:
        _closing.add(key)
        while _active.get(key, 0):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            _cond.wait(left)
        ok = not _active.get(key, 0)
    try:
        yield ok
    finally:
        with _cond:
            _closing.discard(key)
            _cond.notify_all()
