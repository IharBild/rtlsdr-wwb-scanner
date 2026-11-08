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

# V20 (user's series 08.11.2026: mic 632.925 MHz next to the antenna, gain
# 0/7/21/33.8/49.6 dB):
# - after the steps above the gain goes down to the lowest value of the
#   table; if every step still clips, the least clipped reading is used;
# - tiles up to NEIGHBOUR_HZ from such a strong carrier are read again with
#   the same low gain: the dongle's internal ADC clips there too, but the
#   8 output bits do not show it (false peaks at 631.4 / 634.5 MHz);
# - a tile with a low-gain reading is merged point by point: where the
#   low-gain reading shows a signal (more than SIGNAL_OVER_FLOOR_DB above
#   its own floor) its level is used; elsewhere the lower of the two
#   readings (the plateau of the low gain and the false peaks of the
#   normal gain both go away), but not lower than the usual floor of the
#   scan; a normal reading that the carrier made deaf (all of it below the
#   usual floor) is raised by that loss first, up to DESENSE_MAX_DB;
# - a point that only knows "not above the low-gain floor" counts in the
#   spectrum only where no other tile has a real measurement (weight 0.01);
# - the status line says which band lost sensitivity and by how much.
# Tested on the simulator with a wide-band ADC model (tests/sim.py WideAdc)
# against a scan of the same air without the mic (tests/test_v20.py).
AUTO_GAIN_TO_MIN = True
NEIGHBOUR_HZ = 4e6
SIGNAL_OVER_FLOOR_DB = 10.
FLOOR_PERCENTILE = 20.
# at most this share of all tiles is read again as neighbours (a scan
# full of strong carriers must not take twice as long)
MAX_NEIGHBOUR_SHARE = 0.25
# a normal reading this many dB (or more) below the usual floor: the
# dongle was made deaf by the strong carrier, the loss is added back
DESENSE_MIN_DB = 3.
# ... but not more than this: deafer than that, the normal reading is not used
DESENSE_MAX_DB = 30.
# a normal-gain bin counts as a real measurement only this far below the
# floor of the low-gain reading
KNOWN_BELOW_LOW_DB = 6.
# sensitivity loss reported as a warning from this many dB
LOSS_WARN_DB = 10.

# V20.1 (user's log 08.11.2026 22:00, 49.6 dB, mic 632.925 MHz):
# - one gain per carrier zone: some clipped tiles stopped at -6 dB (their
#   8 output bits no longer clipped, the internal ADC still did) and gave
#   products of +30...+46 dB (632.300 / 631.65 / 631.05). Every tile of the
#   zone, clipped or not, is now read with the gain the zone needs;
# - the carrier of a zone is its strongest level (was: nearest product);
# - at least MIN_NEIGHBOUR_TILES tiles are read again (was: 13 of 55);
# - a "signal" seen by the low-gain reading of one tile only, while another
#   tile shows the same frequency PRODUCT_DB lower, is a product of that tile;
# - the low-gain floor of a tile is that of its zone, so a wide signal
#   (DVB-T) is a signal and not "lost sensitivity";
# - the dongle goes deaf up to ~9 MHz from the mic (49.6 dB: 635.4-642 MHz
#   6-9 dB below the floor): normal tiles up to DESENSE_HZ are checked
#   against the floor of the scan and raised by the loss;
# - images of the carrier at +-fs (aliases, 630.525 / 635.325) and at
#   +-28.8 MHz (crystal, 661.725) are seen by every tile there; they are
#   replaced by the floor around them and named in the status line.
MIN_NEIGHBOUR_TILES = 30
PRODUCT_DB = 10.
PRODUCT_WEIGHT = 1e-3
DESENSE_HZ = 10e6
XTAL_HZ = 28.8e6
IMAGE_HALF_HZ = 150e3
IMAGE_OVER_DB = 6.

# V20.2 (user's series 08.11.2026 23:34, V20.1 on the dongle):
# - where no tile has a real measurement (only "not above the floor of the
#   low-gain reading") the plateau of that reading is shown, flat, instead
#   of the spikes of a clipped normal reading (631.05 MHz +31 dB at 49.6 dB:
#   not a signal, only "unknown up to the plateau"); such bands are drawn
#   shaded on the graph with the dB lost;
# - "lost sensitivity" only where the level is not above the plateau of
#   its zone (dips of a DVB-T multiplex are signal, not loss);
# - images at +-fs / +-28.8 MHz also of strong narrow carriers that did not
#   clip (635.325 MHz +12 dB at 0 dB gain), if their level fits the
#   dongle's image rejection (alias -36...-52 dBc, crystal -60...-80 dBc).
STRONG_CARRIER_DB = 45.
ALIAS_DBC = (-52., -36.)       # user's V4: -44 dBc at every gain
XTAL_DBC = (-80., -60.)        # user's V4: -70 dBc
PLATEAU_MARGIN_DB = 6.
# a normal reading at most this far above the usual floor is a real measurement
KNOWN_AT_FLOOR_DB = 3.


def to_db(p):
    return 10. * np.log10(np.maximum(np.asarray(p, dtype=np.float64), 1e-30))


