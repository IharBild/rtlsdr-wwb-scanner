import time
import threading
import numpy as np
from scipy.signal.windows import __all__ as WINDOW_TYPES
from scipy.signal import welch, get_window, hilbert

import logging
logger = logging.getLogger(__name__)

from wwb_scanner.core import JSONMixin
from wwb_scanner import scan_dll
from wwb_scanner.scanner.scan_timing import ScanTiming

WINDOW_TYPES = [s for s in WINDOW_TYPES if s != 'get_window']

# Windows that scipy cannot build from a name alone get usual parameters;
# old names get their new name. Anything that still fails becomes hann.
WINDOW_ALIASES = {'hanning': 'hann'}
def _window_spec(name, size):
    return {
        'kaiser': ('kaiser', 8.6),
        'kaiser_bessel_derived': ('kaiser_bessel_derived', 8.6),
        'gaussian': ('gaussian', size / 6.),
        'general_gaussian': ('general_gaussian', 1.5, size / 6.),
        'general_cosine': ('general_cosine', [0.5, 0.5]),
        'general_hamming': ('general_hamming', 0.54),
        'chebwin': ('chebwin', 100),
        'dpss': ('dpss', 3),
    }.get(name, name)

_window_cache = {}
def make_window(name, size):
    """Window array for ``name``; never raises (falls back to hann)."""
    key = (name, size)
    w = _window_cache.get(key)
    if w is not None:
        return w
    n = WINDOW_ALIASES.get(name or 'hann', name or 'hann')
    try:
        w = get_window(_window_spec(n, size), size)
    except Exception as exc:
        logger.warning('window %r cannot be used (%s), using hann', name, exc)
        w = get_window('hann', size)
    _window_cache[key] = w
    return w

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

# ADC overload (V16): share of samples at the 8-bit limit (0 or 255 ->
# +-1.0 after pyrtlsdr's scaling). From ~0.1 % on, clipping makes false
# peaks (intermodulation, harmonics) all over the tile.
CLIP_LEVEL = 0.995
OVERLOAD_RATIO = 1e-3


def clip_ratio(iq):
    a = np.asarray(iq)
    if a.size == 0:
        return 0.
    clipped = (np.abs(a.real) >= CLIP_LEVEL) | (np.abs(a.imag) >= CLIP_LEVEL)
    return float(np.count_nonzero(clipped)) / a.size


class SdrTimeout(Exception):
    """The dongle did not answer in time (a USB call that never returns)."""

_STUCK = []      # V18.1: helper threads still inside a USB call that never returned


def stuck_usb_calls():
    """V18.1 (audit 1.2): how many USB calls of earlier scans still hang."""
    _STUCK[:] = [t for t in _STUCK if t.is_alive()]
    return len(_STUCK)


def run_with_timeout(func, timeout, *args):
    """Call ``func`` in a helper thread. If it does not return within
    ``timeout`` seconds raise SdrTimeout, so the scan can stop instead of
    freezing the program. (A stuck USB call itself cannot be killed.)"""
    box = {}
    def target():
        try:
            box['result'] = func(*args)
        except BaseException as exc:
            box['exc'] = exc
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        _STUCK.append(t)
        raise SdrTimeout('no answer from the dongle in %s s' % timeout)
    if 'exc' in box:
        raise box['exc']
    return box.get('result')

# V17: a clipped tile is read again with lower gain (these steps down, until
# it is no longer clipped) and scaled back by the gain difference. Such a
# tile counts in the merge only where no normal tile has the point.
AUTO_GAIN_DROPS_DB = (6., 12., 20., 30.)
REMEASURED_WEIGHT = 0.01

MAX_TILE_ERRORS = 5
TUNE_TIMEOUT = 30.
READ_TIMEOUT = 40.

