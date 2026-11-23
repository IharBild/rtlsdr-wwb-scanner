"""Settings of a scan.

V22.0 (audit 16.11, item 1): one schema for the whole program.

- The default values ARE the tested configuration (every test and every
  measurement on the user's RTL-SDR Blog V4 since V20.8): 2400 kHz, gain
  33.8 dB, Hann window of 96 points (25 kHz bins), 2048 x 40 samples per
  tile, overlap 0.65 (tile step 775 kHz), overload check, peak check and the
  clean scan switched on. Before V22.0 the core still had the values of the
  original project (2048 kHz, boxcar, 8192 x 20, overlap 0.5): a scan
  started without the window got settings nobody had tested.
- The window (ui/pyside/scanner.py FACTORY) and the QML take their values
  from here (``TESTED``); a test checks the remaining QML copies.
- Only the keys below exist. A key with a typo is an error; keys of earlier
  versions found in stored settings (fft_size, rtl_crop, smoothing ...) are
  dropped when the settings are read.
"""
from wwb_scanner.utils.config import Config

# V22.8 (external audit B7): the range a scan may cover, MHz. RTL-SDR Blog V4
# datasheet: 500 kHz to 1.766 GHz (below 28.8 MHz through its built-in
# upconverter, the driver does it; the R828D tuner itself: 24-1766 MHz).
# main.MAX_TUNER_FREQ_HZ is the same upper limit.
MIN_FREQ_MHZ = 0.5
MAX_FREQ_MHZ = 1766.

# V22.9 (external audit P2-GAIN1, P2-RATE1, P2-FCORR1): what the dongle
# takes. RTL2832U sample rates (librtlsdr): 225-300 kHz and 900-3200 kHz.
# Above 2.56 MS/s the RTL2832U drops samples (RTL-SDR Blog datasheet:
# "2.56 MHz stable (up to 3.2 MHz with drops)"); at 3.2 MS/s the levels were
# ~17 dB lower than at 2.4 MS/s (audit pass 3, 22.11.2026) - a warning,
# the rate stays settable.
MIN_GAIN_DB = 0.
MAX_GAIN_DB = 49.6
SAMPLE_RATE_RANGES_HZ = ((225001., 300000.), (900001., 3200000.))
STABLE_SAMPLE_RATE_HZ = 2.56e6
MAX_PPM = 300

# V22.10 (decision of 23.11.2026): the window size is set for this sample
# rate; at another rate the window grows or shrinks with it, so a point of
# the scan stays as wide (96 points at 2.4 MS/s = 25 kHz -> 128 at 3.2,
# 82 at 2.048, 102 at 2.56 MS/s). Off: the window size is used as it is.
WINDOW_REF_RATE_HZ = 2.4e6


def effective_window(window_size, sample_rate, follows=True):
    """The FFT window used at ``sample_rate`` for the window size set at
    WINDOW_REF_RATE_HZ (V22.10)."""
    w = int(window_size)
    if not follows:
        return w
    try:
        fs = float(sample_rate)
    except (TypeError, ValueError):
        return w
    if fs <= 0 or abs(fs - WINDOW_REF_RATE_HZ) < 1.:
        return w
    return max(8, int(round(w * fs / WINDOW_REF_RATE_HZ)))


# The tested configuration in the units of the scanner core.
TESTED = dict(
    sample_rate=2.4e6,
    gain=33.8,
    window_type='hann',
    window_size=96,
    window_follows_rate=True,   # V22.10: 25 kHz points at every sample rate
    samples_per_sweep=2048,
    sweeps_per_scan=40,
    sweep_overlap_ratio=0.65,
    auto_gain=True,
    verify_peaks=True,
    band_gain=True,         # V22.7: own gain per band (A+); V22.12: on (noise check, checked on the V4 23.11.2026)
    clean_ripple=True,
    clean_spurs=True,
    clean_images=False,
    clean_floor=True,
)


