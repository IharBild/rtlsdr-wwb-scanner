import threading
import sys
import time

import numpy as np

import logging
logger = logging.getLogger(__name__)

# The R828D tuner in the RTL-SDR Blog V4 cannot lock its PLL above this.
MAX_TUNER_FREQ_HZ = 1766000000

from wwb_scanner.core import JSONMixin
from wwb_scanner import events
from wwb_scanner.receivers import as_receiver
events.install_log_bridge()       # V22.1: status records -> events
from wwb_scanner.scanner.sdrwrapper import SdrWrapper
from wwb_scanner.scanner.config import ScanConfig
from wwb_scanner.scanner.sample_processing import (
    tile_keep,
    SampleCollection,
    WINDOW_TYPES,
)
from wwb_scanner.scan_objects import Spectrum

def mhz_to_hz(mhz):
    return mhz * 1000000.0
def hz_to_mhz(hz):
    return hz / 1000000.0

def get_freq_resolution(nfft, fs):
    freqs = np.fft.fftfreq(nfft, 1/fs)
    freqs = np.fft.fftshift(freqs)
    r = np.unique(np.diff(np.around(freqs)))
    if r.size != 1:
        # V18.1: float rounding of the FFT bins (e.g. 2.048 MHz / 96), not an
        # error; at ERROR level it would open the error dialog in the window
        logger.debug(f'frequency resolution not unique (rounding): {r}')
        return r.mean()
    return r[0]

def is_equal_spacing(nfft, fs, step_size):
    freqs = np.fft.fftfreq(nfft, 1/fs)
    freqs = np.fft.fftshift(freqs)

    freqs2 = freqs + step_size
    all_freqs = np.unique(np.around(np.append(freqs, freqs2)))
    diff = np.unique(np.diff(np.around(all_freqs)))
    logger.debug(f'freq spacing diff={diff}')
    return diff.size == 1

class StopScanner(Exception):
    pass

class ScanFailed(Exception):
    """V22.8: the scan can not give a result; the text says why (for the
    user, without a traceback in the log)."""

class ScanSettingsError(ScanFailed, ValueError):
    """V22.8: settings that can not be scanned (ScanConfig.check)."""

class NoDongle(ScanFailed, AttributeError):
    """V22.9 (external audit P3-15): something that needs the open dongle
    (the step of the tiles needs its real sample rate) without one. An
    AttributeError too, so ``getattr(scanner, ..., default)`` still works."""

