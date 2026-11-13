from wwb_scanner.utils.config import Config

class ScanConfig(Config):
    DEFAULTS = dict(
        scan_range=[400., 900.],
        scan_ranges=None,         # V18: several ranges [[lo, hi], ...] in one scan
        gain_calibration=False,   # V20.5: survey + gain step calibration (gain_cal)
        save_raw_values=False,
    )
    def __init__(self, initdict=None, **kwargs):
        kwargs.setdefault('_child_conf_keys', ['device', 'sampling', 'processing'])
        super(ScanConfig, self).__init__(initdict, **kwargs)
        if 'device' not in self._data:
            self['device'] = DeviceConfig()
        if 'sampling' not in self._data:
            self['sampling'] = SamplingConfig()
        if 'processing' not in self._data:
            self['processing'] = ProcessingConfig()
    def _deserialize_child(self, key, val, cls=None):
        if key == 'device':
            cls = DeviceConfig
        elif key == 'sampling':
            cls = SamplingConfig
        elif key == 'processing':
            cls = ProcessingConfig
        return super(ScanConfig, self)._deserialize_child(key, val, cls)

class DeviceConfig(Config):
    DEFAULTS = dict(
        serial_number=None,
        gain=30.,
        freq_correction=0,
        is_remote=False,
        remote_hostname='127.0.0.1',
        remote_port=1235,
    )

class SamplingConfig(Config):
    DEFAULTS = dict(
        sample_rate=2.048e6,
        sweep_overlap_ratio=.5,
        sweeps_per_scan=20,
        samples_per_sweep=8192,
        window_size=None,
        fft_size=1024,
        window_type='boxcar',
        rtl_bin_size=0.025,
        rtl_crop=50,
        rtl_fir_size=4,
        auto_gain=False,          # V17: re-measure clipped tiles with lower gain
        verify_peaks=False,       # V21.2: check peaks next to strong carriers with less gain (window: on)
    )

class ProcessingConfig(Config):
    DEFAULTS = dict(
        smoothing_enabled=False,
        smoothing_factor=1.,
        scaling_enabled=True,
        scaling_min_db=-140.,
        scaling_max_db=-50.,
        clean_ripple=False,       # V17: remove the tile ripple (no calibration needed)
        clean_spurs=False,        # V17: hide the dongle's own carriers (V20.9: k x 14.4 MHz, up to +20 dB)
        clean_images=False,       # V20.9: replace images of strong carriers by the floor (off: only marked)
        clean_floor=False,        # V17: level the floor (needs a calibration)
    )
