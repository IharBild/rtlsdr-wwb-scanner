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

# The tested configuration in the units of the scanner core.
TESTED = dict(
    sample_rate=2.4e6,
    gain=33.8,
    window_type='hann',
    window_size=96,
    samples_per_sweep=2048,
    sweeps_per_scan=40,
    sweep_overlap_ratio=0.65,
    auto_gain=True,
    verify_peaks=True,
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
    def check(self):
        """None, or why these settings can not be scanned (text)."""
        for lo, hi in self.segments():
            if not (lo == lo and hi == hi):          # NaN: empty field
                return 'Start and End must be numbers'
            if lo >= hi:
                return 'Start (%g MHz) must be below End (%g MHz)' % (lo, hi)
        s = self.sampling
        if not (s.sample_rate > 0 and s.samples_per_sweep > 0 and s.sweeps_per_scan > 0):
            return 'sample rate, samples per sweep and sweeps per scan must be above 0'
        if not (0 < s.sweep_overlap_ratio <= 1):
            return 'overlap ratio must be above 0 and at most 1'
        return None


class DeviceConfig(Config):
    DEFAULTS = dict(
        serial_number=None,
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
        window_type=TESTED['window_type'],
        auto_gain=TESTED['auto_gain'],          # V17: re-measure clipped tiles with lower gain
        verify_peaks=TESTED['verify_peaks'],    # V21.2: check peaks next to strong carriers with less gain
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
