import time
import threading
import numpy as np
from scipy.signal.windows import __all__ as WINDOW_TYPES
from scipy.signal import welch, get_window, hilbert

import logging
logger = logging.getLogger(__name__)

from wwb_scanner.core import JSONMixin
from wwb_scanner.scanner.scan_timing import ScanTiming

WINDOW_TYPES = [s for s in WINDOW_TYPES if s != 'get_window']

NPERSEG = 128

# Read every tile with one blocking call (True) or with the old async reader (False)
USE_SYNC_READ = True
# Samples thrown away after retuning (about 7 ms at 2.4 MS/s)
NUM_SAMPLES_DISCARDED = 16384

# Fraction of the sample rate on each side of the centre that is kept
# (0.39 -> about +-0.94 MHz of 2.4 MHz). RTL-SDR tile edges roll off by ~3 dB.
DEFAULT_TILE_KEEP_RATIO = 0.39
# Bins closer than this to the centre are dropped (RTL DC spike), in Hz.
DEFAULT_TILE_CENTER_GAP_HZ = 30000.

def next_2_to_pow(val):
    val -= 1
    val |= val >> 1
    val |= val >> 2
    val |= val >> 4
    val |= val >> 8
    val |= val >> 16
    return val + 1

def calc_num_samples(num_samples):
    return next_2_to_pow(int(num_samples))

def sort_psd(f, Pxx, onesided=False):
    return np.fft.fftshift(f), np.fft.fftshift(Pxx)

