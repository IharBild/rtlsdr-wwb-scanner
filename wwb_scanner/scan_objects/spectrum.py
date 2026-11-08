import threading
import datetime
import time

import numpy as np
from scipy import signal

from wwb_scanner.core import JSONMixin
from wwb_scanner.utils import dbmath
from wwb_scanner.utils.dbstore import db_store
from wwb_scanner.utils.color import Color
from wwb_scanner.scan_objects import SampleArray, Sample, TimeBasedSample
try:
    from wwb_scanner import file_handlers
except ImportError:
    file_handlers = None
try:
    from wwb_scanner.ui.plots import SpectrumPlot
except ImportError:
    SpectrumPlot = None

EPOCH = datetime.datetime(1970, 1, 1)

def get_file_handlers():
    global file_handlers
    if file_handlers is None:
        from wwb_scanner import file_handlers as _file_handlers
        file_handlers = _file_handlers
    return file_handlers
def get_importer():
    return get_file_handlers().BaseImporter
def get_exporter():
    return get_file_handlers().BaseExporter

def get_spectrum_plot():
    global SpectrumPlot
    if SpectrumPlot is None:
        from wwb_scanner.ui.plots import SpectrumPlot as _SpectrumPlot
        SpectrumPlot = _SpectrumPlot
    return SpectrumPlot