class ScannerBase(JSONMixin):
    WINDOW_TYPES = WINDOW_TYPES
    def __init__(self, **kwargs):
        self._running = threading.Event()
        self._stopped = threading.Event()
        self.stop_requested = False     # V21.1: set by request_stop()
        # V22.8 (external audit A6): Stop while the dongle is being opened
        # (~1.6 s on the PC) is kept; the lock makes "check Stop, then run"
        # one step against request_stop()
        self._stop_lock = threading.Lock()
        self._run_begun = False
        self.stopped_early = False      # Stop came before the first tile
        self._current_freq = None
        self._progress = 0.
        # V22.0 (audit 16.11, items 1-2): no settings given -> the tested
        # defaults of scanner/config.py. Before, the settings of the last
        # scan were read from the history database, so the result of a scan
        # depended on what had been scanned before.
        ckwargs = kwargs.get('config')
        if isinstance(ckwargs, ScanConfig):
            self.config = ckwargs
        else:
            self.config = ScanConfig(ckwargs or {})
        # V22.0: where a finished scan is saved. 'default' = the history of
        # the program (utils/dbstore.py); None = not saved (tools, tests).
        self.history = kwargs.get('history', 'default')
        # V22.1: what happens during the scan (wwb_scanner/events.py)
        self.events = kwargs.get('events') or events.bus
        self.device_config = self.config.device
        self.sampling_config = self.config.sampling
        if 'spectrum' in kwargs:
            self.spectrum = Spectrum.from_json(kwargs['spectrum'])
        else:
            self.spectrum = Spectrum()
        self.spectrum.scan_config = self.config
        if not kwargs.get('__from_json__'):
            self.sample_collection = SampleCollection(scanner=self)
    @property
    def current_freq(self):
        return self._current_freq
    @current_freq.setter
    def current_freq(self, value):
        self._current_freq = value
        if value is not None:
            f_min, f_max = self.config.scan_range
            # V18.1 (audit 2.2): a range of one point
            self.progress = (value - f_min) / (f_max - f_min) if f_max > f_min else 0.
        self.on_current_freq(value)
    def on_current_freq(self, value):
        pass
    @property
    def progress(self):
        return self._progress
    @progress.setter
    def progress(self, value):
        if value == self._progress:
            return
        self._progress = value
        self.on_progress(value)
        self.events.emit('progress', value=value)
    def on_progress(self, value):
        pass
    @property
    def segments(self):
        """V18: the ranges of this scan, [(lo, hi)] MHz. Several when
        config.scan_ranges lists them (bands of the chosen systems),
        otherwise the one scan_range."""
        from wwb_scanner import bands
        rs = bands.parse_ranges(self.config.get('scan_ranges'))
        if rs:
            segs, outside = bands.scan_segments(rs)
            if len(segs) > 1:
                return segs
            if len(segs) == 1:
                return [segs[0]]
        lo, hi = self.config.scan_range
        return [(float(lo), float(hi))]
    @property
    def multi_range(self):
        return len(self.segments) > 1
    def range_text(self):
        segs = self.segments
        if len(segs) == 1:
            return '%.1f-%.1f MHz' % segs[0]
        return '%d ranges (%s MHz)' % (len(segs), ', '.join('%g-%g' % s for s in segs))
    def build_sample_sets(self):
        try:
            segs = self.segments
        except AttributeError:      # a bare object in old tests
            segs = []
        if len(segs) > 1:
            return self._build_multi_range(segs)
        freq,  end_freq = self.config.scan_range
        # V20.8: say what is wrong with the range instead of an empty scan
        # (End below Start) or 'cannot convert float NaN to integer'
        try:
            lo_ok, hi_ok = float(freq), float(end_freq)
        except (TypeError, ValueError):
            lo_ok = hi_ok = float('nan')
        if not (np.isfinite(lo_ok) and np.isfinite(hi_ok)):
            raise ValueError('the scan range must be two numbers in MHz (got %r - %r)' % (freq, end_freq))
        if hi_ok <= lo_ok:
            raise ValueError('End frequency %g MHz must be above Start frequency %g MHz' % (hi_ok, lo_ok))
        sample_collection = self.sample_collection
        # Centre frequencies are counted in whole Hz. Adding a float step
        # again and again (in MHz) piles up rounding errors, and then the
        # same frequency measured by two tiles no longer looks the same.
        step_hz = int(round(mhz_to_hz(self.step_size)))
        if step_hz <= 0:
            raise ValueError('tile step must be above 0 (check Overlap Ratio)')
        freq_hz = int(round(mhz_to_hz(freq)))
        end_hz = int(round(mhz_to_hz(end_freq)))
        while freq_hz <= end_hz:
            if freq_hz > MAX_TUNER_FREQ_HZ:
                if end_hz > MAX_TUNER_FREQ_HZ:
                    # only when the user asked for more (shown in the window)
                    logger.warning('The dongle reaches only %.0f MHz: scan ends there',
                                   MAX_TUNER_FREQ_HZ / 1e6)
                break
            sample_set = sample_collection.build_sample_set(float(freq_hz))
            freq_hz += step_hz
    def _build_multi_range(self, segs):
        """V18: tiles for several ranges. All tile centres lie on one grid
        that starts at the first range, so the tile ripple has the same
        phase everywhere (clean scan works as for one range). Tiles in
        the gaps between the ranges are not measured."""
        self.config.scan_range = [segs[0][0], segs[-1][1]]
        step_hz = int(round(mhz_to_hz(self.step_size)))
        if step_hz <= 0:
            raise ValueError('tile step must be above 0 (check Overlap Ratio)')
        g0 = int(round(mhz_to_hz(segs[0][0])))
        centres = set()
        for lo, hi in segs:
            k0 = (int(round(mhz_to_hz(lo))) - g0) // step_hz
            k1 = (int(round(mhz_to_hz(hi))) - g0) // step_hz
            for k in range(k0, k1 + 1):
                centres.add(g0 + k * step_hz)
        for c in sorted(centres):
            if c > MAX_TUNER_FREQ_HZ:
                logger.warning('The dongle reaches only %.0f MHz: scan ends there',
                               MAX_TUNER_FREQ_HZ / 1e6)
                break
            self.sample_collection.build_sample_set(float(c))
        logger.info('scan of %s: %d tiles', self.range_text(), len(centres))
    def _begin_run(self):
        """V22.8: the start of a run. The same Scanner run again starts with
        a clean Stop state (before, ``_stopped`` stayed set for ever)."""
        with self._stop_lock:
            if self._stopped.is_set():
                self._stopped.clear()
                self.stop_requested = False
                self.stopped_early = False
            self._run_begun = True
    def _set_running(self, running):
        """V22.8 (audit A6): ``running.set()`` unless Stop came first (then
        False). One step under the lock, so a Stop in between is not lost."""
        with self._stop_lock:
            if self.stop_requested:
                return False
            running.set()
            return True
    def _stopped_before_start(self):
        logging.getLogger('wwb_scanner.status').info(
            'Scan %s stopped before the first tile: nothing measured', self.range_text())
    def run_scan(self):
        """Never raises: an error ends the scan (what was measured is kept),
        so the window does not stay in the "running" state."""
        self._begin_run()
        running = self._running
        self.error = None
        try:
            self._check_ranges()
            self._run_scan(running)
        except Exception as exc:
            self.error = exc
            # shown in the window (status line + dialog) and in the log file
            logger.error('Scan failed: %s', exc, exc_info=not isinstance(exc, ScanFailed))
            events.console('!!! Scan failed: %r' % (exc,))
        finally:
            running.clear()
            self.sample_collection.stopped.set()
            self._stopped.set()
    def _check_ranges(self):
        """V22.8 (external audit B7): the core checks the limits of the dongle
        itself (the window and the service do it too); other problems of the
        range are named by build_sample_sets as before."""
        why = self.config.out_of_range() or self.config.device_problem()
        if why:
            raise ScanSettingsError(why)
        for text in self.config.warnings():     # V22.9 (external audit P3-14)
            logging.getLogger('wwb_scanner.status').warning(text)
    def _nothing_measured(self, coll):
        """V22.8 (external audit B7): a scan without a single measured point
        is an error, not a finished scan (it was saved as one)."""
        why = getattr(coll, 'abort_reason', '') or ''
        if why:
            return ScanFailed('The scan measured nothing: %s' % why)
        if getattr(coll, 'aborted', False):
            return ScanFailed('The scan measured nothing: the dongle stopped answering. Unplug it, '
                              'plug it back in and start again')
        return ScanFailed('The scan measured nothing: no tile gave data (tuner not locked or USB '
                          'errors, see the log). Unplug the dongle, plug it back in and start again')
    def _run_scan(self, running):
        t_start = time.time()
        self.build_sample_sets()
        self.events.emit('scan_started', name=self.spectrum.name, ranges=[list(s) for s in self.segments],
                         tiles=len(self.sample_collection.sample_sets))
        if not self._set_running(running):
            self._stopped_before_start()
            return
        self.sample_collection.scan_all_freqs()
        self.sample_collection.stopped.wait()
        # V21.1 (audit B5): a scan ended by Stop is finished like any other
        # (cropped to the range, cleaned, saved to the history) - it was left
        # as a raw piece that reached beyond the range and could be exported.
        stopped_by_user = bool(self.stop_requested)
        if stopped_by_user and (self.config.get('gain_calibration') or
                                not self.spectrum.sample_data.size):
            logging.getLogger('wwb_scanner.status').info(
                'Scan %s stopped%s', self.range_text(),
                '' if self.spectrum.sample_data.size else ' before the first tile: nothing measured')
            return
        if not self.spectrum.sample_data.size:
            raise self._nothing_measured(self.sample_collection)
        if running.is_set() or stopped_by_user:
            t0 = time.time()
            events.console('scan finished, cropping...')
            self.crop_spectrum()
            clean_note = ''
            try:
                clean_note = self.apply_cleanup()
            except Exception:
                logger.exception('clean scan failed, the scan is kept as measured')
            if self.history is not None:
                events.console('cropped in %.1f s, saving...' % (time.time() - t0))
                self.save_to_dbstore()
                events.console('saved, total %.1f s' % (time.time() - t0))
            what = self.range_text()
            coll = self.sample_collection
            if stopped_by_user and not getattr(coll, 'aborted', False):
                done = len(getattr(coll, 'processed_keys', ()))
                total = len(coll.sample_sets)
                sd = self.spectrum.sample_data
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %s stopped after %.0f s at %.1f MHz (%d of %d tiles), measured part kept%s',
                    what, time.time() - t_start,
                    float(np.max(sd['frequency'])) if sd.size else 0., done, total, clean_note)
            elif getattr(coll, 'aborted', False):
                # V22.8: the reason when the scan knows it (the gain did not come back)
                reason = getattr(coll, 'abort_reason', '') or 'dongle error'
                logging.getLogger('wwb_scanner.status').warning(
                    'Scan %s stopped after %.0f s (%s), measured part kept',
                    what, time.time() - t_start, reason)
            elif getattr(coll, 'missing_tiles', None):
                # V22.8 (external audit B7): a gap in the scan is a warning
                # (it was an info line): WWB shows a straight line there
                logging.getLogger('wwb_scanner.status').warning(
                    'Scan %s done in %.0f s, %d tile(s) without data: the scan has a gap at %s MHz',
                    what, time.time() - t_start, len(coll.missing_tiles),
                    ', '.join('%.1f' % (k / 1e6) for k in sorted(coll.missing_tiles)[:4]) +
                    (' ...' if len(coll.missing_tiles) > 4 else ''))
            else:
                # V20: a band that lost sensitivity next to a strong signal is
                # named in the last message (it stays in the status line)
                note = getattr(coll, 'overload_note', '')
                st = logging.getLogger('wwb_scanner.status')
                (st.warning if note and getattr(coll, 'overload_warn', False) else st.info)(
                    'Scan %s done in %.0f s%s%s', what, time.time() - t_start, clean_note,
                    ('; ' + note) if note else '')
    def request_stop(self):
        """V21.1 (audit B4): ask the scan to end and return at once. The scan
        thread finishes the tile it is reading (a hanging USB read: up to
        READ_TIMEOUT), then crops, cleans and saves what was measured.
        Returns False when no scan is running.
        V22.8 (external audit A6): a Stop while the scan is starting (the
        dongle is being opened, ~1.6 s on the PC) is kept, and the scan ends
        before its first tile; before, it was lost and the whole range was
        scanned."""
        with self._stop_lock:
            if self._running.is_set():
                self.stop_requested = True
                self._running.clear()
                self.sample_collection.scanning.clear()     # as cancel(), without waiting
                return True
            if self._run_begun and not self._stopped.is_set():
                self.stop_requested = True
                self.stopped_early = True
                self.sample_collection.scanning.clear()
                logger.info('Stop while the scan is starting: it ends before the first tile')
                return True
            return False
    def stop_scan(self):
        """Stop and wait until the scan thread is done (scripts, tests)."""
        if not self.request_stop():
            # not started yet or already finished: nothing to wait for
            # (waiting here used to hang forever before the first scan)
            return
        self._stopped.wait()
    def apply_cleanup(self):
        """V17 clean scan (tile ripple, own spurs, floor shape). The values as
        measured are kept in spectrum.raw_dbFS (saved with the scan).
        Returns a short note for the status line."""
        pc = self.config.processing
        want_r = bool(pc.get('clean_ripple'))
        want_s = bool(pc.get('clean_spurs'))
        want_f = bool(pc.get('clean_floor'))
        if not (want_r or want_s or want_f):
            return ''
        from wwb_scanner.scanner import cleanup
        spectrum = self.spectrum
        sd = spectrum.sample_data
        if sd.size < 50:
            return ''
        f = np.asarray(sd['frequency'], dtype=np.float64)
        y = np.asarray(sd['dbFS'], dtype=np.float64)
        bin_mhz = float(np.median(np.diff(f)))
        period = self.step_size / bin_mhz if bin_mhz > 0 else 0
        if abs(period - round(period)) > 0.01:
            want_r = False
        period = int(round(period))
        profile, why = None, ''
        spur_profile = None
        if want_f:
            profile, why = cleanup.find_profile(self.gain, self.sample_rate, period)
        elif want_s:
            # V20.9: how strong the own spurs are without antenna (hiding limit)
            spur_profile = cleanup.find_profile(self.gain, self.sample_rate, period)[0]
        default = None
        if abs(self.sample_rate - cleanup.DEFAULT_RIPPLE_FS) < 1 and \
                abs(self.step_size * 1e6 - cleanup.DEFAULT_RIPPLE_STEP_HZ) < 1:
            default = cleanup.DEFAULT_RIPPLE_DB
        start = float(self.config.scan_range[0])
        yc, info = cleanup.clean(f, y, start, period, profile=profile, ripple=want_r,
                                 spurs=want_s, flatten=want_f, default_ripple=default,
                                 spur_profile=spur_profile)
        serial = getattr(getattr(self, 'sdr_wrapper', None), 'serial', None)
        if serial is not None:
            info['dongle'] = serial         # V22.4: whose calibrations were used
        if profile is not None:
            info['calibration'] = profile.get('meta', {}).get('created')
            if profile.get('leaks'):
                # V20.9: outside signals found in the calibration, left out of the floor shape
                info['calibration_leaks'] = profile['leaks']
        if why:
            info['floor_skipped'] = why
        with spectrum.data_update_lock:
            spectrum.raw_dbFS = y.copy()
            spectrum.cleanup_info = info
            sd.data['dbFS'] = yc
            sd.data['magnitude'] = cleanup.from_db(yc)
        spectrum.set_data_updated()
        parts = []
        if info.get('spurs'):
            parts.append('%d own spurs hidden' % len(info.get('spurs_removed') or []))
            kept = info.get('spurs_kept') or []
            if kept:
                # V20.9: too strong for an own spur - may be a transmitter
                parts.append('kept on the spur grid: %s MHz' % ', '.join(
                    '%.3f (+%.0f dB)' % (k[0], k[1]) for k in kept[:4]) +
                    (' and %d more' % (len(kept) - 4) if len(kept) > 4 else ''))
        if info.get('ripple'):
            parts.append('tile ripple')
        if info.get('flatten'):
            parts.append('floor levelled')
        note = ' (clean: %s)' % ', '.join(parts) if parts else ''
        if why:
            note += '; floor not levelled: %s' % why
        logger.info('clean scan: %s', info)
        return note
    def crop_spectrum(self):
        # tiles reach about 1.2 MHz beyond the requested range
        f_min, f_max = self.config.scan_range
        self.spectrum.crop(f_min, f_max)
        segs = self.segments
        if len(segs) > 1:
            # V18: nothing between the ranges (tile edges reach into the gaps)
            self.spectrum.keep_ranges(segs)
    def save_to_dbstore(self):
        """The eid of the saved scan; None when it is not saved. V22.9
        (external audit P3-03): a history that can not be written (locked by
        another program, the file held by an antivirus) is a warning - the
        scan stays in the window and can be exported."""
        if self.history is None:
            return None
        self.history_error = None
        try:
            return self.spectrum.save_to_dbstore(None if self.history == 'default' else self.history)
        except Exception as exc:
            self.history_error = exc
            logger.debug('saving to the history failed', exc_info=True)
            logging.getLogger('wwb_scanner.status').warning(
                'The scan is not saved to the history (%s). It stays in the window: export it '
                'if you need it', exc)
            return None
    def _serialize(self):
        d = dict(
            config=self.config._serialize(),
            spectrum=self.spectrum._serialize(),
            sample_collection=self.sample_collection._serialize(),
        )
        return d
    def _deserialize(self, **kwargs):
        data = kwargs.get('sample_collection')
        self.sample_collection = SampleCollection.from_json(data, scanner=self)