class ScanConfig(Config):
    DEFAULTS = dict(
        scan_range=[470., 536.],
        scan_ranges=None,         # V18: several ranges [[lo, hi], ...] in one scan
        gain_calibration=False,   # V20.5: survey + gain step calibration (gain_cal)
    )
    # stored with the settings of a scan in db.json
    META_KEYS = ('eid', 'datetime', 'name')
    CHILD_KEYS = ('device', 'sampling', 'processing')
    def __init__(self, initdict=None, **kwargs):
        super(ScanConfig, self).__init__(initdict, **kwargs)
        if 'device' not in self._data:
            self['device'] = DeviceConfig()
        if 'sampling' not in self._data:
            self['sampling'] = SamplingConfig()
        if 'processing' not in self._data:
            self['processing'] = ProcessingConfig()
    def _deserialize_child(self, key, val, cls=None):
        cls = {'device': DeviceConfig, 'sampling': SamplingConfig,
               'processing': ProcessingConfig}[key]
        return super(ScanConfig, self)._deserialize_child(key, val, cls)
    def segments(self):
        """[(lo, hi)] MHz of this scan."""
        if self.get('scan_ranges'):
            return [(float(a), float(b)) for a, b in self.scan_ranges]
        lo, hi = self.scan_range
        return [(float(lo), float(hi))]
    def out_of_range(self):
        """V22.8 (external audit B7): None, or the text when a range of this
        scan is outside what the dongle tunes (0, -100 or 2000 MHz were
        accepted and scanned). Ranges that are not numbers are left to check()."""
        try:
            segs = self.segments()
        except (TypeError, ValueError):
            return None
        inf = float('inf')
        for lo, hi in segs:
            if lo != lo or hi != hi or abs(lo) == inf or abs(hi) == inf:
                continue
            if lo < MIN_FREQ_MHZ or hi > MAX_FREQ_MHZ:
                return ('The dongle scans %g-%g MHz: %g-%g MHz is outside that range.'
                        % (MIN_FREQ_MHZ, MAX_FREQ_MHZ, lo, hi))
        return None
    def device_problem(self):
        """V22.9: None, or why the dongle can not take the gain, the sample
        rate or the frequency correction of these settings (text). A gain
        that was not a number became 0 dB, 100 dB became 49.6 dB, 100000 ppm
        was passed on."""
        def num(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if v == v and abs(v) != float('inf') else None
        g = self.device.get('gain')
        if g is not None:
            gv = num(g)
            if gv is None or not (MIN_GAIN_DB - 0.05 <= gv <= MAX_GAIN_DB + 0.05):
                return 'The gain must be %g-%g dB (it is %r).' % (MIN_GAIN_DB, MAX_GAIN_DB, g)
        r = self.sampling.get('sample_rate')
        rv = num(r)
        if rv is not None and rv > 0 and not any(lo - 1 <= rv <= hi + 1
                                                 for lo, hi in SAMPLE_RATE_RANGES_HZ):
            return ('The dongle takes a sample rate of 225-300 kHz or 900-3200 kHz '
                    '(it is %g kHz).' % (rv / 1e3))
        c = self.device.get('freq_correction')
        if c not in (None, 0):
            cv = num(c)
            if cv is None or abs(cv) > MAX_PPM or cv != int(cv):
                return ('The frequency correction must be a whole number of ppm, at most %d '
                        '(it is %r).' % (MAX_PPM, c))
        return None
    def warnings(self):
        """V22.9 (external audit P3-14): settings that scan, but not well."""
        out = []
        try:
            r = float(self.sampling.get('sample_rate'))
        except (TypeError, ValueError):
            r = 0.
        if r > STABLE_SAMPLE_RATE_HZ + 1:
            out.append('Sample rate %.3f MS/s is above %.2f MS/s, the stable limit of the RTL2832U: '
                       'the dongle may drop samples and the levels are not comparable with scans '
                       'at 2.4 MS/s (at 3.2 MS/s they were about 17 dB lower).'
                       % (r / 1e6, STABLE_SAMPLE_RATE_HZ / 1e6))
        return out
    def check(self):
        """None, or why these settings can not be scanned (text)."""
        inf = float('inf')
        for lo, hi in self.segments():
            if not (lo == lo and hi == hi) or abs(lo) == inf or abs(hi) == inf:   # NaN: empty field
                return 'Start Freq and End Freq must be numbers (MHz).'
            if hi <= lo:
                return 'End Freq (%g MHz) must be above Start Freq (%g MHz).' % (hi, lo)
        why = self.out_of_range()
        if why:
            return why
        s = self.sampling
        if not (s.sample_rate > 0 and s.samples_per_sweep > 0 and s.sweeps_per_scan > 0):
            return 'sample rate, samples per sweep and sweeps per scan must be above 0'
        if not (0 < s.sweep_overlap_ratio <= 1):
            return 'overlap ratio must be above 0 and at most 1'
        return self.device_problem()


class DeviceConfig(Config):
    DEFAULTS = dict(
        serial_number=None,
        device_index=None,      # V22.4: used when two dongles have the same serial number
        gain=TESTED['gain'],
        freq_correction=0,
    )


class SamplingConfig(Config):
    DEFAULTS = dict(
        sample_rate=TESTED['sample_rate'],
        sweep_overlap_ratio=TESTED['sweep_overlap_ratio'],
        sweeps_per_scan=TESTED['sweeps_per_scan'],
        samples_per_sweep=TESTED['samples_per_sweep'],
        window_size=TESTED['window_size'],
        window_follows_rate=TESTED['window_follows_rate'],   # V22.10
        window_type=TESTED['window_type'],
        auto_gain=TESTED['auto_gain'],          # V17: re-measure clipped tiles with lower gain
        verify_peaks=TESTED['verify_peaks'],    # V21.2: check peaks next to strong carriers with less gain
        band_gain=TESTED['band_gain'],          # V22.7: own gain per band (scanner/gain_plan.py)
        tile_keep_ratio=None,                   # None: sample_processing.DEFAULT_TILE_KEEP_RATIO
        tile_center_gap_hz=None,                # None: sample_processing.DEFAULT_TILE_CENTER_GAP_HZ
    )


class ProcessingConfig(Config):
    DEFAULTS = dict(
        clean_ripple=TESTED['clean_ripple'],    # V17: remove the tile ripple (no calibration needed)
        clean_spurs=TESTED['clean_spurs'],      # V17: hide the dongle's own carriers (V20.9: k x 14.4 MHz, up to +20 dB)
        clean_images=TESTED['clean_images'],    # V20.9: replace images of strong carriers by the floor (off: only marked)
        clean_floor=TESTED['clean_floor'],      # V17: level the floor (needs a calibration)
    )


def raw_processing():
    """Processing switches for a scan kept exactly as measured
    (calibrations, tests)."""
    return {k: False for k in ProcessingConfig.DEFAULTS}
