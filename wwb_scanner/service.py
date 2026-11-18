"""V22.1: the scan service of the core (audit 16.11.2026, item 3).

What the program window decided by itself before V22.1 is here, so the
window, the command line and the future web interface do the same:

- ``prepare()``: the settings of a scan of a kind (ordinary scan, floor
  calibration without antenna, gain step calibration), its name and why it
  can not start;
- ``run()``: run a prepared scan, finish it (a floor calibration is saved
  here) and describe the result in one object (``ScanResult``);
- ``ScanService``: start / stop / wait in a thread of its own (command line,
  web server, tests). The window keeps its Qt thread and calls ``run()``.

Everything is told on the event bus (wwb_scanner/events.py).
"""
import logging
import threading
import time

import numpy as np

from wwb_scanner import events
from wwb_scanner.scanner import config as config_mod

logger = logging.getLogger(__name__)
status = logging.getLogger('wwb_scanner.status')

SCAN = 'scan'
FLOOR_CALIBRATION = 'floor_calibration'      # 31-1766 MHz without antenna (cleanup.py)
GAIN_CALIBRATION = 'gain_calibration'        # real gain steps on signals of the air (gain_cal.py)
KINDS = (SCAN, FLOOR_CALIBRATION, GAIN_CALIBRATION)

NO_DONGLE_TEXT = ('No RTL-SDR dongle found. Plug the dongle in (close other SDR programs) '
                  'and press Start again.')


class ScanError(Exception):
    """The scan can not start; the text says why (for the user)."""


def _number(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float('inf') else None


def start_problem(conf, kind=SCAN, dongle_found=True):
    """Why a scan with ``conf`` (ScanConfig, before prepare) can not start:
    a text for the user, or ''."""
    if not dongle_found:
        return NO_DONGLE_TEXT
    rate = _number(conf.sampling.get('sample_rate'))
    if rate is None or rate <= 0:
        return 'The sample rate is not set: enter it in Device Settings (for example 2400).'
    if _number(conf.device.get('gain')) is None:
        return 'The gain is not set: choose it in Device Settings.'
    if kind != SCAN or conf.get('scan_ranges'):
        return ''           # these scans bring their own ranges
    return conf.check() or ''


def prepare(kind, base, when=None):
    """(ScanConfig, name) of a scan of ``kind`` from the user's settings
    ``base`` (a ScanConfig; not changed)."""
    if kind not in KINDS:
        raise ValueError('unknown scan kind %r' % (kind,))
    conf = base.copy()
    for meta in ('eid', 'datetime'):
        if meta in conf:
            del conf[meta]                  # a new scan gets new stored settings
    off = config_mod.raw_processing()
    if kind == GAIN_CALIBRATION:
        # V20.5: survey tiles for the gain step calibration
        from wwb_scanner.scanner import gain_cal
        conf.scan_range = list(gain_cal.GAIN_CAL_RANGE_MHZ)
        conf.scan_ranges = gain_cal.survey_ranges()
        conf.gain_calibration = True
        conf.device.gain = gain_cal.SURVEY_GAIN_DB
        conf.sampling.auto_gain = False
        conf.sampling.verify_peaks = False
        conf.processing.update(off)
        name = 'Gain step calibration (survey, gain %s dB)' % gain_cal.SURVEY_GAIN_DB
    elif kind == FLOOR_CALIBRATION:
        # whole range, values exactly as measured
        from wwb_scanner.scanner import cleanup
        conf.scan_range = list(cleanup.CALIBRATION_RANGE_MHZ)
        conf.scan_ranges = None
        conf.gain_calibration = False
        conf.sampling.auto_gain = False
        conf.sampling.verify_peaks = False
        conf.processing.update(off)
        name = 'Calibration without antenna, gain %s dB' % base.device.gain
    else:
        conf.gain_calibration = False
        # V20.7: the start time in the name (rename in the scan list)
        stamp = time.strftime('%d.%m %H:%M', time.localtime(when))
        segs = conf.get('scan_ranges')
        if segs and len(segs) > 1:
            name = 'Bands %s MHz, %s' % (', '.join('%g-%g' % (float(a), float(b)) for a, b in segs), stamp)
        else:
            lo, hi = conf.scan_range
            name = '%g-%g MHz, %s' % (float(lo), float(hi), stamp)
    return conf, name


class ScanResult(object):
    """What came out of one scan (V22.1: one object instead of attributes
    spread over the scanner and its tile collection)."""
    FIELDS = ('kind', 'name', 'ranges', 'seconds', 'passes', 'view', 'error', 'refused', 'stopped_by_user', 'aborted',
              'tiles_done', 'tiles_total', 'missing_tiles', 'overload_note', 'overload_warn',
              'images', 'peak_checks', 'points', 'saved_eid', 'calibration_ok', 'calibration_text',
              'gain_cal_ok', 'gain_cal_text')
    def __init__(self, **kw):
        for f in self.FIELDS:
            setattr(self, f, kw.get(f))
        self.spectrum = kw.get('spectrum')
    @property
    def ok(self):
        """Finished as asked: no error, not stopped, not aborted."""
        return not (self.error or self.refused or self.stopped_by_user or self.aborted)
    @classmethod
    def collect(cls, scanner, kind=SCAN, seconds=None):
        coll = getattr(scanner, 'sample_collection', None)
        sd = scanner.spectrum.sample_data
        gc = getattr(scanner, 'gain_cal_result', None)
        err = getattr(scanner, 'error', None)
        return cls(
            kind=kind, name=scanner.spectrum.name, spectrum=scanner.spectrum,
            ranges=[list(s) for s in scanner.segments], seconds=seconds,
            error=None if err is None else str(err), refused=bool(getattr(scanner, '_refused', False)),
            stopped_by_user=bool(getattr(scanner, 'stop_requested', False)),
            aborted=bool(getattr(coll, 'aborted', False)),
            tiles_done=len(getattr(coll, 'processed_keys', ()) or ()),
            tiles_total=len(getattr(coll, 'sample_sets', {}) or {}),
            missing_tiles=list(getattr(coll, 'missing_tiles', []) or []),
            overload_note=getattr(coll, 'overload_note', '') or '',
            overload_warn=bool(getattr(coll, 'overload_warn', False)),
            images=list(getattr(coll, 'images', []) or []),
            peak_checks=list(getattr(coll, 'peak_checks', []) or []),
            points=int(sd.size), saved_eid=scanner.spectrum.eid,
            gain_cal_ok=None if gc is None else bool(gc[0]),
            gain_cal_text=None if gc is None else gc[1],
        )
    def as_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS}