def keep_awake(on):
    """V20.5.2: Windows must not go to sleep during a scan. On the user's
    laptop two unattended QA runs had one tile "waiting" 458 s and 212 s
    with no USB timeout firing - what a sleeping computer looks like (the
    process is frozen, the clock goes on); after waking the USB failed.
    SetThreadExecutionState works per thread: call it in the scan thread.
    Does nothing on other systems."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        logger.exception('keep_awake')
        return False


class Scanner(ScannerBase):
    '''
        params:
            scan_range: (list) frequency range to scan (in MHz)
            step_size:  increment (in MHz) to return scan values
    '''
    def __init__(self, **kwargs):
        super(Scanner, self).__init__(**kwargs)
        self.sdr_wrapper = SdrWrapper(scanner=self)
        # V18.1 (audit 1.1): the gain used to be rounded to the dongle's
        # gain table here, which opened the dongle in the window thread
        # (no timeout: a stuck USB froze the window). Now the table is
        # read when the scan opens the dongle (SdrWrapper.open_sdr).
    @property
    def sdr(self):
        """The pyrtlsdr object of the open dongle (or None)."""
        return self.sdr_wrapper.sdr
    @property
    def receiver(self):
        """V22.3: the open receiver (wwb_scanner/receivers), or None. The
        scanner core talks to the dongle only through it."""
        return self.sdr_wrapper.receiver
    @property
    def sample_rate(self):
        return self.sampling_config.get('sample_rate')
    @sample_rate.setter
    def sample_rate(self, value):
        self.sampling_config.sample_rate = value
    @property
    def freq_correction(self):
        return self.device_config.get('freq_correction')
    @freq_correction.setter
    def freq_correction(self, value):
        self.device_config.freq_correction = value
    @property
    def sweeps_per_scan(self):
        return self.sampling_config.sweeps_per_scan
    @sweeps_per_scan.setter
    def sweeps_per_scan(self, value):
        self.sampling_config.sweeps_per_scan = value
    @property
    def samples_per_sweep(self):
        return self.sampling_config.samples_per_sweep
    @samples_per_sweep.setter
    def samples_per_sweep(self, value):
        self.sampling_config.samples_per_sweep = value
    @property
    def step_size(self):
        c = self.sampling_config
        overlap = c.sweep_overlap_ratio
        # V22.9 (external audit P3-15): computed again when the settings it
        # comes from change (before, the first value stayed for ever)
        key = (c.sample_rate, overlap, self.window_size, tile_keep(c))
        step_size = getattr(self, '_step_size', None)
        if step_size is not None and getattr(self, '_step_key', None) == key:
            return step_size
        rx = self.receiver
        if rx is None:
            raise NoDongle('the dongle is not open: the step of the tiles needs its sample rate')
        self._equal_spacing = None
        rx.sample_rate = c.sample_rate
        rs = int(round(rx.sample_rate))

        self.sample_rate = rs
        nfft = self.window_size
        resolution = get_freq_resolution(nfft, rs)

        step_size = self._step_size = rs / 2. * overlap
        # Each tile keeps +-KEEP_RATIO*rs around its centre but not +-GAP at
        # the centre (DC spike). The neighbour tile must cover that gap, so
        # the step may not exceed KEEP*rs - GAP (0.9 MHz at 2.4 MS/s);
        # Overlap Ratio 0.8 gave holes next to the tile centres.
        keep_ratio, center_gap = tile_keep(c)
        max_step = ((keep_ratio * rs - center_gap) // resolution) * resolution
        if step_size > max_step:
            logger.warning('Overlap Ratio %s gives holes in the scan; step limited to %.3f MHz',
                           overlap, max_step / 1e6)
            step_size = self._step_size = max_step
        if step_size < resolution:
            # Overlap Ratio 0 gave step 0: the tile list never ended (hang)
            logger.warning('Overlap Ratio %s is too small; step set to %.4f MHz',
                           overlap, resolution / 1e6)
            step_size = self._step_size = resolution
        self._equal_spacing = is_equal_spacing(nfft, rs, step_size)
        if not self.equal_spacing:
            step_size -= step_size % resolution
            step_size = round(step_size)
            if step_size <= 0:
                step_size += resolution
            self._equal_spacing = is_equal_spacing(nfft, rs, step_size)

        step_size = hz_to_mhz(step_size)
        self._step_size = step_size
        # the key of the settings as they are now (the dongle's real rate is in them)
        self._step_key = (c.sample_rate, overlap, self.window_size, tile_keep(c))
        logger.info(f'step_size: {step_size!r}, equal_spacing: {self._equal_spacing}')
        return step_size
    @property
    def equal_spacing(self):
        r = getattr(self, '_equal_spacing', None)
        if r is not None:
            return r
        _ = self.step_size
        return self._equal_spacing
    @property
    def window_size(self):
        c = self.config
        w = c.sampling.get('window_size')
        if not w:
            # not set: 25 kHz bins, like the UI default (None used to stop the scan)
            rs = c.sampling.get('sample_rate') or 2.4e6
            w = int(round(rs / 25e3))
        return w
    @window_size.setter
    def window_size(self, value):
        if value == self.sampling_config.get('window_size'):
            return
        self.sampling_config.window_size = value
    @property
    def gain(self):
        return self.device_config.get('gain')
    @gain.setter
    def gain(self, value):
        # V18.1: round only with a gain table already read from the dongle;
        # never open the dongle from here
        if value is not None and getattr(self, '_gains', None):
            value = self.get_nearest_gain(value)
        self.device_config.gain = value
    def learn_gains(self, sdr):
        """V18.1: read the gain table of an open dongle and round the gain."""
        try:
            gains = [float(g) for g in as_receiver(sdr).gains()]
        except Exception:
            return
        if gains:
            self._gains = gains
            if self.gain is not None:
                self.gain = self.gain
    @property
    def gains(self):
        gains = getattr(self, '_gains', None)
        if gains is None:
            gains = self._gains = self.get_gains()
        return gains
    def get_gains(self):
        """The gain table of the dongle.
        V22.8 (external audit B2): the settings of the dongle are switched
        on again whatever happens (an error left them off for every later
        open), and a dongle the scan has open is only asked, never closed
        here (``with`` closed it in the middle of a scan)."""
        wrapper = self.sdr_wrapper
        if wrapper.sdr is not None:
            rx = self.receiver
            return None if rx is None else [float(g) for g in rx.gains()]
        wrapper.enable_scanner_updates = False
        try:
            with wrapper:
                rx = self.receiver
                gains = None if rx is None else [float(g) for g in rx.gains()]
        finally:
            wrapper.enable_scanner_updates = True
        return gains
    def get_nearest_gain(self, gain):
        gains = self.gains
        if gains is None:
            return gain
        npgains = np.array(gains)
        return gains[np.abs(npgains - gain).argmin()]
    def run_scan(self):
        # V18.1 (audit 1.2): a USB call of an earlier scan never returned.
        # Opening the dongle again would only hang another thread.
        from wwb_scanner.scanner.sample_processing import stuck_usb_calls
        from wwb_scanner.scanner import watchdog
        self._begin_run()
        self._refused = False
        self._open_error = None
        if stuck_usb_calls():
            self._refused = True
            return super(Scanner, self).run_scan()
        keep_awake(True)
        # V22.8 (external audit A7): a scan that stops moving leaves the
        # stacks of all threads in the log (from opening to closing the dongle)
        dog = watchdog.Watchdog('scan').start()
        try:
            try:
                # V22.8: not when Stop came already or the range is wrong
                # (the base class says why, audit B7); V22.9: nor with a gain,
                # sample rate or correction the dongle can not take (a gain
                # that was not a number became 0 dB when the dongle was set up)
                if not self.stop_requested and not self.config.out_of_range() and \
                        not self.config.device_problem():
                    try:
                        self.sdr_wrapper.open_sdr()
                    except Exception as exc:
                        # V22.8 (audit A1): the set-up failed; the dongle is closed
                        self._open_error = exc
                    if self.sdr is None and self._open_error is None and not self.stop_requested:
                        self._wait_for_dongle()
                super(Scanner, self).run_scan()
            finally:
                try:
                    self.sdr_wrapper.close_sdr()
                except Exception:
                    logger.exception('closing the dongle failed')
        finally:
            dog.stop()
            keep_awake(False)
    def _wait_for_dongle(self):
        """V20.4: just after a USB error the dongle is "not found or busy"
        for a few seconds. Try again for up to REOPEN_WAIT_S before failing.
        V22.8: Stop ends the waiting."""
        from wwb_scanner.scanner import sample_processing as sp
        events.console('dongle not found or busy, waiting up to %.0f s...' % sp.REOPEN_WAIT_S)
        deadline = time.monotonic() + sp.REOPEN_WAIT_S
        while self.sdr is None and time.monotonic() < deadline and not self.stop_requested:
            time.sleep(sp.REOPEN_PAUSE_S)
            try:
                self.sdr_wrapper.open_sdr()
            except Exception as exc:
                logger.warning('opening the dongle failed: %s', exc)
                self._open_error = exc
                break
        if self.sdr is not None:
            events.console('dongle found')
    def _run_scan(self, running):
        if getattr(self, '_refused', False):
            raise IOError('The dongle stopped answering in the last scan. Unplug it, plug it back '
                          'in and start again; if this message comes back, restart the program')
        if self.stop_requested:
            self._stopped_before_start()          # V22.8 (audit A6)
            return
        if self.sdr is None:
            if self._open_error is not None:
                raise ScanFailed('The dongle could not be set up (%s). Check Device Settings '
                                 '(sample rate), unplug the dongle, plug it back in and start '
                                 'again' % (self._open_error,))
            raise IOError('RTL-SDR dongle not found or busy (unplug/replug it, '
                          'close other SDR programs)')
        # V22.7: own gain per band (gain_plan.py): a short probe first
        self.gain_plan = None
        if self._band_gain_wanted():
            if not self._set_running(running):  # Stop works during the probe
                self._stopped_before_start()
                return
            st = logging.getLogger('wwb_scanner.status')
            st.info('Choosing the gain of every band (short probe)...')
            try:
                self.gain_plan = self.make_gain_plan()
            except Exception:
                logger.exception('gain plan failed: one gain for the whole scan')
            # V22.9 (external audit AUD-05): a USB error in the probe closed
            # the dongle - opened anew before the scan uses it
            if not self.stop_requested and self._usable_receiver() is None:
                raise ScanFailed('the dongle stopped answering during the gain probe and could not be '
                                 'opened again: nothing measured. Unplug the dongle, plug it back in '
                                 'and start again')
            if self.stop_requested:
                st.info('Scan %s stopped during the gain probe: nothing measured', self.range_text())
                return
            if self.gain_plan is None or not self.gain_plan.banded:
                # V22.8 (audit B2): one gain for the whole scan - it must be the
                # gain of the scan again, whatever the probe left set
                from wwb_scanner.scanner.sample_processing import set_gain_back
                rx = self.receiver
                if getattr(rx, 'is_open', True) and \
                        not set_gain_back(None, rx, float(self.gain), 'the gain probe', stop=False):
                    raise ScanFailed('the gain could not be set back to %s dB after the gain probe: '
                                     'nothing measured. Unplug the dongle, plug it back in and start '
                                     'again' % self.gain)
        if self.gain_plan is not None and self.gain_plan.banded:
            self._run_banded(running, self.gain_plan)
        else:
            if self.gain_plan is not None:
                logging.getLogger('wwb_scanner.status').info(
                    'Own gain per band: one gain for the whole scan (%s)',
                    self.gain_plan.note or 'no band is quiet enough for more gain')
            super(Scanner, self)._run_scan(running)
        if self.config.get('gain_calibration') and running.is_set():
            # V20.5: the survey is done, the dongle is still open
            from wwb_scanner.scanner import gain_cal
            ok, text = gain_cal.calibrate(self, running)
            self.gain_cal_result = (ok, text)
            (logging.getLogger('wwb_scanner.status').info if ok else
             logging.getLogger('wwb_scanner.status').warning)(text)
    # ------------------------------------------------ V22.7: gain per band
    def _band_gain_wanted(self):
        try:
            return (bool(self.sampling_config.get('band_gain')) and
                    not self.config.get('gain_calibration'))
        except Exception:
            return False
    def make_gain_plan(self):
        """Probe the bands of this scan with the open dongle (gain_plan.py)."""
        from wwb_scanner.scanner import gain_plan, gain_cal, cleanup
        sr = self.sample_rate
        def floor_calibrated(g):
            return cleanup.find_profile(g, sr)[0] is not None
        # only gains that can be levelled, when the floor is levelled
        has_floor = floor_calibrated if self.config.processing.get('clean_floor') else None

        def drop(g_hi, g_lo, f_hz):
            return gain_cal.real_drop(g_hi, g_lo, f_hz, sample_rate=sr)
        rx = self.receiver
        try:
            g0 = float(rx.gain)
        except Exception:
            g0 = float(self.gain)
        return gain_plan.make_plan(rx, self.segments, g0, self.gains, sr, has_floor=has_floor,
                                   drop=drop, should_stop=lambda: bool(self.stop_requested))
    def _usable_receiver(self):
        """V22.9: the receiver, opened anew when a USB error closed it (None:
        the dongle can not be opened again)."""
        rx = self.receiver
        if rx is not None and getattr(rx, 'is_open', True):
            return rx
        from wwb_scanner.scanner.sample_processing import stuck_usb_calls
        wrapper = getattr(self, 'sdr_wrapper', None)
        if wrapper is None or stuck_usb_calls() or self.stop_requested:
            return None
        try:
            if wrapper.open_sdr() is None:
                return None
        except Exception:
            logger.warning('opening the dongle again failed', exc_info=True)
            return None
        events.console('dongle opened again after a USB error')
        return self.receiver
    def _band_gain_set(self, gain, ranges):
        """V22.9 (external audit AUD-02): set the gain of a group of bands on
        the receiver of now. When that fails, the dongle is opened anew once
        and the gain set again; when it still fails, the bands are "not
        measured" and the scan goes on with the next group."""
        from wwb_scanner.scanner.sample_processing import run_with_timeout, TUNE_TIMEOUT
        for attempt in (0, 1):
            rx = self._usable_receiver()
            if rx is None:
                break
            try:
                run_with_timeout(setattr, TUNE_TIMEOUT, rx, 'gain', gain)
                return True
            except Exception:
                logger.warning('setting the gain %.1f dB for %s failed', gain, ranges, exc_info=True)
                if attempt == 0 and getattr(rx, 'is_open', True):
                    # an error that left the dongle open: closed and opened anew
                    try:
                        self.sdr_wrapper.close_sdr()
                    except Exception:
                        logger.exception('closing the dongle after the error failed')
        self.bands_not_measured.extend([list(r) for r in ranges])
        return False
    def _run_banded(self, running, plan):
        """V22.7: every group of bands of one gain is scanned as a scan of its
        own (tiles, Overload, peak check, clean scan with the calibration of
        that gain), then the levels are brought to the gain of the scan and
        the bands are put together into this scan's spectrum."""
        from wwb_scanner.scanner import gain_plan, gain_cal
        t_start = time.time() - plan.seconds
        st = logging.getLogger('wwb_scanner.status')
        main = self.spectrum
        conf = self.config
        g0 = plan.ref_gain
        sr = self.sample_rate

        def drop(g_hi, g_lo, f_hz):
            return gain_cal.real_drop(g_hi, g_lo, f_hz, sample_rate=sr)
        saved = (list(conf.scan_range), conf.get('scan_ranges'))
        groups = sorted(plan.groups(), key=lambda gr: gr[1][0][0])
        step = float(self.step_size) or 1.
        total = sum(int((hi - lo) / step) + 1 for _, rs in groups for lo, hi in rs)
        self._banded = {'total': max(total, 1), 'done': 0}
        self.events.emit('scan_started', name=main.name, ranges=[list(x) for x in self.segments],
                         tiles=total)
        st.info('Gain per band: %s (probe %.0f s)', plan.text(), plan.seconds)
        parts, infos, colls = [], [], []
        timing = None
        self.bands_not_measured = []
        try:
            for gain, ranges in groups:
                if self.stop_requested:
                    break
                # V22.9 (external audit AUD-02 / P3-02): the receiver of now -
                # after a USB error in the band before, the dongle was opened
                # anew and the old one is closed
                if not self._band_gain_set(gain, ranges):
                    continue
                self.device_config.gain = gain
                conf.scan_range = [ranges[0][0], ranges[-1][1]]
                conf.scan_ranges = [list(r) for r in ranges] if len(ranges) > 1 else None
                band = Spectrum(name=main.name, step_size=main.step_size)
                self.spectrum = band
                self.sample_collection = SampleCollection(scanner=self)
                # V22.9 (external audit AUD-19): one time report for all bands
                if timing is None:
                    timing = getattr(self.sample_collection, 'timing', None)
                elif hasattr(self.sample_collection, 'timing'):
                    self.sample_collection.timing = timing
                self.build_sample_sets()
                self._live = (main, gain, g0, drop)
                if not self._set_running(running):     # V22.8: one step with Stop
                    break
                self.sample_collection.scan_all_freqs()
                self.sample_collection.stopped.wait()
                self._live = None
                colls.append(self.sample_collection)
                self._banded['done'] += len(self.sample_collection.sample_sets)
                if not band.sample_data.size:
                    continue
                self.crop_spectrum()
                try:
                    self.apply_cleanup()
                except Exception:
                    logger.exception('clean scan of %s failed, the band is kept as measured', ranges)
                sd = band.sample_data
                f = np.asarray(sd['frequency'], dtype=np.float64)
                y = np.asarray(sd['dbFS'], dtype=np.float64)
                raw = getattr(band, 'raw_dbFS', None)
                raw = y.copy() if raw is None or np.size(raw) != y.size else np.asarray(raw, dtype=np.float64)
                sh = gain_plan.level_shift(f, gain, g0, drop)
                parts.append((f, y - sh, raw - sh))
                info = dict(getattr(band, 'cleanup_info', None) or {})
                info.update(ranges=[list(r) for r in ranges], gain=gain,
                            level_shift_db=[round(float(sh.min()), 2), round(float(sh.max()), 2)])
                infos.append(info)
                self._put_bands(main, parts)
        finally:
            self._live = None
            self.spectrum = main
            conf.scan_range, conf.scan_ranges = saved
            self.device_config.gain = g0
            from wwb_scanner.scanner.sample_processing import set_gain_back
            rx = self.receiver
            if rx is not None and getattr(rx, 'is_open', True):
                set_gain_back(None, rx, g0, 'the scan of the bands', stop=False)
        self._merge_band_collections(colls)
        for lo, hi in self.bands_not_measured:
            st.warning('Gain per band: %.3f-%.3f MHz not measured (dongle error)', lo, hi)
        stopped_by_user = bool(self.stop_requested)
        if not parts:
            if not stopped_by_user:
                # V22.8 (external audit B7): nothing measured is an error
                raise self._nothing_measured(colls[-1] if colls else self.sample_collection)
            st.info('Scan %s stopped before the first tile: nothing measured', self.range_text())
            return
        info = self._put_bands(main, parts, infos, plan)
        if self.history is not None:
            self.save_to_dbstore()
        coll = self.sample_collection
        what = self.range_text()
        note = ' (gain per band: %s%s)' % (plan.text(), self._clean_note(info))
        if stopped_by_user:
            st.info('Scan %s stopped after %.0f s, measured part kept%s', what, time.time() - t_start, note)
        elif getattr(coll, 'aborted', False):
            st.info('Scan %s stopped after %.0f s (dongle error), measured part kept', what,
                    time.time() - t_start)
        else:
            over = getattr(coll, 'overload_note', '')
            (st.warning if over and getattr(coll, 'overload_warn', False) else st.info)(
                'Scan %s done in %.0f s%s%s', what, time.time() - t_start, note,
                ('; ' + over) if over else '')
    def _put_bands(self, main, parts, infos=None, plan=None):
        """The finished bands into the scan's spectrum (levels at the gain of
        the scan). With ``infos``: also the raw values and what the clean
        scan did (cleanup_info, with the gain of every band)."""
        from wwb_scanner.scan_objects.samplearray import SampleArray
        f = np.concatenate([p[0] for p in parts])
        y = np.concatenate([p[1] for p in parts])
        raw = np.concatenate([p[2] for p in parts])
        order = np.argsort(f, kind='stable')
        f, y, raw = f[order], y[order], raw[order]
        keep = np.concatenate([[True], np.diff(f) > 1e-9])     # a band edge measured twice
        f, y, raw = f[keep], y[keep], raw[keep]
        info = None
        if infos is not None:
            info = merge_band_infos(infos, plan)
            serial = getattr(getattr(self, 'sdr_wrapper', None), 'serial', None)
            if serial is not None:
                info['dongle'] = serial
        with main.data_update_lock:
            main.sample_data = SampleArray.create(frequency=f, dbFS=y)
            if info is not None:
                main.raw_dbFS = raw
                main.cleanup_info = info
        main.set_data_updated()
        return info
    def _merge_band_collections(self, colls):
        """ScanResult and the window read the tile collection: the last one
        gets the tiles and notes of all bands."""
        if not colls:
            return
        last = colls[-1]
        sets, done = {}, set()
        notes, missing, images, checks = [], [], [], []
        warn = False
        for c in colls:
            sets.update(c.sample_sets)
            done |= set(getattr(c, 'processed_keys', ()) or ())
            n = getattr(c, 'overload_note', '')
            if n:
                notes.append(n)
            warn = warn or bool(getattr(c, 'overload_warn', False))
            missing += list(getattr(c, 'missing_tiles', []) or [])
            images += list(getattr(c, 'images', []) or [])
            checks += list(getattr(c, 'peak_checks', []) or [])
        last.sample_sets = sets
        last.processed_keys = done
        last.overload_note = '; '.join(notes)
        last.overload_warn = warn
        last.missing_tiles = missing
        last.images = images
        last.peak_checks = checks
        self.sample_collection = last
    @staticmethod
    def _clean_note(info):
        parts = []
        if info.get('spurs'):
            parts.append('%d own spurs hidden' % len(info.get('spurs_removed') or []))
        if info.get('ripple'):
            parts.append('tile ripple')
        if info.get('flatten'):
            parts.append('floor levelled')
        note = '; clean: %s' % ', '.join(parts) if parts else ''
        if info.get('floor_skipped'):
            note += '; floor not levelled: %s' % info['floor_skipped']
        return note
    def _tile_kwargs(self, sample_set):
        weight = 1.0
        bw = getattr(sample_set, 'bin_weight', None)
        if bw is not None and np.size(bw) == np.size(sample_set.powers):
            # V20: bins that only know the low-gain floor count where no
            # other tile has a real measurement
            weight = np.asarray(bw, dtype=np.float64)
        elif getattr(sample_set, 'gain_drop', 0.) > 0:
            # V17: only a low-gain reading (V20 merges both readings: weight 1)
            from wwb_scanner.scanner.sample_processing import REMEASURED_WEIGHT
            weight = REMEASURED_WEIGHT
        return dict(
            frequency=sample_set.frequencies,
            magnitude=sample_set.powers,
            center_frequency=sample_set.center_frequency,
            force_lower_freq=bool(self.equal_spacing),
            weight=weight,
        )
    def on_sample_set_processed(self, sample_set):
        kw = self._tile_kwargs(sample_set)
        self.spectrum.add_sample_set(**kw)
        live = getattr(self, '_live', None)
        if live is not None:
            # V22.7: the band being scanned also shows in the scan's spectrum
            # (at the gain of the scan) while it is measured
            main, g, g0, drop = live
            try:
                sh = drop(g, g0, sample_set.center_frequency)
                kw2 = dict(kw)
                kw2['magnitude'] = np.asarray(kw['magnitude']) * (10. ** (-sh / 10.))
                main.add_sample_set(**kw2)
            except Exception:
                logger.exception('live view of the band')
        self.events.emit('tile', center_mhz=sample_set.center_frequency / 1e6,
                         points=int(np.size(kw['frequency'])))
        banded = getattr(self, '_banded', None)
        coll = self.sample_collection
        if banded is not None and live is not None:
            self.progress = min(1., (banded['done'] + len(getattr(coll, 'processed_keys', ()) or ()))
                                / float(banded['total']))
        else:
            self.progress = coll.calc_progress()
    def rebuild_spectrum(self):
        """V20: some tiles changed after they were added (read again next to
        a strong carrier): the spectrum is built again from all tiles."""
        coll = self.sample_collection
        done = coll.processed_keys
        tiles = [self._tile_kwargs(coll.sample_sets[k]) for k in sorted(coll.sample_sets)
                 if k in done and coll.sample_sets[k].powers is not None]
        t0 = time.time()
        self.spectrum.rebuild(tiles)
        logger.info('spectrum rebuilt from %d tiles in %.2f s', len(tiles), time.time() - t0)