class Spectrum(JSONMixin):
    _serialize_attrs = [
        'name', 'color', 'timestamp_utc', 'step_size',
        'center_frequencies', 'scan_config_eid',
    ]
    DEFAULT_COLOR = Color({'r':0, 'g':1, 'b':0, 'a':1})
    def __init__(self, **kwargs):
        self.name = kwargs.get('name')
        self.eid = kwargs.get('eid')
        eid = kwargs.get('scan_config_eid')
        config = kwargs.get('scan_config')
        if config is not None:
            self.scan_config = config
        elif eid is not None:
            self.scan_config_eid = eid

        color = kwargs.get('color')
        if color is None:
            color = self.DEFAULT_COLOR
        if isinstance(color, Color):
            self.color = color.copy()
        else:
            self.color = Color(color)

        datetime_utc = kwargs.get('datetime_utc')
        timestamp_utc = kwargs.get('timestamp_utc')
        if datetime_utc is not None:
            self.datetime_utc = datetime_utc
        else:
            if timestamp_utc is None:
                timestamp_utc = time.time()
            self.timestamp_utc = timestamp_utc
        self.step_size = kwargs.get('step_size')
        self.data_updated = threading.Event()
        self.data_update_lock = threading.RLock()
        self.samples = {}
        self.sample_data = SampleArray()
        self.center_frequencies = kwargs.get('center_frequencies', [])
    @property
    def datetime_utc(self):
        return getattr(self, '_datetime_utc', None)
    @datetime_utc.setter
    def datetime_utc(self, value):
        if value == self.datetime_utc:
            return
        if value is None:
            return
        self._datetime_utc = value
        td = value - EPOCH
        timestamp = td.total_seconds()
        if timestamp != self.timestamp_utc:
            self.timestamp_utc = timestamp
    @property
    def timestamp_utc(self):
        return getattr(self, '_timestamp_utc', None)
    @timestamp_utc.setter
    def timestamp_utc(self, value):
        if value == self.timestamp_utc:
            return
        if value is None:
            return
        self._timestamp_utc = value
        dt = datetime.datetime.utcfromtimestamp(value)
        if dt != self.datetime_utc:
            self.datetime_utc = dt
    @property
    def scan_config(self):
        return getattr(self, '_scan_config', None)
    @scan_config.setter
    def scan_config(self, value):
        if value == self.scan_config:
            return
        self._scan_config = value
        if value.get('eid') is None:
            eid = self.scan_config_eid
            if eid is not None:
                value.eid = eid
    @property
    def scan_config_eid(self):
        eid = getattr(self, '_scan_config_eid', None)
        if eid is None:
            config = self.scan_config
            if config is not None:
                eid = self._scan_config_eid = config.get('eid')
        return eid
    @scan_config_eid.setter
    def scan_config_eid(self, value):
        if value == self.scan_config_eid:
            return
        self._scan_config_eid = value
        if value is None:
            return
        config = self.scan_config
        if config is None:
            config = self._scan_config = db_store.get_scan_config(eid=value)
        else:
            if config.get('eid') is None:
                config.eid = value
    def _deserialize(self, **kwargs):
        sample_data = kwargs.get('sample_data')
        samples = kwargs.get('samples')
        if sample_data is not None:
            self.sample_data = sample_data
            self.samples.clear()
            skwargs = dict(spectrum=self, init_complete=True)
            for f in sample_data.frequency:
                skwargs['frequency'] = f
                sample = self._build_sample(**skwargs)
                self.samples[f] = sample
        elif samples is not None:
            if isinstance(samples, dict):
                for key, data in samples.items():
                    if isinstance(data, dict):
                        self.add_sample(**data)
                    else:
                        self.add_sample(frequency=key, dbFS=data)
            else:
                for sample_kwargs in samples:
                    self.add_sample(**sample_kwargs)
    @classmethod
    def import_from_file(cls, filename):
        importer = get_importer()
        return importer.import_file(filename)
    def export_to_file(self, **kwargs):
        exporter = get_exporter()
        kwargs['spectrum'] = self
        exporter.export_to_file(**kwargs)
    def show_plot(self):
        plot_cls = get_spectrum_plot()
        plot = plot_cls(spectrum=self)
        plot.build_plot()
        return plot
    def smooth(self, N):
        with self.data_update_lock:
            if N % 2 != 0:
                N += 1
            self.sample_data.smooth(N)
        self.set_data_updated()
    def interpolate(self, spacing=0.025):
        with self.data_update_lock:
            self.sample_data.interpolate(spacing)
            self.samples.clear()
            kwargs = {'spectrum':self, 'init_complete':True}
            for freq in self.sample_data.frequency:
                kwargs['frequency'] = freq
                sample = self._build_sample(**kwargs)
                self.samples[freq] = sample
            self.step_size = spacing
        self.set_data_updated()
    def crop(self, start_freq, end_freq):
        """Remove points outside of ``start_freq`` - ``end_freq`` (MHz, inclusive)."""
        with self.data_update_lock:
            self.sample_data.crop(start_freq, end_freq)
            self.samples.clear()
            kwargs = {'spectrum':self, 'init_complete':True}
            for freq in self.sample_data.frequency:
                kwargs['frequency'] = freq
                self._build_sample(**kwargs)
        self.set_data_updated()
    def scale(self, min_dB, max_dB):
        with self.data_update_lock:
            if self.sample_data.size == 0:
                return
            y = self.sample_data['dbFS']
            ymin = y.min()
            ymax = y.max()
            y -= ymin
            out_scale = max_dB - min_dB

            span = y.max()
            if span == 0:
                return
            y /= span
            y *= out_scale
            y += min_dB
            self.sample_data['magnitude'] = dbmath.from_dB(y)
            self.sample_data['dbFS'] = y
        self.set_data_updated()
    def add_sample(self, **kwargs):
        f = kwargs.get('frequency')
        iq = kwargs.get('iq')
        if iq is not None and isinstance(iq, list):
            iq = complex(*(float(v) for v in iq))
            kwargs['iq'] = iq
        if kwargs.get('is_center_frequency') and f not in self.center_frequencies:
            self.center_frequencies.append(f)
        if f in self.samples:
            sample = self.samples[f]
            if kwargs.get('force_magnitude'):
                self.sample_data.set_fields(**kwargs)
            return sample
        if len(self.samples) and f < max(self.samples.keys()):
            if not kwargs.get('force_lower_freq', True):
                return
        with self.data_update_lock:
            if f not in self.sample_data['frequency']:
                self.sample_data.set_fields(**kwargs)
                skwargs = {'spectrum':self, 'init_complete':True, 'frequency':f}
                sample = self._build_sample(**skwargs)
                self.samples[f] = sample
        self.set_data_updated()
        return sample
    def add_sample_set(self, **kwargs):
        with self.data_update_lock:
            self._add_sample_set(**kwargs)
        self.set_data_updated()
    def _add_sample_set(self, **kwargs):
        weight = kwargs.pop('weight', 1.0)
        force_lower_freq = kwargs.get('force_lower_freq')
        a = SampleArray.create(**kwargs)

        sdata = self.sample_data

        if not force_lower_freq and sdata['frequency'].size:
            r_ix = np.flatnonzero(np.greater_equal(a['frequency'], [sdata['frequency'].max()]))
            a = a[r_ix]
        self.sample_data.insert_sorted(a, weight)

        skwargs = {'spectrum':self, 'init_complete':True}
        for f in a['frequency']:
            if f in self.samples:
                continue
            skwargs['frequency'] = f
            sample = self._build_sample(**skwargs)
            self.samples[f] = sample
    def _build_sample(self, **kwargs):
        sample = Sample(**kwargs)
        self.samples[sample.frequency] = sample
        return sample
    def iter_frequencies(self):
        for key in sorted(self.samples.keys()):
            yield key
    def iter_samples(self):
        for key in self.iter_frequencies():
            yield self.samples[key]
    def on_sample_change(self, **kwargs):
        sample = kwargs.get('sample')
        if sample.frequency not in self.samples:
            return
        self.set_data_updated()
    def set_data_updated(self):
        with self.data_update_lock:
            self.data_updated.set()
    def save_to_dbstore(self):
        db_store.add_scan(self)
    def update_dbstore(self, *attrs):
        if self.eid is None:
            return
        if not len(attrs):
            attrs = self._serialize_attrs
        d = {attr:getattr(self, attr) for attr in attrs}
        db_store.update_scan(self.eid, **d)
    @classmethod
    def from_dbstore(cls, dbdata=None, eid=None):
        if dbdata is None:
            assert eid is not None
            dbdata = db_store.get_scan(eid)
        else:
            from wwb_scanner.utils.dbstore import doc_id
            eid = doc_id(dbdata)
            if dbdata.get('sample_data') is None and dbdata.get('sample_data_file'):
                dbdata = db_store.get_scan(eid)       # V14: points in scans/*.npz
        dbdata = dict(dbdata)
        dbdata.pop('sample_data_file', None)
        return cls.from_json(dbdata, eid=eid)
    def _serialize(self):
        d = {attr: getattr(self, attr) for attr in self._serialize_attrs}
        d['sample_data'] = self.sample_data
        return d

