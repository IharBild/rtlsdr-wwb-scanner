"""A scan: its points (SampleArray) and what is known about it.

V22.0 (audit 16.11, items 2 and 5):
- no Sample object per point any more (69 441 objects = 13 MB for a full
  scan, read by nothing but the removed matplotlib plot);
- the history (utils/dbstore.py) and the file handlers are imported only
  when used, so the core can work without them;
- smoothing, scaling and interpolation are gone (switched off in the window
  since V21.0: they changed the levels after the scan was saved).
"""
import threading
import datetime
import time

import numpy as np

from wwb_scanner.core import JSONMixin
from wwb_scanner.utils.color import Color
from wwb_scanner.scan_objects.samplearray import SampleArray

EPOCH = datetime.datetime(1970, 1, 1)


def get_file_handlers():
    from wwb_scanner import file_handlers
    return file_handlers
def get_importer():
    return get_file_handlers().BaseImporter
def get_exporter():
    return get_file_handlers().BaseExporter


def _db_store():
    from wwb_scanner.utils.dbstore import db_store
    return db_store


class Spectrum(JSONMixin):
    _serialize_attrs = [
        'name', 'color', 'timestamp_utc', 'step_size', 'scan_config_eid', 'pass_view',
    ]
    DEFAULT_COLOR = Color({'r':0, 'g':1, 'b':0, 'a':1})
    def __init__(self, **kwargs):
        self.name = kwargs.get('name')
        self.eid = kwargs.get('eid')
        # V20.9: what the clean scan did (saved with the scan since V17) is
        # read back, so a scan opened from the history shows its spur marks
        info = kwargs.get('cleanup')
        if isinstance(info, dict):
            self.cleanup_info = info
        # V22.8 (external audit B1): marks and values as measured (before the
        # clean scan) come back from the history with the scan
        marks = kwargs.get('marks')
        if isinstance(marks, dict):
            from wwb_scanner.utils.dbstore import MARK_KEYS
            for k in MARK_KEYS:
                v = marks.get(k)
                if isinstance(v, list):
                    setattr(self, k, [dict(x) for x in v if isinstance(x, dict)])
        raw = kwargs.get('raw_dbFS')
        if raw is not None:
            self.raw_dbFS = raw
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
        self.sample_data = SampleArray()
        # V22.2: the passes of a scan of several passes (scan_objects/passes.py)
        # and which of their views the points are; None: a scan of one pass
        self.passes = kwargs.get('passes')
        self.pass_view = kwargs.get('pass_view')
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
        # V21.3: utcfromtimestamp() is deprecated since Python 3.12; same naive UTC value
        dt = datetime.datetime.fromtimestamp(value, datetime.timezone.utc).replace(tzinfo=None)
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
            stored = _db_store().get_scan_config(eid=value)
            if stored is not None:
                from wwb_scanner.scanner.config import ScanConfig
                config = self._scan_config = ScanConfig(dict(stored), eid=value)
        else:
            if config.get('eid') is None:
                config.eid = value
    def _deserialize(self, **kwargs):
        sample_data = kwargs.get('sample_data')
        if sample_data is not None:
            if not isinstance(sample_data, SampleArray):
                sample_data = SampleArray(sample_data)
            self.sample_data = sample_data
    @classmethod
    def import_from_file(cls, filename):
        importer = get_importer()
        return importer.import_file(filename)
    def export_to_file(self, **kwargs):
        exporter = get_exporter()
        kwargs['spectrum'] = self
        exporter.export_to_file(**kwargs)
    def set_passes(self, passes, view='max_hold'):
        """V22.2: keep ``passes`` (ScanPasses) and make the points the view
        ``view`` of them ('max_hold', 'average', 'last')."""
        f, y = passes.view(view)
        with self.data_update_lock:
            self.passes = passes
            self.pass_view = view
            self.sample_data = SampleArray.create(frequency=f, dbFS=y)
        self.set_data_updated()
    def show_view(self, view):
        """Points from another view of the same passes (nothing measured is
        changed: the passes stay as they are)."""
        if self.passes is None:
            raise ValueError('a scan of one pass has no views')
        self.set_passes(self.passes, view)
    def keep_ranges(self, ranges):
        """V18: remove points outside the scanned ranges [(lo, hi)] MHz."""
        with self.data_update_lock:
            self.sample_data.keep_ranges(ranges)
        self.set_data_updated()
    def crop(self, start_freq, end_freq):
        """Remove points outside of ``start_freq`` - ``end_freq`` (MHz, inclusive)."""
        with self.data_update_lock:
            self.sample_data.crop(start_freq, end_freq)
        self.set_data_updated()
    def add_sample_set(self, **kwargs):
        with self.data_update_lock:
            self._add_sample_set(**kwargs)
        self.set_data_updated()
    def rebuild(self, tiles):
        """V20: build the points again from ``tiles`` (keyword dicts as for
        add_sample_set, in frequency order). The new points are made first
        and swapped in at the end, so the window is not held up meanwhile."""
        tiles = [dict(kw) for kw in tiles]
        sd = SampleArray()
        if tiles and all(kw.get('force_lower_freq') for kw in tiles):
            # all points of all tiles in one merge (a weighted mean does not
            # depend on the order): ~30 times faster than tile by tile
            parts, weights = [], []
            for kw in tiles:
                w = kw.pop('weight', 1.0)
                a = SampleArray.create(**kw)
                parts.append(a.data)
                weights.append(np.broadcast_to(np.asarray(w, dtype=np.float64), (a.data.size,)))
            allp = SampleArray()
            allp.data = np.concatenate(parts)
            sd.insert_sorted(allp, np.concatenate(weights))
        else:
            tmp = SampleArray()
            for kw in tiles:
                w = kw.pop('weight', 1.0)
                a = SampleArray.create(**kw)
                if not kw.get('force_lower_freq') and tmp.data['frequency'].size:
                    r_ix = np.flatnonzero(np.greater_equal(a['frequency'], [tmp.data['frequency'].max()]))
                    a = a[r_ix]
                    if np.ndim(w):
                        w = np.asarray(w)[r_ix]
                tmp.insert_sorted(a, w)
            sd = tmp
        with self.data_update_lock:
            self.sample_data = sd
        self.set_data_updated()
    def _add_sample_set(self, **kwargs):
        weight = kwargs.pop('weight', 1.0)
        force_lower_freq = kwargs.get('force_lower_freq')
        a = SampleArray.create(**kwargs)

        sdata = self.sample_data

        if not force_lower_freq and sdata['frequency'].size:
            r_ix = np.flatnonzero(np.greater_equal(a['frequency'], [sdata['frequency'].max()]))
            a = a[r_ix]
            if np.ndim(weight):
                weight = np.asarray(weight)[r_ix]      # V20: one weight per point
        self.sample_data.insert_sorted(a, weight)
    def set_data_updated(self):
        with self.data_update_lock:
            self.data_updated.set()
    def save_to_dbstore(self, store=None):
        """The number (eid) of the scan in the history (V22.9, audit P3-20: was None)."""
        return (store or _db_store()).add_scan(self)
    def update_dbstore(self, *attrs):
        if self.eid is None:
            return
        if not len(attrs):
            attrs = self._serialize_attrs
        d = {attr:getattr(self, attr) for attr in attrs}
        _db_store().update_scan(self.eid, **d)
    @classmethod
    def from_dbstore(cls, dbdata=None, eid=None):
        """A scan of the history, by its record or its number (eid).
        V22.9 (external audit AUD-03 / P3-09): a record as get_all_scans
        gives it (a plain dict) opened the first scan of the history, and
        the eid was lost (the record's own "eid": None won)."""
        if dbdata is None:
            if eid is None:
                raise ValueError('from_dbstore: give the record or the number (eid) of the scan')
            dbdata = _db_store().get_scan(eid)
            if dbdata is None:
                raise ValueError('scan %s is not in the history' % (eid,))
        else:
            from wwb_scanner.utils.dbstore import doc_id
            if eid is None:
                eid = doc_id(dbdata)
            if eid is None:
                eid = dbdata.get('eid')
            if dbdata.get('sample_data') is None and dbdata.get('sample_data_file'):
                if eid is None:
                    raise ValueError('from_dbstore: the record has no number (eid)')
                dbdata = _db_store().get_scan(eid)    # V14: points in scans/*.npz
                if dbdata is None:
                    raise ValueError('scan %s is not in the history' % (eid,))
        dbdata = dict(dbdata)
        dbdata['eid'] = eid
        dbdata.pop('sample_data_file', None)
        dbdata.pop('center_frequencies', None)       # written before V22.0
        dbdata.pop('samples', None)
        from wwb_scanner.scan_objects.passes import ScanPasses
        if not isinstance(dbdata.get('passes'), ScanPasses):
            dbdata.pop('passes', None)                 # the index stores only their number
        return cls.from_json(dbdata, eid=eid)
    def _serialize(self):
        d = {attr: getattr(self, attr) for attr in self._serialize_attrs}
        d['sample_data'] = self.sample_data
        return d