class SampleSet(JSONMixin):
    clip_ratio = 0.
    clip_ratio_first = None     # V17: before the re-measurement with lower gain
    gain_drop = 0.              # V17: dB the gain was lowered for this tile
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
    def device_closed(self, sdr):
        """pyrtlsdr closes the device after any library error. Another call
        on a closed device uses a freed handle (can crash the program), so
        the scan has to stop instead of retrying."""
        if getattr(sdr, 'device_opened', True):
            return False
        logger.error('the dongle was closed after an error, stopping the scan')
        self.abort_scan()
        return True

    def abort_scan(self):
        coll = self.collection
        if coll is not None:
            coll.aborted = True

    def tune(self, sdr, freq):
        """Set the centre frequency. A tuner that cannot lock must not
        kill the whole scan: retry once, then give up on this tile."""
        for attempt in range(2):
            try:
                run_with_timeout(sdr.set_center_freq, TUNE_TIMEOUT, freq)
                # scan edition DLL: the stock library says "OK" even when
                # the tuner PLL did not lock; such a tile would be garbage
                if scan_dll.tuner_locked(sdr) is False:
                    logger.error('PLL not locked at %s Hz (attempt %d)', freq, attempt + 1)
                    continue
                return True
            except SdrTimeout as exc:
                logger.error('set_center_freq(%s): %s', freq, exc)
                self.abort_scan()
                return False
            except Exception as exc:
                logger.error('set_center_freq(%s) failed (attempt %d): %s',
                             freq, attempt + 1, exc)
                if self.device_closed(sdr):
                    return False
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
                run_with_timeout(sdr.read_samples, READ_TIMEOUT, NUM_SAMPLES_DISCARDED)    # old data from before retuning
                iq = run_with_timeout(sdr.read_samples, READ_TIMEOUT, num_samples)
                break
            except SdrTimeout as exc:
                logger.error('read at %s Hz: %s', self.center_frequency, exc)
                self.abort_scan()
                break
            except Exception:
                logger.exception('read failed at %s Hz (attempt %s)', self.center_frequency, attempt + 1)
                if self.device_closed(sdr):
                    break
        if iq is None:
            self.raw = None
            self.complete.set()
            return
        sweeps, per_sweep = self.raw.shape
        self.clip_ratio = clip_ratio(iq)
        if self.clip_ratio >= OVERLOAD_RATIO and self._auto_gain_enabled():
            iq = self.remeasure_lower_gain(sdr, num_samples, iq)
        self.raw = np.asarray(iq[:sweeps * per_sweep], dtype=np.complex128).reshape(sweeps, per_sweep)
        self.process_samples()
    def _auto_gain_enabled(self):
        try:
            return bool(self.scanner.sampling_config.get('auto_gain'))
        except Exception:
            return False
    def remeasure_lower_gain(self, sdr, num_samples, iq):
        """The ADC clipped: read the tile again with lower gain and scale the
        samples back by the gain difference, so levels stay comparable.
        The gain is always set back. Returns the samples to use."""
        scanner = self.scanner
        try:
            g0 = float(sdr.gain)
        except Exception:
            g0 = float(scanner.gain or 0.)
        gains = None
        try:
            gains = sorted(float(g) for g in scanner.gains)
        except Exception:
            pass
        if not gains:
            return iq
        first = self.clip_ratio
        best = None
        try:
            for drop in AUTO_GAIN_DROPS_DB:
                lower = [g for g in gains if g <= g0 - drop + 0.5]
                if not lower:
                    lower = [gains[0]]
                g = lower[-1]
                if g >= g0 or (best is not None and g >= best[1]):
                    continue
                run_with_timeout(setattr, TUNE_TIMEOUT, sdr, 'gain', g)
                try:
                    g_set = float(sdr.gain)
                except Exception:
                    g_set = g
                run_with_timeout(sdr.read_samples, READ_TIMEOUT, NUM_SAMPLES_DISCARDED)
                x = run_with_timeout(sdr.read_samples, READ_TIMEOUT, num_samples)
                cr = clip_ratio(x)
                best = (x, g_set, cr)
                if cr < OVERLOAD_RATIO:
                    break
        except SdrTimeout as exc:
            logger.error('auto gain at %s Hz: %s', self.center_frequency, exc)
            self.abort_scan()
        except Exception:
            logger.exception('auto gain at %s Hz failed, clipped tile kept', self.center_frequency)
        finally:
            try:
                run_with_timeout(setattr, TUNE_TIMEOUT, sdr, 'gain', g0)
            except Exception:
                logger.exception('could not set the gain back to %s dB', g0)
        if best is None:
            return iq
        x, g_set, cr = best
        self.clip_ratio_first = first
        self.clip_ratio = cr
        self.gain_drop = g0 - g_set
        return np.asarray(x) * (10. ** (self.gain_drop / 20.))
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
        self.clip_ratio = max(self.clip_ratio, clip_ratio(iq))
        self.current_sweep += 1
        if current_sweep > self.raw.shape[0]:
            self.on_sample_read_complete()
    def on_sample_read_complete(self):
        sdr = self.scanner.sdr
        if not sdr.read_async_canceling:
            sdr.cancel_read_async()
        self.process_samples()
    # not used (old tile layout), kept for reference
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
        win = make_window(self.scanner.sampling_config.window_type, win_size)
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
        self.aborted = False
        self.tile_errors = 0
        self.processed_keys = set()
        self.missing_tiles = []
    def calc_progress(self):
        num_sets = len(self.sample_sets)
        if not num_sets:
            return 0
        done = getattr(self, 'processed_keys', None)
        if done is not None and self.scanning.is_set():
            # counting the "complete" flag of every tile for every tile was
            # O(n^2): 1.1 s of Python time on a full-range scan
            return min(1., len(done) / float(num_sets))
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
        self.aborted = False
        self.tile_errors = 0
        self.processed_keys = set()
        keys = sorted(self.sample_sets.keys())
        for n, key in enumerate(keys):
            if not self.scanning.is_set():
                break
            if self.aborted:
                logger.error('Scan stopped: the dongle stopped answering. '
                             'Unplug and replug it, then start again.')
                break
            if n % 20 == 0:
                print('scan tile %d/%d at %.3f MHz' % (n + 1, len(keys), key / 1e6), flush=True)
            sample_set = self.sample_sets[key]
            try:
                sample_set.read_samples()
            except Exception:
                # one bad tile must not kill the scan thread: the program
                # would stay "running" forever and Stop would hang
                logger.exception('tile at %s Hz failed', key)
                sample_set.complete.set()
                self.tile_errors += 1
                if self.tile_errors >= MAX_TILE_ERRORS:
                    logger.error('Scan stopped: %d tiles failed, see the log.', self.tile_errors)
                    self.aborted = True
            if not sample_set.complete.is_set():
                complete_events.add(sample_set.complete)
        self.retry_failed_tiles(keys)
        if self.scanning.is_set():
            for e in complete_events.copy():
                if e.is_set():
                    complete_events.discard(e)
                else:
                    e.wait()
        self.scanning.clear()
        self.report_pll()
        self.report_overload()
        self.timing.write_report()
        self.stopped.set()
    def retry_failed_tiles(self, keys):
        """One more try for tiles that gave no data (PLL not locked, USB
        error). Without it the WWB file gets a straight line there and a
        signal in that place is lost without notice."""
        done = self.processed_keys
        failed = [k for k in keys if k not in done]
        if not failed or self.aborted or not self.scanning.is_set():
            self.missing_tiles = failed
            return
        if len(failed) > max(3, 0.2 * len(keys)):
            # most tiles failed: something is wrong with the dongle itself,
            # measuring them all again would only double the time
            self.missing_tiles = failed
            logger.error('%d of %d tiles have no data: check the dongle', len(failed), len(keys))
            return
        print('retrying %d tile(s) without data' % len(failed), flush=True)
        for key in failed:
            if self.aborted or not self.scanning.is_set():
                break
            sample_set = self.sample_sets[key]
            sample_set.complete.clear()
            try:
                sample_set.read_samples()
            except Exception:
                logger.exception('tile at %s Hz failed again', key)
                sample_set.complete.set()
        self.missing_tiles = [k for k in keys if k not in done]
        if self.missing_tiles:
            logger.warning('%d tile(s) have no data, e.g. %.3f MHz: the scan has a gap there',
                           len(self.missing_tiles), self.missing_tiles[0] / 1e6)

    def report_overload(self):
        """Warn about tiles where the ADC clipped (signal too strong).
        Sets self.overload = [(lo_mhz, hi_mhz, max_ratio), ...]."""
        self.overload = []
        self.remeasured = []
        def first(s):
            r = getattr(s, 'clip_ratio_first', None)
            return getattr(s, 'clip_ratio', 0.) if r is None else r
        bad = sorted((k, first(s)) for k, s in self.sample_sets.items()
                     if first(s) >= OVERLOAD_RATIO)
        if not bad:
            return
        fixed = [k for k, s in self.sample_sets.items() if getattr(s, 'gain_drop', 0.) > 0
                 and getattr(s, 'clip_ratio', 0.) < OVERLOAD_RATIO]
        still = [k for k, _ in bad if self.sample_sets[k].clip_ratio >= OVERLOAD_RATIO]
        keys = sorted(self.sample_sets)
        steps = [b - a for a, b in zip(keys, keys[1:]) if b > a]
        step = min(steps) if steps else 1e6
        try:
            fs = float(self.scanner.sample_rate)
        except Exception:
            fs = 2.4e6
        half = 0.39 * fs
        groups = []
        for k, r in bad:
            if groups and k - groups[-1][1] <= 2.5 * step:
                groups[-1][1] = k
                groups[-1][2] = max(groups[-1][2], r)
            else:
                groups.append([k, k, r])
        self.overload = [((a - half) / 1e6, (b + half) / 1e6, r) for a, b, r in groups]
        if fixed:
            drops = [self.sample_sets[k].gain_drop for k in fixed]
            self.remeasured = sorted(fixed)
            where = ', '.join('%.1f-%.1f' % (lo, hi) for lo, hi, _ in self.overload[:6])
            if not still:
                logger.info('Overload at %s MHz: %d tile(s) measured again with %.0f-%.0f dB less gain '
                            '(levels right, floor there higher)', where, len(fixed), min(drops), max(drops))
                return
            logger.warning('Overload at %s MHz: %d tile(s) still clipped even with %.0f dB less gain: '
                           'move the antenna away from the transmitters', where, len(still),
                           max(AUTO_GAIN_DROPS_DB))
            return
        logger.warning('Overload (signal too strong for the dongle) at %s MHz, up to %.1f %% of samples '
                       'clipped: lower the gain or move the antenna away from transmitters',
                       ', '.join('%.1f-%.1f' % (lo, hi) for lo, hi, _ in self.overload[:6]),
                       100. * max(r for _, _, r in self.overload))

    def report_pll(self):
        sdr = getattr(getattr(self, 'scanner', None), 'sdr', None)
        try:
            stats = scan_dll.pll_stats(sdr) if sdr is not None else None
        except Exception:
            stats = None
        if stats is not None:
            print('tuner PLL: locked late %d times, not locked %d times' % stats, flush=True)
    def stop(self):
        if self.scanning.is_set():
            self.scanning.clear()
            self.stopped.wait()
    def cancel(self):
        if self.scanning.is_set():
            self.scanning.clear()
            self.stopped.wait()
    def on_sample_set_processed(self, sample_set):
        self.processed_keys.add(sample_set.center_frequency)
        self.scanner.on_sample_set_processed(sample_set)
    def _serialize(self):
        return {'sample_sets':
            {k: v._serialize() for k, v in self.sample_sets.items()},
        }
    def _deserialize(self, **kwargs):
        for key, val in kwargs.get('sample_sets', {}).items():
            sample_set = SampleSet.from_json(val, collection=self)
            self.sample_sets[key] = sample_set