def merge_low_gain(p_normal, p_low, over_db=None, floor=None, low_floor_db=None):
    """V20: one tile read with the normal gain (``p_normal``, sensitive, but
    with false peaks when the ADC clips) and with a low gain (``p_low``,
    levels right, floor raised by the gain difference), both linear power
    on the same bins. Returns (merged, signal_mask, low_floor_db):
    where the low-gain reading is more than ``over_db`` above its own floor
    it is used, elsewhere min(normal, low).

    ``floor`` (linear, per bin or one value): the usual floor of the scan
    at these bins. A clipped reading also pushes the noise down (the
    carrier takes the whole ADC range), so min(normal, low) could fall
    below the real floor; it is not taken lower than min(low, floor)."""
    if over_db is None:
        over_db = SIGNAL_OVER_FLOOR_DB
    pl = np.asarray(p_low, dtype=np.float64)
    pn = np.asarray(p_normal, dtype=np.float64)
    if pl.size == 0:
        return pl.copy(), np.zeros(0, bool), None
    dl = to_db(pl)
    low_floor = float(np.percentile(dl, FLOOR_PERCENTILE))
    if low_floor_db is not None:
        # V20.1: floor of the low-gain readings of the whole zone (a tile
        # full of a wide signal has no floor of its own)
        low_floor = min(low_floor, float(low_floor_db))
    sig = dl > low_floor + over_db
    if pn.shape != pl.shape:
        return pl.copy(), sig, low_floor
    quiet = np.minimum(pn, pl)
    if floor is not None:
        fl = np.broadcast_to(np.asarray(floor, dtype=np.float64), pl.shape)
        quiet = np.maximum(quiet, np.minimum(pl, fl))
    return np.where(sig, pl, quiet), sig, low_floor


def low_limited_bins(p_normal, p_low, signal_mask, floor=None):
    """V20: bins where the merged tile only says "not above the low-gain
    floor": no signal in the low-gain reading and the normal reading was
    higher (false peaks / clipping noise). Such a bin counts in the
    spectrum only where no other tile has a real measurement."""
    pn = np.asarray(p_normal, dtype=np.float64)
    pl = np.asarray(p_low, dtype=np.float64)
    if pn.shape != pl.shape:
        return ~np.asarray(signal_mask, bool)
    # a normal reading only a little below the low-gain floor is no
    # better: a tile clipped all the time has its clipping noise there
    lim = (~np.asarray(signal_mask, bool)) & (pn > pl * 10. ** (-KNOWN_BELOW_LOW_DB / 10.))
    if floor is not None:
        # V20.2: ... unless it is at the usual floor of the scan: with a small
        # gain drop the low-gain plateau is only a few dB higher, and a clean
        # normal reading there is a real measurement
        fl = np.broadcast_to(np.asarray(floor, dtype=np.float64), pn.shape)
        lim &= pn > fl * 10. ** (KNOWN_AT_FLOOR_DB / 10.)
    return lim

MAX_TILE_ERRORS = 5
TUNE_TIMEOUT = 30.
READ_TIMEOUT = 40.

