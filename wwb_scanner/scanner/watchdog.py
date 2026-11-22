"""V22.8 (external audit 22.11.2026, A7): a scan that stops moving leaves
the state of every thread in the log file.

"A long scan froze" could not be looked into: the program had no record of
where it stood. Every USB call of a scan goes through
``sample_processing.run_with_timeout`` (and opening / closing the dongle
through ``SdrWrapper``); each of them calls :func:`beat`. While a scan runs
a :class:`Watchdog` looks at the time of the last beat. With no beat for
``STALL_S`` (longer than any USB call may take before its own timeout) it
writes the stack of every thread to the log (``logs/wwb_scanner.log``) and
one line to the status line, once per stall. It never stops or changes the
scan.
"""
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger('wwb_scanner.watchdog')

# READ_TIMEOUT (40 s) is the longest a single USB call may take before the
# scan gives up on it; a beat comes before and after every call.
STALL_S = 60.
CHECK_S = 1.

_last = [time.monotonic()]


def beat():
    """Something moved (a USB call started or ended, a tile was read)."""
    _last[0] = time.monotonic()


def idle_seconds():
    return time.monotonic() - _last[0]


def thread_stacks():
    """The Python stack of every thread, as text."""
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for ident, frame in sorted(sys._current_frames().items(), key=lambda kv: str(names.get(kv[0]))):
        out.append('--- thread %s (id %s)' % (names.get(ident, '?'), ident))
        out.append(''.join(traceback.format_stack(frame)).rstrip())
    return '\n'.join(out)


class Watchdog(object):
    """Started at the beginning of a scan, stopped at its end."""
    def __init__(self, what='scan', stall_s=None, check_s=None):
        self.what = what
        self.stall_s = STALL_S if stall_s is None else float(stall_s)
        self.check_s = CHECK_S if check_s is None else float(check_s)
        self.dumps = 0
        self._done = threading.Event()
        self._thread = None
    def start(self):
        beat()
        self._done.clear()
        self._thread = threading.Thread(target=self._run, name='wwb-watchdog', daemon=True)
        self._thread.start()
        return self
    def stop(self):
        self._done.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(2.)
        self._thread = None
    def _run(self):
        reported = None
        while not self._done.wait(self.check_s):
            last = _last[0]
            idle = time.monotonic() - last
            if idle < self.stall_s or reported == last:
                continue
            reported = last
            self.dumps += 1
            try:
                # the first line reaches the status line, the stacks only the log
                logger.warning('The %s has not moved for %.0f s (the dongle does not answer?). '
                               'The state of the program is in the log file (logs/wwb_scanner.log): '
                               'send it with your report.\n%s', self.what, idle, thread_stacks())
            except Exception:
                pass
    def __enter__(self):
        return self.start()
    def __exit__(self, *args):
        self.stop()