def clipped_share(scanner):
    """Share of the ordinary tiles that clipped (None if unknown)."""
    try:
        sets = [x for x in scanner.sample_collection.sample_sets.values()
                if not getattr(x, 'neighbour', False)]
        return (sum(1 for x in sets if getattr(x, 'clip_ratio', 0.) >= 1e-3) / float(len(sets))
                if sets else None)
    except Exception:
        return None


def finish_floor_calibration(scanner, serial=None):
    """V17 / V20.7.3: save the floor calibration of a finished scan without
    antenna. Returns (ok, text); the text also goes to the status line."""
    from wwb_scanner.scanner import cleanup
    if scanner is None or getattr(scanner, 'error', None) is not None or \
            getattr(scanner.sample_collection, 'aborted', False) or \
            getattr(scanner, 'stop_requested', False):
        # V21.1: also when Stop ended it (a part of the range is no calibration)
        text = 'Calibration not saved: the scan did not finish'
        status.warning(text)
        return False, text
    try:
        sd = scanner.spectrum.sample_data
        meta = dict(gain=float(scanner.gain), sample_rate=float(scanner.sample_rate),
                    step_mhz=float(scanner.step_size),
                    window_size=int(scanner.window_size or 0),
                    window_type=str(scanner.sampling_config.window_type))
        if serial is None:
            # V22.4: the serial number of the dongle the scanner opened
            serial = getattr(getattr(scanner, 'sdr_wrapper', None), 'serial', None)
        if serial:
            meta['serial'] = serial
        ok, text = cleanup.calibrate(
            np.asarray(sd['frequency'], dtype=float), np.asarray(sd['dbFS'], dtype=float),
            float(scanner.config.scan_range[0]), scanner.step_size, meta,
            clipped_share=clipped_share(scanner))
    except Exception as exc:
        logging.getLogger('wwb_scanner').error('Calibration failed: %s', exc, exc_info=True)
        return False, 'Calibration failed: %s' % exc
    if ok and serial:
        text += ' - dongle %s' % serial          # V22.4: calibrations are kept per dongle
    (status.info if ok else status.warning)(text)
    return ok, text


def _run_one(scanner):
    try:
        scanner.run_scan()
    except Exception as exc:                    # run_scan itself never raises
        scanner.error = exc
        logger.exception('scan')


def run(scanner, kind=SCAN, serial=None, bus=None):
    """Run a prepared scanner to the end (in the calling thread) and finish
    it. Never raises. Returns the ScanResult (also sent as scan_finished)."""
    bus = bus or events.bus
    t0 = time.time()
    _run_one(scanner)
    result = ScanResult.collect(scanner, kind, seconds=time.time() - t0)
    result.passes = 1
    if kind == FLOOR_CALIBRATION:
        try:
            result.calibration_ok, result.calibration_text = finish_floor_calibration(scanner, serial)
        except Exception as exc:
            result.calibration_ok, result.calibration_text = False, str(exc)
    bus.emit('scan_finished', result=result)
    return result