class TimeBasedSpectrum(Spectrum):
    def _build_sample(self, **kwargs):
        sample = TimeBasedSample(**kwargs)
        if sample.frequency not in self.samples:
            self.samples[sample.frequency] = {}
        self.samples[sample.frequency][sample.timestamp] = sample
        return sample
    def iter_samples(self):
        samples = self.samples
        last_ts = None
        for key in self.iter_frequencies():
            if last_ts is None:
                last_ts = min(samples[key])
                sample = samples[key][last_ts]
            else:
                if last_ts in samples[key]:
                    sample = samples[key][last_ts]
                else:
                    l = [ts for ts in samples[key] if last_ts < ts]
                    if not len(l):
                        sample = None
                    else:
                        last_ts = min(l)
                        sample = samples[key][last_ts]
            if sample is None:
                break
            yield sample

def compare_spectra(spec1, spec2):
    diff_spec = Spectrum()
    for sample in spec1.iter_samples():
        other_sample = spec2.samples.get(sample.frequency)
        if other_sample is None:
            continue
        magnitude = sample.magnitude - other_sample.magnitude
        diff_spec.add_sample(frequency=sample.frequency, magnitude=magnitude)
    return diff_spec


# HDR (V19): low-gain values above this are "strong" (measured cleanly at low
# gain); below it the signal is weak and comes from the high-gain scan.
HDR_MERGE_THRESHOLD_DBFS = -80.0
# Around each strong signal this zone is taken from the low-gain scan, so its
# intermodulation spurs (only present at high gain) are not re-introduced.
# The zone is asymmetric: the dongle's spurs reach further below the carrier
# (up to ~5.3 MHz) than above it (~3 MHz), and the TV channel starts at
# ~+5.5 MHz, so the "above" side is kept shorter.
HDR_PROTECT_LOW_MHZ = 5.5
HDR_PROTECT_HIGH_MHZ = 4.0
HDR_BLEND_MHZ = 1.0        # smooth transition at the zone edges (no hard step)


def merge_hdr(high, low, threshold_dbFS=HDR_MERGE_THRESHOLD_DBFS,
              protect_low_mhz=HDR_PROTECT_LOW_MHZ, protect_high_mhz=HDR_PROTECT_HIGH_MHZ,
              blend_mhz=HDR_BLEND_MHZ):
    """Merge a high-gain scan (weak signals visible) and a low-gain scan
    (strong signals clean) into one Spectrum. Strong signals and their
    neighbourhood come from the low-gain scan (clean, no intermodulation
    spurs); elsewhere the high-gain value is used (weak signals visible). The
    transition is blended so the noise floor changes smoothly, not in a step."""
    hd = high.sample_data
    ld = low.sample_data
    freq = np.asarray(hd['frequency'], dtype=np.float64)
    high_db = np.asarray(hd['dbFS'], dtype=np.float64)
    low_freq = np.asarray(ld['frequency'], dtype=np.float64)
    low_db = np.asarray(ld['dbFS'], dtype=np.float64)
    hkeys = SampleArray._freq_keys(freq)
    lkeys = SampleArray._freq_keys(low_freq)
    idx = np.searchsorted(hkeys, lkeys)
    valid = (idx < hkeys.size) & (hkeys[idx] == lkeys)
    aligned_low = np.full(freq.size, np.nan)
    aligned_low[idx[valid]] = low_db[valid]
    strong = np.isfinite(aligned_low) & (aligned_low > threshold_dbFS)
    blend = max(float(blend_mhz), 1e-6)
    # weight of the low-gain scan: 1 inside the protection zone, 0 outside,
    # linear blend in between
    weight = np.zeros(freq.size)
    for sf in freq[strong]:
        off = freq - sf
        w = np.where(
            off >= 0,
            np.clip((protect_high_mhz + blend - off) / blend, 0.0, 1.0),
            np.clip((protect_low_mhz + blend + off) / blend, 0.0, 1.0),
        )
        weight = np.maximum(weight, w)
    merged_db = high_db.copy()
    finite = np.isfinite(aligned_low)
    use = (weight > 0) & finite
    merged_db[use] = aligned_low[use] * weight[use] + high_db[use] * (1.0 - weight[use])
    merged = Spectrum()
    merged.name = high.name
    merged.step_size = high.step_size
    merged.add_sample_set(frequency=freq, dbFS=merged_db, force_lower_freq=True)
    if high.scan_config is not None:
        merged.scan_config = high.scan_config
    return merged