def merge_band_infos(infos, plan=None):
    """V22.7: one cleanup_info for a scan of several bands (one per gain)."""
    out = {'bands': infos}
    if plan is not None:
        out['gain_plan'] = plan.as_list()
        out['ref_gain'] = plan.ref_gain
        out['gain_probe_s'] = round(plan.seconds, 1)
    for key in ('spurs_removed', 'spurs_kept', 'calibration_leaks'):
        vals = []
        for i in infos:
            vals += list(i.get(key) or [])
        if vals:
            out[key] = vals
    out['spurs'] = any(i.get('spurs') for i in infos)
    out['ripple'] = any(i.get('ripple') for i in infos)
    out['flatten'] = bool(infos) and all(i.get('flatten') for i in infos)
    cals = [i.get('calibration') for i in infos if i.get('calibration')]
    if cals:
        out['calibration'] = cals[0]
    skipped = ['%s: %s' % ('-'.join('%g' % v for v in i['ranges'][0]), i['floor_skipped'])
               for i in infos if i.get('floor_skipped')]
    if skipped:
        out['floor_skipped'] = '; '.join(skipped)
    return out


def scan_and_save(filename=None, frequency_format=None, **kwargs):
    """Scan and write the result to a file (.csv, .sdb2, .sdb3 or .npz);
    not saved in the history unless history= is given."""
    if not filename:
        raise ValueError('scan_and_save: give a file name (.csv, .sdb2, .sdb3 or .npz)')
    kwargs.setdefault('history', None)
    scanner = Scanner(**kwargs)
    scanner.run_scan()
    scanner.spectrum.export_to_file(filename=filename, frequency_format=frequency_format)
    return scanner