class SampleSet(JSONMixin):
    clip_ratio = 0.
    clip_ratio_first = None     # V17: before the re-measurement with lower gain
    gain_drop = 0.              # V17: dB the gain was lowered for this tile
    raw_normal = None           # V20: samples of the normal-gain reading (until processed)
    powers_normal = None        # V20: PSD with the normal gain (kept bins)
    powers_low = None           # V20: PSD with the low gain, scaled back
    low_gain = None             # V20: gain of the low-gain reading, dB
    low_floor_db = None         # V20: floor of the low-gain reading (20th percentile)
    carrier_hz = None           # V20: strongest signal of a clipped tile
    neighbour = False           # V20: read again because a strong carrier is near
    clip_ratio_low = None       # V20: clipping of the low-gain reading of a neighbour
    floor_ref = None            # V20: usual floor at the bins of this tile (linear)
    bin_weight = None           # V20: weight of every bin in the spectrum merge
    signal_mask = None          # V20: bins with a signal in the low-gain reading
    desense_db = 0.             # V20: normal reading this much below the usual floor
    carrier_db = None           # V20.1: level of carrier_hz (low gain, scaled back)
    zone_reread = False         # V20.1: clipped tile read again with the gain of its zone
    product_bins = None         # V20.1: bins found to be a product of this tile
    powers_orig = None          # V20.1: powers before the deafness was added back
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
        self.raw_normal = None
        if self.clip_ratio >= OVERLOAD_RATIO and self._auto_gain_enabled():
            normal = iq
            iq = self.remeasure_lower_gain(sdr, num_samples, iq)
            if iq is not normal:
                # V20: both readings go into the tile (merge_low_gain)
                self.raw_normal = np.asarray(normal[:sweeps * per_sweep], dtype=np.complex128)
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
        tried = []
        try:
            for g in self.lower_gain_steps(g0, gains):
                if best is not None and g >= best[1]:
                    continue
                run_with_timeout(setattr, TUNE_TIMEOUT, sdr, 'gain', g)
                try:
                    g_set = float(sdr.gain)
                except Exception:
                    g_set = g
                run_with_timeout(sdr.read_samples, READ_TIMEOUT, NUM_SAMPLES_DISCARDED)
                x = run_with_timeout(sdr.read_samples, READ_TIMEOUT, num_samples)
                cr = clip_ratio(x)
                tried.append((x, g_set, cr))
                best = (x, g_set, cr)
                if cr < OVERLOAD_RATIO:
                    break
            if best is not None and best[2] >= OVERLOAD_RATIO and tried:
                # V20: still clipped even at the lowest gain - the least
                # clipped reading (the higher gain of equal ones)
                best = min(tried, key=lambda t: (t[2], -t[1]))
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
        self.low_gain = g_set
        return np.asarray(x) * (10. ** (self.gain_drop / 20.))

    def read_low_gain(self, sdr, g_low, g0):
        """V20: this tile is near a strong carrier: read it again with the
        gain ``g_low`` (the caller has set it) and merge the reading with
        the normal one of the scan. V20.1: also a clipped tile whose own
        low-gain reading was made with more gain than its zone needs.
        The spectrum is rebuilt by the caller."""
        if self.powers is None:
            return False
        was_low = self.powers_low is not None
        normal = self.powers_normal if was_low else self.powers
        if normal is None:
            return False
        if not self.tune(sdr, self.center_frequency):
            return False
        sweeps, per_sweep = self.sweeps_per_scan, self.samples_per_sweep
        n = int(-(-(sweeps * per_sweep) // 512) * 512)
        run_with_timeout(sdr.read_samples, READ_TIMEOUT, NUM_SAMPLES_DISCARDED)
        x = run_with_timeout(sdr.read_samples, READ_TIMEOUT, n)
        if x is None:
            return False
        cr = clip_ratio(x)
        drop = float(g0) - float(g_low)
        x = np.asarray(x[:sweeps * per_sweep], dtype=np.complex128) * (10. ** (drop / 20.))
        offs, Pfull, keep = self.tile_psd(x)
        P = Pfull[keep]
        if P.shape != np.shape(normal):
            return False
        self.powers = self.merge_readings(normal, P)
        self.gain_drop = drop
        self.low_gain = float(g_low)
        if was_low:
            self.zone_reread = True
            self.clip_ratio = cr
            self._find_carrier(offs, Pfull)
        else:
            self.clip_ratio_low = cr
            self.neighbour = True
        return True

    def _find_carrier(self, offs, Pfull):
        """V20: the strongest signal of a clipped tile (anywhere in the tile
        except DC): its zone is read again after the scan."""
        cfg = self.scanner.sampling_config
        gap = getattr(cfg, 'tile_center_gap_hz', None)
        if gap is None:
            gap = DEFAULT_TILE_CENTER_GAP_HZ
        ok = np.abs(offs) > gap
        if ok.any():
            i = int(np.flatnonzero(ok)[np.argmax(Pfull[ok])])
            self.carrier_hz = float(self.center_frequency + offs[i])
            self.carrier_db = float(to_db(Pfull[i]))

    @staticmethod
    def lower_gain_steps(g0, gains):
        """Gains to try for a clipped tile, high to low: the table gain at
        least each AUTO_GAIN_DROPS_DB below ``g0``, then (V20) the lowest."""
        out = []
        for drop in AUTO_GAIN_DROPS_DB:
            lower = [g for g in gains if g <= g0 - drop + 0.5]
            if not lower:
                lower = [gains[0]]
            out.append(lower[-1])
        if AUTO_GAIN_TO_MIN:
            out.append(gains[0])
        steps = []
        for g in out:
            if g < g0 and g not in steps and (not steps or g < steps[-1]):
                steps.append(g)
        return steps
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
    def tile_psd(self, samples):
        """PSD of one reading of the tile, centred on fc. Returns
        (offsets_hz, Pxx, keep): all bins, and the mask of the bins used."""
        rs = self.scanner.sample_rate
        samples = np.asarray(samples).ravel()
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
        return freqs, Pxx, keep

    def merge_readings(self, p_normal, p_low, floor=None, normal_gain_db=0., low_floor_db=None):
        """V20: keep both readings and merge them (merge_low_gain).
        ``normal_gain_db``: the normal reading is raised by this much first
        (desensitization measured by merge_again)."""
        p_low = np.asarray(p_low, dtype=np.float64)
        pn = np.asarray(p_normal, dtype=np.float64)
        if normal_gain_db is None:
            # the normal reading is of no use (dongle completely deaf)
            merged, sig, low_floor = merge_low_gain(p_low, p_low, low_floor_db=low_floor_db)
            merged = p_low.copy()
            limited = ~sig
        else:
            use = pn * (10. ** (normal_gain_db / 10.)) if normal_gain_db else pn
            merged, sig, low_floor = merge_low_gain(use, p_low, floor=floor, low_floor_db=low_floor_db)
            limited = low_limited_bins(use, p_low, sig, floor=floor)
        # V20.2: where the normal reading tells nothing (limited) the honest
        # value is the low-gain reading itself - "unknown up to here" - not
        # the spikes of the clipped normal reading
        merged = np.where(limited, p_low, merged)
        self.powers_normal = pn
        self.powers_low = p_low
        self.low_floor_db = low_floor
        self.signal_mask = sig
        self.bin_weight = np.where(limited, REMEASURED_WEIGHT, 1.0)
        return merged

    def merge_again(self, floor, low_floor_db=None):
        """V20: merge the two readings again, now that the usual floor of
        the scan at these bins (``floor``, linear) is known. A strong
        carrier can also make the dongle deaf: the whole normal reading
        (noise and weak signals alike) comes out lower than the usual
        floor. That loss is measured on the quiet bins and added back."""
        if self.powers_low is None or self.powers_normal is None:
            return False
        d = 0.
        if floor is not None and np.size(floor) == np.size(self.powers_normal):
            sig = getattr(self, 'signal_mask', None)
            quiet = ~np.asarray(sig, bool) if sig is not None and np.size(sig) == np.size(floor) \
                else np.ones(np.size(floor), bool)
            if np.count_nonzero(quiet) >= 8:
                d = float(np.percentile(to_db(np.asarray(floor)[quiet]), FLOOR_PERCENTILE)
                          - np.percentile(to_db(self.powers_normal[quiet]), FLOOR_PERCENTILE))
        self.desense_db = d if d >= DESENSE_MIN_DB else 0.
        gain_db = self.desense_db
        if d > DESENSE_MAX_DB:
            # nearly nothing came through: only the low-gain reading counts
            self.desense_db = 0.
            gain_db = None
        self.powers = self.merge_readings(self.powers_normal, self.powers_low, floor=floor,
                                          normal_gain_db=gain_db, low_floor_db=low_floor_db)
        return True

    def process_samples(self):
        self._tick('got_data')
        fc = self.center_frequency

        offs, Pfull, keep = self.tile_psd(self.raw)
        freqs = offs[keep]
        Pxx = Pfull[keep]
        if self.raw_normal is not None:
            # V20: clipped tile read again with low gain. The strongest
            # signal (anywhere in the tile except DC) is the carrier whose
            # neighbour tiles are read again after the scan.
            self._find_carrier(offs, Pfull)
            _, Pn_full, _ = self.tile_psd(self.raw_normal)
            self.raw_normal = None
            if Pn_full.shape == Pfull.shape:
                Pxx = self.merge_readings(Pn_full[keep], Pxx)

        freqs = freqs + fc
        freqs /= 1e6

        self.powers = Pxx
        if not np.array_equal(freqs, self.frequencies):
            # diagnostic only (the tile is used as measured): no status line message
            logger.info('freq not equal: %s, %s' % (self.frequencies.size, freqs.size))
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
        self.remeasure_neighbours(keys)
        self.remove_images()
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

    def carrier_zones(self):
        """V20.1: strong carriers found by clipped tiles, grouped: carriers
        closer than NEIGHBOUR_HZ belong to one zone. Each zone:
        {'carrier': Hz of its strongest level, 'level': dB, 'gain': lowest
        gain any of its clipped tiles needed, 'members': [carrier Hz]}."""
        cs = sorted((s.carrier_hz, s.carrier_db if s.carrier_db is not None else -1e9, s.low_gain)
                    for s in self.sample_sets.values()
                    if s.carrier_hz is not None and s.low_gain is not None and not s.neighbour)
        zones = []
        for c, db, g in cs:
            if zones and c - zones[-1]['members'][-1] <= NEIGHBOUR_HZ:
                z = zones[-1]
                z['members'].append(c)
                z['gain'] = min(z['gain'], g)
                if db > z['level']:
                    z['level'], z['carrier'] = db, c
            else:
                zones.append({'carrier': c, 'level': db, 'gain': g, 'members': [c]})
        return zones

    @staticmethod
    def zone_of(key, zones, reach=None):
        """V20.1: the zone tile ``key`` belongs to (None: none)."""
        if reach is None:
            reach = NEIGHBOUR_HZ
        best = None
        for z in zones:
            d = min(abs(key - c) for c in z['members'])
            if d <= reach and (best is None or d < best[0]):
                best = (d, z)
        return best

    def remeasure_neighbours(self, keys):
        """V20: tiles up to NEIGHBOUR_HZ from the carrier of a clipped tile are
        read again with the low gain of that tile and merged. Their own 8
        output bits did not clip, but the dongle's internal ADC did (false
        peaks next to a strong mic / IEM). V20.1: one gain for the whole
        zone - also clipped tiles that stopped at a smaller gain drop are
        read again with it. The spectrum is rebuilt after."""
        self.neighbours = []
        self.zone_rereads = []
        self.zone_floors = []
        self.neighbours_skipped = 0
        if self.aborted or not self.scanning.is_set():
            return
        zones = self.carrier_zones()
        if not zones:
            self.finish_low_gain()
            return
        done_keys = self.processed_keys
        targets = {}
        for k in keys:
            s = self.sample_sets[k]
            if k not in done_keys or s.powers is None:
                continue
            hit = self.zone_of(k, zones)
            if hit is None:
                continue
            d, z = hit
            if s.powers_low is None:
                targets[k] = (z['gain'], d)
            elif not s.neighbour and s.low_gain is not None and s.low_gain > z['gain'] + 0.5:
                targets[k] = (z['gain'], -1.)          # clipped tile first
        if not targets:
            self.finish_low_gain()
            return
        limit = max(MIN_NEIGHBOUR_TILES, int(MAX_NEIGHBOUR_SHARE * len(keys)))
        if len(targets) > limit:
            keep = sorted(targets, key=lambda k: targets[k][1])[:limit]
            self.neighbours_skipped = len(targets) - limit
            targets = dict((k, targets[k]) for k in keep)
        scanner = self.scanner
        sdr = scanner.sdr
        try:
            g0 = float(sdr.gain)
        except Exception:
            g0 = float(scanner.gain or 0.)
        by_gain = {}
        for k, (g, _) in targets.items():
            by_gain.setdefault(g, []).append(k)
        print('reading %d tile(s) next to strong carriers again with less gain' % len(targets), flush=True)
        done = []
        try:
            for g in sorted(by_gain, reverse=True):
                if self.aborted or not self.scanning.is_set():
                    break
                run_with_timeout(setattr, TUNE_TIMEOUT, sdr, 'gain', g)
                try:
                    g_set = float(sdr.gain)
                except Exception:
                    g_set = g
                for k in sorted(by_gain[g]):
                    if self.aborted or not self.scanning.is_set():
                        break
                    if self.sample_sets[k].read_low_gain(sdr, g_set, g0):
                        done.append(k)
        except SdrTimeout as exc:
            logger.error('reading tiles next to a strong carrier: %s', exc)
            self.aborted = True
        except Exception:
            logger.exception('reading tiles next to a strong carrier failed, they are kept as measured')
        finally:
            try:
                run_with_timeout(setattr, TUNE_TIMEOUT, sdr, 'gain', g0)
            except Exception:
                logger.exception('could not set the gain back to %s dB', g0)
        self.neighbours = sorted(k for k in done if self.sample_sets[k].neighbour)
        self.zone_rereads = sorted(k for k in done if self.sample_sets[k].zone_reread)
        self.finish_low_gain()

    def _sample_rate(self):
        try:
            return float(self.scanner.sample_rate)
        except Exception:
            return 2.4e6

    def far_tiles(self, reach=None):
        """V20: keys of normal tiles far from every strong carrier. V20.1:
        beyond DESENSE_HZ (the dongle can be deaf that far) if there are at
        least 4 such tiles, else beyond NEIGHBOUR_HZ + fs."""
        carriers = [s.carrier_hz for s in self.sample_sets.values() if s.carrier_hz is not None]
        fs = self._sample_rate()
        reaches = [reach] if reach is not None else [DESENSE_HZ + fs / 2., NEIGHBOUR_HZ + fs]
        out = []
        for far in reaches:
            out = []
            for k, s in self.sample_sets.items():
                if s.powers is None or s.powers_low is not None or not np.size(s.powers):
                    continue
                if any(abs(k - c) <= far for c in carriers):
                    continue
                out.append(k)
            if len(out) >= 4:
                break
        return sorted(out)

    def _tile_floor_db(self, k):
        s = self.sample_sets[k]
        p = s.powers_orig if s.powers_orig is not None else s.powers
        return float(np.percentile(to_db(p), FLOOR_PERCENTILE))

    def floor_template(self, key, far_keys=None, span_hz=20e6):
        """V20: the usual floor at the bins of the tile ``key`` (linear): per
        bin the 30th percentile of normal tiles far from strong carriers,
        nearest ones (within ``span_hz``) if there are at least 4. V20.1:
        only quiet tiles (own floor within 3 dB of the quietest quarter) -
        a DVB-T multiplex next to the zone is not a floor. None if there is
        nothing to compare with."""
        if far_keys is None:
            far_keys = self.far_tiles()
        n = np.size(self.sample_sets[key].powers)
        same = [k for k in far_keys if np.size(self.sample_sets[k].powers) == n]
        if not same:
            return None
        near = [k for k in same if abs(k - key) <= span_hz]
        use = near if len(near) >= 4 else same
        floors = np.array([self._tile_floor_db(k) for k in use])
        quiet = [k for k, f in zip(use, floors) if f <= np.percentile(floors, 25) + 3.]
        if len(quiet) >= 2:
            use = quiet
        stack = np.vstack([np.asarray(self.sample_sets[k].powers_orig if self.sample_sets[k].powers_orig
                                      is not None else self.sample_sets[k].powers, dtype=np.float64)
                           for k in use])
        return np.percentile(stack, 30, axis=0)

    def finish_low_gain(self):
        """V20: after the scan the usual floor is known: tiles with a
        low-gain reading are merged again with it, then the spectrum is
        built again from all tiles. V20.1: with the low-gain floor of the
        whole zone, products of single tiles taken out, normal tiles made
        deaf by the carrier raised, images of the carrier replaced."""
        low = [k for k in sorted(self.sample_sets)
               if self.sample_sets[k].powers_low is not None and k in self.processed_keys]
        self.desensed = []
        self.images = []
        if not low:
            return
        zones = self.carrier_zones()
        far_keys = self.far_tiles()
        # floor of the low-gain readings of each zone
        zone_floor = {}
        for z in zones:
            ks = [k for k in low if (self.zone_of(k, [z]) is not None)]
            if ks:
                allp = np.concatenate([to_db(self.sample_sets[k].powers_low) for k in ks])
                zone_floor[id(z)] = float(np.percentile(allp, FLOOR_PERCENTILE))
        for k in low:
            s = self.sample_sets[k]
            hit = self.zone_of(k, zones)
            zf = zone_floor.get(id(hit[1])) if hit is not None else None
            s.floor_ref = self.floor_template(k, far_keys)
            s.merge_again(s.floor_ref, low_floor_db=zf)
        try:
            self._mark_tile_products(low)
        except Exception:
            logger.exception('product check failed')
        try:
            self._raise_deaf_tiles(zones, far_keys)
        except Exception:
            logger.exception('deafness check failed')
        self.zone_floors = [(z, zone_floor.get(id(z))) for z in zones]
        rebuild = getattr(self.scanner, 'rebuild_spectrum', None)
        if rebuild is not None:
            rebuild()

    def _mark_tile_products(self, low):
        """V20.1: a "signal" of one tile's low-gain reading that another tile
        shows PRODUCT_DB lower at the same frequency is a product of that
        tile (a real signal is in every tile that covers it): there it gets
        the other tile's value and almost no weight."""
        where = {}
        for k in low:
            s = self.sample_sets[k]
            fr = np.round(np.asarray(s.frequencies, dtype=np.float64) * 1e6).astype(np.int64)
            dl = to_db(s.powers_low)
            for i in range(fr.size):
                where.setdefault(int(fr[i]), []).append((k, i, float(dl[i])))
        n = 0
        for f, lst in where.items():
            if len(lst) < 2:
                continue
            kmin, imin, ref = min(lst, key=lambda t: t[2])
            for k, i, v in lst:
                s = self.sample_sets[k]
                if v > ref + PRODUCT_DB and s.signal_mask is not None and s.signal_mask[i]:
                    s.powers[i] = self.sample_sets[kmin].powers[imin]
                    s.bin_weight[i] = PRODUCT_WEIGHT
                    if s.product_bins is None:
                        s.product_bins = []
                    s.product_bins.append(i)
                    n += 1
        self.product_points = n
        if n:
            logger.info('%d point(s) seen by one tile only next to strong carriers: taken from the other tiles', n)

    def _raise_deaf_tiles(self, zones, far_keys):
        """V20.1: normal tiles up to DESENSE_HZ from a strong carrier whose
        floor is DESENSE_MIN_DB or more below the usual one: the dongle was
        deaf there (noise and weak signals alike); the loss is added back
        (up to DESENSE_MAX_DB)."""
        if not zones:
            return
        fs = self._sample_rate()
        for k in sorted(self.sample_sets):
            s = self.sample_sets[k]
            if s.powers is None or s.powers_low is not None or k in far_keys or not np.size(s.powers):
                continue
            if self.zone_of(k, zones, reach=DESENSE_HZ + fs / 2.) is None:
                continue
            tmpl = self.floor_template(k, far_keys)
            if tmpl is None:
                continue
            base = s.powers_orig if s.powers_orig is not None else s.powers
            d = float(np.percentile(to_db(tmpl), FLOOR_PERCENTILE)
                      - np.percentile(to_db(base), FLOOR_PERCENTILE))
            if d < DESENSE_MIN_DB:
                continue
            d = min(d, DESENSE_MAX_DB)
            s.powers_orig = np.asarray(base, dtype=np.float64)
            s.powers = s.powers_orig * (10. ** (d / 10.))
            s.desense_db = d
            self.desensed.append(k)
        if self.desensed:
            logger.info('%d tile(s) near strong carriers were deaf, raised by up to %.1f dB',
                        len(self.desensed), max(self.sample_sets[k].desense_db for k in self.desensed))

    def strong_carriers(self):
        """V20.2: narrow carriers of the merged spectrum STRONG_CARRIER_DB or
        more above the usual floor (they need not have clipped):
        [(Hz, level dB), ...]."""
        spectrum = getattr(self.scanner, 'spectrum', None)
        ref = self._reference_floor_db()
        if spectrum is None or ref is None:
            return []
        with spectrum.data_update_lock:
            f = np.asarray(spectrum.sample_data['frequency'], dtype=np.float64).copy()
            y = np.asarray(spectrum.sample_data['dbFS'], dtype=np.float64).copy()
        if f.size < 16:
            return []
        out = []
        df = float(np.median(np.diff(f)))
        w = max(2, int(round(0.15 / df))) if df > 0 else 6
        for i in np.flatnonzero(y >= ref + STRONG_CARRIER_DB):
            a, b = max(i - w, 0), min(i + w, y.size - 1)
            if y[i] < y[a:b + 1].max():
                continue
            # narrow: 150 kHz away it is at least 20 dB lower on both sides
            if y[a] > y[i] - 20. or y[b] > y[i] - 20.:
                continue
            out.append((f[i] * 1e6, float(y[i])))
        return out

    def remove_images(self):
        """V20.2: images of strong carriers (clipping zones and, with the
        auto gain or the spur cleaning on, any strong narrow carrier)."""
        self.images = []
        if self.aborted:
            return
        zones = list(getattr(self, 'zone_floors', None) and [z for z, _ in self.zone_floors] or [])
        if not zones:
            zones = self.carrier_zones() if any(
                s.powers_low is not None for s in self.sample_sets.values()) else []
        carriers = [(z['carrier'], None) for z in zones]
        auto = False
        try:
            auto = bool(self.scanner.sampling_config.get('auto_gain'))
        except Exception:
            pass
        spurs = False
        try:
            spurs = bool(self.scanner.config.processing.get('clean_spurs'))
        except Exception:
            pass
        if auto or spurs:
            for c, lv in self.strong_carriers():
                if all(abs(c - z) > IMAGE_HALF_HZ for z, _ in carriers):
                    carriers.append((c, lv))
        if not carriers:
            return
        try:
            self._replace_images([{'carrier': c, 'members': [c], 'level_db': lv} for c, lv in carriers])
        except Exception:
            logger.exception('image check failed')

    def _replace_images(self, zones):
        """V20.1: images of a strong carrier that every tile there sees - at
        +-fs (aliases of the dongle's filter) and +-28.8 MHz (crystal) - are
        replaced by the floor around them. Sets self.images = [(MHz of the
        image, dB above its surroundings, MHz of the carrier), ...]."""
        spectrum = getattr(self.scanner, 'spectrum', None)
        if spectrum is None or not zones:
            return
        fs = self._sample_rate()
        ref = self._reference_floor_db()
        with spectrum.data_update_lock:
            sd = spectrum.sample_data
            f = np.asarray(sd['frequency'], dtype=np.float64)
            if f.size < 8:
                return
            y = np.asarray(sd['dbFS'], dtype=np.float64)
            changed = False
            for z in zones:
                c = z['carrier'] / 1e6
                ci = int(np.argmin(np.abs(f - c)))
                c_db = float(y[max(ci - 2, 0):ci + 3].max()) if abs(f[ci] - c) < 0.1 else None
                for off in (fs, -fs, XTAL_HZ, -XTAL_HZ):
                    p = c + off / 1e6
                    win = np.abs(f - p) <= IMAGE_HALF_HZ / 1e6
                    ring = (np.abs(f - p) > IMAGE_HALF_HZ / 1e6) & (np.abs(f - p) <= 2.5 * IMAGE_HALF_HZ / 1e6)
                    if np.count_nonzero(win) < 3 or np.count_nonzero(ring) < 4:
                        continue
                    if np.any(np.abs(f[win] - c) <= IMAGE_HALF_HZ / 1e6):
                        continue
                    base = float(np.median(y[ring]))
                    top = float(y[win].max())
                    if top < base + IMAGE_OVER_DB:
                        continue
                    if ref is not None and base > ref + IMAGE_OVER_DB:
                        continue            # inside a wide signal (DVB-T): leave it
                    if c_db is not None and z.get('level_db') is not None:
                        # V20.2: a carrier that did not clip: the image must
                        # be as much below it as the dongle's images are (a
                        # real signal there is left alone). In a clipping
                        # zone the tiles were merged, levels differ: no check.
                        lo_dbc, hi_dbc = ALIAS_DBC if abs(off) < 10e6 else XTAL_DBC
                        if not (lo_dbc <= top - c_db <= hi_dbc):
                            continue
                    idx = np.flatnonzero(win)
                    # the image is a copy of the carrier: as wide as it is
                    # above the floor around it
                    hi = idx[y[idx] > base + 3.]
                    if not hi.size:
                        continue
                    a, b = int(hi.min()), int(hi.max())
                    left = y[max(a - 3, 0):a]
                    right = y[b + 1:b + 4]
                    lv = float(np.median(left)) if left.size else base
                    rv = float(np.median(right)) if right.size else base
                    for i in range(a, b + 1):
                        t = (f[i] - f[a]) / (f[b] - f[a]) if f[b] > f[a] else 0.5
                        v = min(lv + (rv - lv) * t, y[i])
                        sd.data['magnitude'][i] = 10. ** (v / 10.)
                        sd.data['dbFS'][i] = v
                    self.images.append((round(p, 3), round(top - base, 1), round(c, 3)))
                    changed = True
        if changed:
            spectrum.set_data_updated()
            logger.info('images of strong carriers replaced by the floor around them: %s',
                        ', '.join('%.3f (+%.0f dB, of %.3f)' % t for t in self.images))

    def _reference_floor_db(self):
        """V20: usual floor of this scan - median of the tile floors (20th
        percentile) of tiles far from every strong carrier."""
        vals = [self._tile_floor_db(k) for k in self.far_tiles()]
        return float(np.median(vals)) if vals else None

    def sensitivity_loss(self):
        """V20: [(lo_mhz, hi_mhz, loss_db, carrier_mhz), ...] - bands next to
        strong carriers where weak signals may be missed, and by how many dB:
        points of the merged spectrum where every tile only had the floor of
        a low-gain reading (loss = that floor above the usual one), and tiles
        whose normal reading was made deaf by the carrier (loss = the deafness
        that was added back; the levels there are less certain). V20.1: the
        carrier named is the strongest of its zone; points of a wide signal
        (above the low-gain floor of the zone) are not "lost"."""
        low = [k for k, s in self.sample_sets.items() if s.powers_low is not None]
        if not low:
            return []
        fs = self._sample_rate()
        half = DEFAULT_TILE_KEEP_RATIO * fs / 1e6
        spans = []          # (lo, hi, loss) in MHz
        ref = self._reference_floor_db()
        spectrum = getattr(self.scanner, 'spectrum', None)
        if ref is not None and spectrum is not None:
            with spectrum.data_update_lock:
                sd = spectrum.sample_data
                f = np.asarray(sd['frequency'], dtype=np.float64).copy()
                y = np.asarray(sd['dbFS'], dtype=np.float64).copy()
                w = np.asarray(sd._get_counts(), dtype=np.float64).copy()
            if f.size and w.shape == f.shape:
                zfl = [(z, zf) for z, zf in (getattr(self, 'zone_floors', None) or []) if zf is not None]
                reach = NEIGHBOUR_HZ + fs
                for i in np.flatnonzero(w < 0.5):
                    if y[i] - ref < 3.:
                        continue
                    # V20.2: above the plateau of its zone it is a signal
                    # (dips of a DVB-T multiplex), not lost sensitivity
                    hit = [zf for z, zf in zfl if self.zone_of(f[i] * 1e6, [z], reach=reach) is not None]
                    if hit and y[i] > min(hit) + PLATEAU_MARGIN_DB:
                        continue
                    spans.append((f[i], f[i], y[i] - ref))
        for k, s in self.sample_sets.items():
            d = getattr(s, 'desense_db', 0.) or 0.
            if d >= DESENSE_MIN_DB:
                spans.append((k / 1e6 - half, k / 1e6 + half, d))
        if not spans:
            return []
        spans.sort()
        groups = []
        for lo, hi, loss in spans:
            if groups and lo <= groups[-1][1] + 0.5:
                g = groups[-1]
                g[1] = max(g[1], hi)
                g[2] = max(g[2], loss)
            else:
                groups.append([lo, hi, loss])
        try:
            lo_r, hi_r = [float(v) for v in self.scanner.config.scan_range]
        except Exception:
            lo_r, hi_r = -1e9, 1e9
        zones = self.carrier_zones()
        out = []
        for lo, hi, loss in groups:
            lo, hi = max(lo, lo_r), min(hi, hi_r)
            if hi < lo:
                continue
            mid = (lo + hi) / 2.
            c = None
            if zones:
                z = min(zones, key=lambda z: min(abs(mid * 1e6 - m) for m in z['members']))
                c = round(z['carrier'] / 1e6, 3)
            out.append((lo, hi, loss, c))
        return out

    def report_overload(self):
        """Warn about tiles where the ADC clipped (signal too strong).
        Sets self.overload = [(lo_mhz, hi_mhz, max_ratio), ...] and (V20)
        self.sensitivity, self.overload_note (for the status line) and
        self.overload_warn."""
        self.overload = []
        self.remeasured = []
        self.sensitivity = []
        self.overload_note = ''
        self.overload_warn = False
        try:
            self._report_overload()
        finally:
            self._note_sensitivity()

    def _note_sensitivity(self):
        """V20: one clear line for the status line: which band lost
        sensitivity and by how much."""
        try:
            self.sensitivity = self.sensitivity_loss()
        except Exception:
            logger.exception('sensitivity estimate failed')
            self.sensitivity = []
        still = [k for k, s in self.sample_sets.items()
                 if s.powers_low is not None and not s.neighbour
                 and getattr(s, 'clip_ratio', 0.) >= OVERLOAD_RATIO]
        parts = []
        for lo, hi, loss, c in self.sensitivity[:4]:
            near = ' (signal %.3f)' % c if c is not None else ''
            parts.append('%.1f-%.1f MHz up to %.0f dB less sensitive%s' % (lo, hi, loss, near))
        if len(self.sensitivity) > 4:
            parts.append('%d more band(s)' % (len(self.sensitivity) - 4))
        worst = max([loss for _, _, loss, _ in self.sensitivity] or [0.])
        note = ''
        if parts:
            note = 'overload next to a strong signal: ' + ', '.join(parts)
        if still:
            note = (note + '; ' if note else '') + \
                '%d tile(s) clipped even at the lowest gain - move the antenna away' % len(still)
        images = getattr(self, 'images', None) or []
        if images:
            note = (note + '; ' if note else '') + 'images of the strong signal removed at ' + \
                ', '.join('%.3f' % p for p, _, _ in images[:4]) + ' MHz (not measurable there)'
        if getattr(self, 'neighbours_skipped', 0):
            note = (note + '; ' if note else '') + \
                '%d tile(s) next to strong carriers not checked' % self.neighbours_skipped
        self.overload_note = note
        self.overload_warn = bool(still) or worst >= LOSS_WARN_DB
        # V20.2: for the graph (shaded bands, image marks)
        spectrum = getattr(self.scanner, 'spectrum', None)
        if spectrum is not None:
            try:
                spectrum.loss_bands = [{'lo': float(lo), 'hi': float(hi), 'db': float(loss),
                                        'signal': float(c) if c is not None else 0.}
                                       for lo, hi, loss, c in self.sensitivity]
                spectrum.image_marks = [{'f': float(p), 'of': float(c)} for p, _, c in images]
            except Exception:
                logger.exception('graph marks failed')
        if note:
            (logger.warning if self.overload_warn else logger.info)(
                'Sensitivity: %s (weak signals below that floor are not seen there)', note)

    def _report_overload(self):
        def first(s):
            r = getattr(s, 'clip_ratio_first', None)
            return getattr(s, 'clip_ratio', 0.) if r is None else r
        bad = sorted((k, first(s)) for k, s in self.sample_sets.items()
                     if first(s) >= OVERLOAD_RATIO)
        if not bad:
            return
        fixed = [k for k, s in self.sample_sets.items() if getattr(s, 'gain_drop', 0.) > 0
                 and not s.neighbour and getattr(s, 'clip_ratio', 0.) < OVERLOAD_RATIO]
        lowered = [s.gain_drop for s in self.sample_sets.values()
                   if getattr(s, 'gain_drop', 0.) > 0 and not s.neighbour]
        nb = len(getattr(self, 'neighbours', None) or [])
        nb_text = ', %d neighbour tile(s) read again too' % nb if nb else ''
        rr = len(getattr(self, 'zone_rereads', None) or [])
        if rr:
            nb_text += ', %d with the gain of the zone' % rr
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
                logger.info('Overload at %s MHz: %d tile(s) measured again with %.0f-%.0f dB less gain%s '
                            '(levels right)', where, len(fixed), min(drops), max(drops), nb_text)
                return
        if lowered:
            where = ', '.join('%.1f-%.1f' % (lo, hi) for lo, hi, _ in self.overload[:6])
            logger.warning('Overload at %s MHz: %d tile(s) still clipped even with %.0f dB less gain: '
                           'move the antenna away from the transmitters', where, len(still),
                           max(lowered))
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
