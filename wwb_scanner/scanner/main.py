import threading
import sys
import time

import numpy as np

import logging
logger = logging.getLogger(__name__)

# The R828D tuner in the RTL-SDR Blog V4 cannot lock its PLL above this.
MAX_TUNER_FREQ_HZ = 1766000000

from wwb_scanner.core import JSONMixin
from wwb_scanner.utils.dbstore import db_store
from wwb_scanner.scanner.sdrwrapper import SdrWrapper
from wwb_scanner.scanner.config import ScanConfig
from wwb_scanner.scanner.sample_processing import (
    DEFAULT_TILE_KEEP_RATIO,
    DEFAULT_TILE_CENTER_GAP_HZ,
    SampleCollection,
    calc_num_samples,
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

class ScannerBase(JSONMixin):
    WINDOW_TYPES = WINDOW_TYPES
    def __init__(self, **kwargs):
        self._running = threading.Event()
        self._stopped = threading.Event()
        self._current_freq = None
        self._progress = 0.
        ckwargs = kwargs.get('config')
        if not ckwargs:
            ckwargs = db_store.get_scan_config()
        if not ckwargs:
            ckwargs = {}
        self.config = ScanConfig(ckwargs)
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
    def run_scan(self):
        """Never raises: an error ends the scan (what was measured is kept),
        so the window does not stay in the "running" state."""
        running = self._running
        self.error = None
        try:
            self._run_scan(running)
        except Exception as exc:
            self.error = exc
            # shown in the window (status line + dialog) and in the log file
            logger.error('Scan failed: %s', exc, exc_info=True)
            print('!!! Scan failed: %r' % (exc,), flush=True)
        finally:
            running.clear()
            self.sample_collection.stopped.set()
            self._stopped.set()
    def _run_scan(self, running):
        t_start = time.time()
        self.build_sample_sets()
        running.set()
        self.sample_collection.scan_all_freqs()
        self.sample_collection.stopped.wait()
        if running.is_set():
            t0 = time.time()
            print('scan finished, cropping...', flush=True)
            self.crop_spectrum()
            clean_note = ''
            try:
                clean_note = self.apply_cleanup()
            except Exception:
                logger.exception('clean scan failed, the scan is kept as measured')
            print('cropped in %.1f s, saving...' % (time.time() - t0), flush=True)
            self.save_to_dbstore()
            print('saved, total %.1f s' % (time.time() - t0), flush=True)
            what = self.range_text()
            coll = self.sample_collection
            if getattr(coll, 'aborted', False):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %s stopped after %.0f s (dongle error), measured part kept',
                    what, time.time() - t_start)
            elif getattr(coll, 'missing_tiles', None):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %s done in %.0f s, %d tile(s) without data',
                    what, time.time() - t_start, len(coll.missing_tiles))
            else:
                # V20: a band that lost sensitivity next to a strong signal is
                # named in the last message (it stays in the status line)
                note = getattr(coll, 'overload_note', '')
                st = logging.getLogger('wwb_scanner.status')
                (st.warning if note and getattr(coll, 'overload_warn', False) else st.info)(
                    'Scan %s done in %.0f s%s%s', what, time.time() - t_start, clean_note,
                    ('; ' + note) if note else '')
    def stop_scan(self):
        if not self._running.is_set():
            # not started yet or already finished: nothing to wait for
            # (waiting here used to hang forever before the first scan)
            return
        self._running.clear()
        self.sample_collection.cancel()
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
        if want_f:
            profile, why = cleanup.find_profile(self.gain, self.sample_rate, period)
        default = None
        if abs(self.sample_rate - cleanup.DEFAULT_RIPPLE_FS) < 1 and \
                abs(self.step_size * 1e6 - cleanup.DEFAULT_RIPPLE_STEP_HZ) < 1:
            default = cleanup.DEFAULT_RIPPLE_DB
        start = float(self.config.scan_range[0])
        yc, info = cleanup.clean(f, y, start, period, profile=profile, ripple=want_r,
                                 spurs=want_s, flatten=want_f, default_ripple=default)
        if profile is not None:
            info['calibration'] = profile.get('meta', {}).get('created')
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
            parts.append('%d own spurs removed' % len(info.get('spurs_removed') or []))
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
        self.spectrum.save_to_dbstore()
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
        return self.sdr_wrapper.sdr
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
        step_size = getattr(self, '_step_size', None)
        if step_size is not None:
            return step_size
        c = self.sampling_config
        overlap = c.sweep_overlap_ratio
        self.sdr.sample_rate = c.sample_rate
        rs = int(round(self.sdr.sample_rate))

        self.sample_rate = rs
        nfft = self.window_size
        resolution = get_freq_resolution(nfft, rs)

        step_size = self._step_size = rs / 2. * overlap
        # Each tile keeps +-KEEP_RATIO*rs around its centre but not +-GAP at
        # the centre (DC spike). The neighbour tile must cover that gap, so
        # the step may not exceed KEEP*rs - GAP (0.9 MHz at 2.4 MS/s);
        # Overlap Ratio 0.8 gave holes next to the tile centres.
        max_step = ((DEFAULT_TILE_KEEP_RATIO * rs - DEFAULT_TILE_CENTER_GAP_HZ) // resolution) * resolution
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
            gains = [g / 10. for g in sdr.get_gains()]
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
        self.sdr_wrapper.enable_scanner_updates = False
        with self.sdr_wrapper:
            sdr = self.sdr
            if sdr is None:
                gains = None
            else:
                gains = self.sdr.get_gains()
        self.sdr_wrapper.enable_scanner_updates = True
        if gains is not None:
            gains = [gain / 10. for gain in gains]
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
        if stuck_usb_calls():
            self._refused = True
            return super(Scanner, self).run_scan()
        keep_awake(True)
        try:
            with self.sdr_wrapper:
                if self.sdr is None:
                    self._wait_for_dongle()
                super(Scanner, self).run_scan()
        finally:
            keep_awake(False)
    def _wait_for_dongle(self):
        """V20.4: just after a USB error the dongle is "not found or busy"
        for a few seconds. Try again for up to REOPEN_WAIT_S before failing."""
        from wwb_scanner.scanner import sample_processing as sp
        if self.device_config.is_remote:
            return
        print('dongle not found or busy, waiting up to %.0f s...' % sp.REOPEN_WAIT_S, flush=True)
        deadline = time.monotonic() + sp.REOPEN_WAIT_S
        while self.sdr is None and time.monotonic() < deadline:
            time.sleep(sp.REOPEN_PAUSE_S)
            try:
                self.sdr_wrapper.open_sdr()
            except Exception:
                logger.exception('opening the dongle failed')
        if self.sdr is not None:
            print('dongle found', flush=True)
    def _run_scan(self, running):
        if getattr(self, '_refused', False):
            raise IOError('The dongle stopped answering in the last scan. Unplug it, plug it back '
                          'in and start again; if this message comes back, restart the program')
        if self.sdr is None:
            raise IOError('RTL-SDR dongle not found or busy (unplug/replug it, '
                          'close other SDR programs)')
        super(Scanner, self)._run_scan(running)
        if self.config.get('gain_calibration') and running.is_set():
            # V20.5: the survey is done, the dongle is still open
            from wwb_scanner.scanner import gain_cal
            ok, text = gain_cal.calibrate(self, running)
            self.gain_cal_result = (ok, text)
            (logging.getLogger('wwb_scanner.status').info if ok else
             logging.getLogger('wwb_scanner.status').warning)(text)
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
        self.spectrum.add_sample_set(**self._tile_kwargs(sample_set))
        self.progress = self.sample_collection.calc_progress()
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

class ThreadedScanner(threading.Thread, Scanner):
    def __init__(self, **kwargs):
        threading.Thread.__init__(self)
        Scanner.__init__(self, **kwargs)
        self.plot = kwargs.get('plot')
        self.run_once = kwargs.get('run_once', True)
        self.scan_wait_timeout = kwargs.get('scan_wait_timeout', 5.)
        self.scanning = threading.Event()
        self.waiting = threading.Event()
        self.stopping = threading.Event()
        self.stopped = threading.Event()
        self.need_update = threading.Event()
        self.need_update_lock = threading.Lock()
    def on_current_freq(self, value):
        if self.plot is not None:
            self.plot.update_plot()
        with self.need_update_lock:
            self.need_update.set()
    def run(self):
        scanning = self.scanning
        waiting = self.waiting
        stopping = self.stopping
        stopped = self.stopped
        scan_wait_timeout = self.scan_wait_timeout
        run_once = self.run_once
        run_scan = self.run_scan
        while True:
            if stopping.is_set():
                break
            scanning.set()
            run_scan()
            scanning.clear()
            if run_once:
                break
            waiting.wait(scan_wait_timeout)
        stopped.set()
    def stop(self):
        self.stopping.set()
        self.waiting.set()
        self.stopped.wait()

def scan_and_plot(**kwargs):
    # not used by the window (old command line helper)
    scanner = Scanner(**kwargs)
    scanner.run_scan()
    scanner.spectrum.show_plot()
    return scanner

def scan_and_save(filename=None, frequency_format=None, **kwargs):
    # not used by the window (command line helper)
    if not filename:
        raise ValueError('scan_and_save: give a file name (.csv, .sdb2, .sdb3 or .npz)')
    scanner = Scanner(**kwargs)
    scanner.run_scan()
    scanner.spectrum.export_to_file(filename=filename, frequency_format=frequency_format)
    return scanner