class SampleSet(JSONMixin):
    __slots__ = ('scanner', 'center_frequency', 'raw', 'current_sweep', 'complete',
                 '_frequencies', 'powers', 'collection', 'process_thread', 'samples_discarded')
    _serialize_attrs = ('center_frequency', '_frequencies', 'powers')
    def __init__(self, **kwargs):
        for key in self.__slots__:
            if key == '_frequencies':
                key = 'frequencies'
            setattr(self, key, kwargs.get(key))
        self.complete = threading.Event()
        self.samples_discarded = False
        if self.scanner is None and self.collection is not None:
            self.scanner = self.collection.scanner
    @property
    def frequencies(self):
        f = getattr(self, '_frequencies', None)
        if f is None:
            f = self._frequencies= self.calc_expected_freqs()
        return f
    @frequencies.setter
    def frequencies(self, value):
        self._frequencies = value
    @property
    def sweeps_per_scan(self):
        return self.scanner.sweeps_per_scan
    @property
    def samples_per_sweep(self):
        return self.scanner.samples_per_sweep
    @property
    def window_size(self):
        return getattr(self.scanner, 'window_size', NPERSEG)
    def _tick(self, stage):
        collection = self.collection
        timing = getattr(collection, 'timing', None)
        if timing is not None:
            timing.tick(self.center_frequency, stage)
    def tune(self, sdr, freq):
        """Set the centre frequency. A tuner that cannot lock must not
        kill the whole scan: retry once, then give up on this tile."""
        for attempt in range(2):
            try:
                sdr.set_center_freq(freq)
                return True
            except Exception as exc:
                logger.error('set_center_freq(%s) failed (attempt %d): %s',
                             freq, attempt + 1, exc)
        return False

    def read_samples(self):
        self._tick('start')
        scanner = self.scanner
        freq = self.center_frequency
        sweeps_per_scan = scanner.sweeps_per_scan
        samples_per_sweep = scanner.samples_per_sweep
        sdr = scanner.sdr
        if not self.tune(sdr, freq):
            self._tick('tuned')
            self.raw = None
            self.complete.set()
            self._tick('returned')
            return
        self._tick('tuned')
        self.raw = np.zeros((sweeps_per_scan, samples_per_sweep), 'complex')
        self.powers = np.zeros((sweeps_per_scan, samples_per_sweep), 'float64')
        if USE_SYNC_READ and hasattr(sdr, 'read_samples'):
            self.read_samples_sync(sdr, sweeps_per_scan * samples_per_sweep)
        else:
            sdr.read_samples_async(self.samples_callback, num_samples=samples_per_sweep)
        self._tick('returned')
    def read_samples_sync(self, sdr, num_samples):
        """One blocking read per tile. Starting and cancelling the async
        reader for every tile cost more time than the reading itself, and
        a cancel that never completes was one source of hangs."""
        num_samples = int(-(-num_samples // 512) * 512)   # multiple of 512
        iq = None
        for attempt in range(2):
            try:
                sdr.read_samples(NUM_SAMPLES_DISCARDED)    # old data from before retuning
                iq = sdr.read_samples(num_samples)
                break
            except Exception:
                logger.exception('read failed at %s Hz (attempt %s)', self.center_frequency, attempt + 1)
        if iq is None:
            self.raw = None
            self.complete.set()
            return
        sweeps, per_sweep = self.raw.shape
        self.raw = np.asarray(iq[:sweeps * per_sweep], dtype=np.complex128).reshape(sweeps, per_sweep)
        self.process_samples()
    def samples_callback(self, iq, context):
        samples_per_sweep = self.scanner.samples_per_sweep
        if not self.samples_discarded:
            self.samples_discarded = True
            return
        current_sweep = getattr(self, 'current_sweep', None)
        if current_sweep is None:
            current_sweep = self.current_sweep = 0
        if current_sweep >= self.raw.shape[0]:
            self.on_sample_read_complete()
            return
        self.raw[current_sweep] = iq
        self.current_sweep += 1
        if current_sweep > self.raw.shape[0]:
            self.on_sample_read_complete()
    def on_sample_read_complete(self):
        sdr = self.scanner.sdr
        if not sdr.read_async_canceling:
            sdr.cancel_read_async()
        self.process_samples()
    def translate_freq(self, samples, freq, rs):
        # Adapted from https://github.com/vsergeev/luaradio/blob/master/radio/blocks/signal/frequencytranslator.lua
        if not np.iscomplexobj(samples):
            samples = hilbert(samples)
        omega = 2 * np.pi * (freq / rs)
        def iter_phase():
            p = 0
            i = 0
            while i < samples.shape[-1]:
                yield p
                p += omega
                p -= 2 * np.pi
                i += 1
        phase_rot = np.fromiter(iter_phase(), dtype=np.float64)
        phase_rot = np.unwrap(phase_rot)
        xlator = np.zeros(phase_rot.size, dtype=samples.dtype)
        xlator.real = np.cos(phase_rot)
        xlator.imag = np.sin(phase_rot)
        samples *= xlator
        return samples
    def process_samples(self):
        self._tick('got_data')
        rs = self.scanner.sample_rate
        fc = self.center_frequency

        samples = self.raw.flatten()

        win_size = self.window_size
        win = get_window(self.scanner.sampling_config.window_type, win_size)
        freqs, Pxx = welch(samples, fs=rs, window=win, detrend=False,
            nperseg=win_size, scaling='density', return_onesided=False)

        # Plain PSD of the tile, centred on fc. (The old irfft -> translate_freq
        # -> rfft chain made the result depend on the centre frequency.)
        freqs, Pxx = sort_psd(freqs, Pxx)
        freqs = np.around(freqs)

        # Drop the weak tile edges and the DC spike in the middle of the tile.
        # Every frequency is also seen by a neighbouring tile at another
        # position, so nothing is lost as long as step < kept half-width.
        keep = self.good_bins_mask(freqs, rs)
        freqs = freqs[keep]
        Pxx = Pxx[keep]

        freqs += fc
        freqs /= 1e6

        self.powers = Pxx
        if not np.array_equal(freqs, self.frequencies):
            logger.warning('freq not equal: %s, %s' % (self.frequencies.size, freqs.size))
            self.frequencies = freqs
        self.raw = None
        self._tick('processed')
        self.collection.on_sample_set_processed(self)
        self._tick('inserted')
        self.complete.set()
    def good_bins_mask(self, offsets_hz, rs):
        """True for FFT bins that are used: not near the tile edge, not at DC."""
        cfg = self.scanner.sampling_config
        half = getattr(cfg, 'tile_keep_ratio', None)
        if half is None:
            half = DEFAULT_TILE_KEEP_RATIO
        gap = getattr(cfg, 'tile_center_gap_hz', None)
        if gap is None:
            gap = DEFAULT_TILE_CENTER_GAP_HZ
        a = np.abs(offsets_hz)
        return (a <= half * rs) & (a > gap)
    def calc_expected_freqs(self):
        freq = self.center_frequency
        scanner = self.scanner
        rs = scanner.sample_rate
        win_size = self.window_size
        num_samples = scanner.samples_per_sweep * scanner.sweeps_per_scan
        overlap_ratio = scanner.sampling_config.sweep_overlap_ratio
        fake_samples = np.zeros(num_samples, 'complex')
        f_expected, Pxx = welch(fake_samples.real, fs=rs, nperseg=win_size, return_onesided=False)

        f_expected, Pxx = sort_psd(f_expected, Pxx)

        f_expected = np.around(f_expected)
        f_expected = f_expected[self.good_bins_mask(f_expected, rs)]

        f_expected += freq
        f_expected /= 1e6
        return f_expected
    def _serialize(self):
        return {k:getattr(self, k) for k in self._serialize_attrs}


class SampleCollection(JSONMixin):
    def __init__(self, **kwargs):
        self.scanner = kwargs.get('scanner')
        self.scanning = threading.Event()
        self.stopped = threading.Event()
        self.sample_sets = {}
        self.timing = ScanTiming()
    def calc_progress(self):
        num_sets = len(self.sample_sets)
        if not num_sets:
            return 0
        num_complete = 0.
        for sample_set in self.sample_sets.values():
            if sample_set.complete.is_set():
                num_complete += 1
        return num_complete / num_sets
    def add_sample_set(self, sample_set):
        self.sample_sets[sample_set.center_frequency] = sample_set
    def build_sample_set(self, freq):
        sample_set = SampleSet(collection=self, center_frequency=freq)
        self.add_sample_set(sample_set)
        return sample_set
    def scan_all_freqs(self):
        self.scanning.set()
        complete_events = set()
        for key in sorted(self.sample_sets.keys()):
            if not self.scanning.is_set():
                break
            sample_set = self.sample_sets[key]
            sample_set.read_samples()
            if not sample_set.complete.is_set():
                complete_events.add(sample_set.complete)
        if self.scanning.is_set():
            for e in complete_events.copy():
                if e.is_set():
                    complete_events.discard(e)
                else:
                    e.wait()
        self.scanning.clear()
        self.timing.write_report()
        self.stopped.set()
    def stop(self):
        if self.scanning.is_set():
            self.scanning.clear()
            self.stopped.wait()
    def cancel(self):
        if self.scanning.is_set():
            self.scanning.clear()
            self.stopped.wait()
    def on_sample_set_processed(self, sample_set):
        self.scanner.on_sample_set_processed(sample_set)
    def _serialize(self):
        return {'sample_sets':
            {k: v._serialize() for k, v in self.sample_sets.items()},
        }
    def _deserialize(self, **kwargs):
        for key, val in kwargs.get('sample_sets', {}).items():
            sample_set = SampleSet.from_json(val, collection=self)
            self.sample_sets[key] = sample_set
