import threading
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
        logger.error(f'!!! Not unique: {r}')
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
            self.progress = (value - f_min) / (f_max - f_min)
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
    def build_sample_sets(self):
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
            print('cropped in %.1f s, saving...' % (time.time() - t0), flush=True)
            self.save_to_dbstore()
            print('saved, total %.1f s' % (time.time() - t0), flush=True)
            lo, hi = self.config.scan_range
            coll = self.sample_collection
            if getattr(coll, 'aborted', False):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %.1f-%.1f MHz stopped after %.0f s (dongle error), measured part kept',
                    lo, hi, time.time() - t_start)
            elif getattr(coll, 'missing_tiles', None):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %.1f-%.1f MHz done in %.0f s, %d tile(s) without data',
                    lo, hi, time.time() - t_start, len(coll.missing_tiles))
            else:
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %.1f-%.1f MHz done in %.0f s', lo, hi, time.time() - t_start)
    def stop_scan(self):
        if not self._running.is_set():
            # not started yet or already finished: nothing to wait for
            # (waiting here used to hang forever before the first scan)
            return
        self._running.clear()
        self.sample_collection.cancel()
        self._stopped.wait()
    def crop_spectrum(self):
        # tiles reach about 1.2 MHz beyond the requested range
        f_min, f_max = self.config.scan_range
        self.spectrum.crop(f_min, f_max)
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

class Scanner(ScannerBase):
    '''
        params:
            scan_range: (list) frequency range to scan (in MHz)
            step_size:  increment (in MHz) to return scan values
    '''
    def __init__(self, **kwargs):
        super(Scanner, self).__init__(**kwargs)
        self.sdr_wrapper = SdrWrapper(scanner=self)
        self.gain = self.gain
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
        if value is not None and hasattr(self, 'sdr_wrapper'):
            value = self.get_nearest_gain(value)
        self.device_config.gain = value
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
        with self.sdr_wrapper:
            super(Scanner, self).run_scan()
    def _run_scan(self, running):
        if self.sdr is None:
            raise IOError('RTL-SDR dongle not found or busy (unplug/replug it, '
                          'close other SDR programs)')
        super(Scanner, self)._run_scan(running)
    def on_sample_set_processed(self, sample_set):
        powers = sample_set.powers
        freqs = sample_set.frequencies
        spectrum = self.spectrum
        center_freq = sample_set.center_frequency
        if self.equal_spacing:
            force_lower_freq = True
        else:
            force_lower_freq = False
        spectrum.add_sample_set(
            frequency=freqs,
            magnitude=powers,
            center_frequency=center_freq,
            force_lower_freq=force_lower_freq,
        )
        self.progress = self.sample_collection.calc_progress()

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
    scanner = Scanner(**kwargs)
    scanner.run_scan()
    scanner.spectrum.show_plot()
    return scanner

def scan_and_save(filename=None, frequency_format=None, **kwargs):
    scanner = Scanner(**kwargs)
    scanner.run_scan()
    scanner.spectrum.export_to_file(filename=filename, frequency_format=frequency_format)
    return scanner
