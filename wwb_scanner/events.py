"""V22.1: events of the scanner core (audit 16.11.2026, item 4).

Before V22.1 the core told the window what happened through the logging
module (logger "wwb_scanner.status"), ``print`` and a progress function put
into an attribute of the scanner. A web interface or a live mode needs the
same information as a stream. Every part of the program can now subscribe
to one bus:

    from wwb_scanner import events
    token = events.bus.subscribe(callback)      # callback(event)
    events.bus.unsubscribe(token)

An event is a dict with at least ``kind``; the kinds are:

- ``status``    level ('info' / 'warning' / 'error'), text, logger - the
  status line of the window (records of "wwb_scanner.status" and every
  warning or error of a "wwb_scanner" logger, as the window showed them
  since V16);
- ``console``   text - a line the core prints to the console (the QA tools
  read the console, so the line is still printed as before);
- ``scan_started``  ranges, tiles, name;
- ``progress``  value (0..1);
- ``tile``      center_mhz, points (one tile measured);
- ``scan_finished``  result (wwb_scanner.service.ScanResult).

Callbacks run in the thread that emits (usually the scan thread); a
callback that fails is logged and never stops the scan.
"""
import itertools
import logging
import threading

logger = logging.getLogger(__name__)

STATUS_LOGGER = 'wwb_scanner.status'


class EventBus(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = {}
        self._ids = itertools.count(1)
    def subscribe(self, callback, kinds=None):
        """``kinds``: None = every event, or a list of kinds. Returns a token."""
        token = next(self._ids)
        with self._lock:
            self._subs[token] = (callback, None if kinds is None else frozenset(kinds))
        return token
    def unsubscribe(self, token):
        with self._lock:
            self._subs.pop(token, None)
    def emit(self, kind, **data):
        event = dict(data, kind=kind)
        with self._lock:
            subs = list(self._subs.values())
        for cb, kinds in subs:
            if kinds is not None and kind not in kinds:
                continue
            try:
                cb(event)
            except Exception:
                if kind != 'status':          # no loop through the log bridge
                    logger.exception('event callback failed (%s)', kind)
        return event
    def subscribers(self):
        with self._lock:
            return len(self._subs)


bus = EventBus()


def console(text):
    """A console line of the core: printed (as before V22.1) and sent."""
    print(text, flush=True)
    bus.emit('console', text=str(text))


def status_level(levelno):
    if levelno >= logging.ERROR:
        return 'error'
    if levelno >= logging.WARNING:
        return 'warning'
    return 'info'


class StatusBridge(logging.Handler):
    """Log records -> ``status`` events (the rule of the window's status
    line since V16: INFO of "wwb_scanner.status", WARNING+ of any
    "wwb_scanner" logger)."""
    def emit(self, record):
        try:
            # This handler must not change what reaches the console: without
            # any other handler logging used to print warnings through its
            # "last resort" handler, so do the same here.
            last = logging.lastResort
            if last is not None and record.levelno >= last.level and not logging.getLogger().handlers \
                    and len(logging.getLogger('wwb_scanner').handlers) == 1:
                last.handle(record)
            if record.levelno < logging.WARNING and not record.name.startswith(STATUS_LOGGER):
                return
            text = record.getMessage().strip().split('\n')[0][:400]
            bus.emit('status', level=status_level(record.levelno), text=text, logger=record.name)
        except Exception:
            pass


_bridge = {'handler': None}


def install_log_bridge():
    """Once per process; the core calls it when it is imported."""
    if _bridge['handler'] is not None:
        return _bridge['handler']
    log = logging.getLogger('wwb_scanner')
    if log.level == logging.NOTSET or log.level > logging.INFO:
        log.setLevel(logging.INFO)
    h = StatusBridge(logging.INFO)
    log.addHandler(h)
    _bridge['handler'] = h
    return h