class ScanService(object):
    """One scan at a time in a thread of its own.

        svc = ScanService()
        svc.start(ScanConfig({'scan_range': [470, 700]}))
        result = svc.wait()
    """
    def __init__(self, history='default', bus=None, scanner_factory=None):
        self.history = history
        self.bus = bus or events.bus
        self.scanner_factory = scanner_factory
        self.scanner = None
        self.thread = None
        self.result = None
        self.kind = None
    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()
    def start(self, conf, kind=SCAN, dongle_found=True, serial=None, name=None, passes=1,
              view='max_hold'):
        """Start a scan of ``kind`` with the user's settings ``conf``.
        ``passes`` > 1 (ordinary scans): the range is scanned that many
        times; every pass is kept (spectrum.passes) and the points are the
        view ``view`` of them (V22.2: the base of the "Precise" mode).
        Raises ScanError (text for the user) when it can not start."""
        if self.running:
            raise ScanError('A scan is running: stop it first.')
        if not isinstance(conf, config_mod.ScanConfig):
            conf = config_mod.ScanConfig(conf)
        why = start_problem(conf, kind, dongle_found)
        if why:
            raise ScanError(why)
        passes = int(passes)
        if passes < 1 or (passes > 1 and kind != SCAN):
            raise ScanError('Several passes are for ordinary scans only (1 or more).')
        from wwb_scanner.scan_objects.passes import VIEWS
        if view not in VIEWS:
            raise ScanError('unknown view %r' % (view,))
        prepared, auto_name = prepare(kind, conf)
        self._prepared, self._name = prepared, name or auto_name
        self._stop_all = False
        scanner = self._new_scanner(history=self.history if passes == 1 else None)
        self.scanner, self.kind, self.result = scanner, kind, None
        if passes == 1:
            target, args = self._run, (scanner, kind, serial)
        else:
            target, args = self._run_passes, (scanner, passes, view)
        self.thread = threading.Thread(target=target, args=args, name='wwb-scan', daemon=True)
        self.thread.start()
        return scanner
    def _new_scanner(self, history):
        factory = self.scanner_factory
        if factory is None:
            from wwb_scanner.scanner.main import Scanner as factory
        scanner = factory(config=self._prepared.copy(), history=history)
        scanner.spectrum.name = self._name
        return scanner
    def _run(self, scanner, kind, serial):
        self.result = run(scanner, kind, serial, self.bus)
    def _run_passes(self, first, passes, view):
        """V22.2: the same scan ``passes`` times, one ScanPasses."""
        from wwb_scanner.scan_objects.passes import ScanPasses
        t0 = time.time()
        kept = ScanPasses()
        scanner = first
        for i in range(passes):
            if i:
                if self._stop_all:
                    break
                scanner = self._new_scanner(history=None)
                self.scanner = scanner
            self.bus.emit('pass_started', index=i, passes=passes)
            started = time.time()
            _run_one(scanner)
            sd = scanner.spectrum.sample_data
            if sd.size:
                kept.add(sd['frequency'], sd['dbFS'], time=started)
            self.bus.emit('pass_finished', index=i, passes=passes, points=int(sd.size))
            if scanner.error is not None or scanner.stop_requested or \
                    getattr(scanner.sample_collection, 'aborted', False):
                break
        result = ScanResult.collect(scanner, SCAN, seconds=time.time() - t0)
        sp = first.spectrum
        if len(kept):
            info = getattr(scanner.spectrum, 'cleanup_info', None)
            sp.set_passes(kept, view)
            if info:
                sp.cleanup_info = info
            if hasattr(sp, 'raw_dbFS'):
                del sp.raw_dbFS                 # belongs to one pass, not to the view
            if self.history is not None and result.error is None:
                try:
                    sp.save_to_dbstore(None if self.history == 'default' else self.history)
                except Exception:
                    logger.exception('saving the scan of %d passes', len(kept))
        result.spectrum, result.points, result.saved_eid = sp, int(sp.sample_data.size), sp.eid
        result.passes, result.view = len(kept), view
        self.result = result
        self.bus.emit('scan_finished', result=result)
    def stop_after_pass(self):
        """Finish the pass that is running, then stop (multi-pass scans)."""
        self._stop_all = True
    def stop(self, wait_start=10.):
        """Ask the running scan to end. A scan that is still opening the
        dongle is waited for (up to ``wait_start`` s) so the request is not
        lost; then it returns at once."""
        s = self.scanner
        if s is None or not self.running:
            return False
        self._stop_all = True
        deadline = time.monotonic() + wait_start
        while not s._running.is_set() and self.running and time.monotonic() < deadline:
            time.sleep(0.01)
        return bool(self.running and s.request_stop())
    def wait(self, timeout=None):
        t = self.thread
        if t is not None:
            t.join(timeout)
        return self.result
