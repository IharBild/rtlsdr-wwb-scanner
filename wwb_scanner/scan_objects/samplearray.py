import numpy as np

from wwb_scanner.core import JSONMixin
from wwb_scanner.utils import dbmath, numpyjson

class SampleArray(JSONMixin):
    """Points of a scan, sorted by frequency (MHz).

    V22.0: three fields per point (24 bytes). The field ``iq`` of the
    original project (16 bytes, always zero: a scan stores power, not IQ)
    is gone; arrays saved by earlier versions are converted when read.
    ``magnitude`` is the linear power, ``dbFS`` the same in dB."""
    dtype = np.dtype([
        ('frequency', np.float64),
        ('magnitude', np.float64),
        ('dbFS', np.float64)
    ])
    def __init__(self, data=None, keep_sorted=True):
        self.keep_sorted = keep_sorted
        if data is None:
            data = np.empty([0], dtype=self.dtype)
        self.data = self.convert(data)
        if keep_sorted:
            self.data = np.sort(self.data, order='frequency')
    @classmethod
    def convert(cls, data):
        """``data`` with this dtype (V22.0: arrays of earlier versions have
        an extra ``iq`` field)."""
        data = np.asarray(data)
        if data.dtype == cls.dtype:
            return data
        names = data.dtype.names or ()
        if 'frequency' not in names:
            raise ValueError('not scan points: fields %r' % (names,))
        data = data.reshape(-1)
        out = np.zeros(data.size, dtype=cls.dtype)
        out['frequency'] = data['frequency']
        if 'magnitude' in names:
            out['magnitude'] = data['magnitude']
        if 'dbFS' in names:
            out['dbFS'] = data['dbFS']
            if 'magnitude' not in names:
                out['magnitude'] = dbmath.from_dB(out['dbFS'])
        elif 'magnitude' in names:
            out['dbFS'] = dbmath.to_dB(out['magnitude'])
        return out
    @classmethod
    def create(cls, keep_sorted=True, **kwargs):
        data = kwargs.get('data')
        obj = cls(data, keep_sorted=keep_sorted)
        if not obj.data.size:
            obj.set_fields(**kwargs)
        return obj
    def set_fields(self, **kwargs):
        f = kwargs.get('frequency')
        if f is None:
            raise Exception('frequency array must be provided')
        if not isinstance(f, np.ndarray):
            f = np.array([f])
        data = np.zeros(f.size, dtype=self.dtype)
        data['frequency'] = f
        for key, val in kwargs.items():
            if key not in self.dtype.fields:
                continue
            if key == 'frequency':
                continue
            if not isinstance(val, np.ndarray):
                val = np.array([val])
            data[key] = val

        mag = kwargs.get('magnitude')
        dbFS = kwargs.get('dbFS')

        if dbFS is not None and mag is None:
            mag = data['magnitude'] = dbmath.from_dB(dbFS)
        if mag is not None and dbFS is None:
            data['dbFS'] = dbmath.to_dB(mag)

        self.append(data)
    def __getattr__(self, attr):
        if attr in self.dtype.fields.keys():
            return self.data[attr]
        raise AttributeError
    def __setattr__(self, attr, val):
        if attr in self.dtype.fields.keys():
            self.data[attr] = val
        super(SampleArray, self).__setattr__(attr, val)
    def __getitem__(self, key):
        return self.data[key]
    def __setitem__(self, key, value):
        self.data[key] = value
    def __len__(self):
        return len(self.data)
    def __iter__(self):
        return iter(self.data)
    @property
    def size(self):
        return self.data.size
    @property
    def shape(self):
        return self.data.shape
    def _check_obj_type(self, other):
        if isinstance(other, SampleArray):
            return other.data
        if isinstance(other, np.ndarray) and other.dtype.names and 'frequency' in other.dtype.names:
            return self.convert(other)
        raise Exception('Cannot extend this object type: {}'.format(other))
    def append(self, other):
        if self.keep_sorted:
            self.insert_sorted(other)
        else:
            # V22.13 (external audit AUD-21): the weights go along
            counts = self._get_counts()
            data = self._check_obj_type(other)
            other_counts = other._get_counts() if isinstance(other, SampleArray) else \
                np.ones(np.size(data), dtype=np.float64)
            self.data = np.append(self.data, data)
            self._counts = np.concatenate([counts, other_counts])
    @staticmethod
    def _freq_keys(freqs):
        """Frequencies (MHz) as integer Hz, so that values which differ only
        by floating point noise are treated as equal."""
        return np.rint(np.asarray(freqs, dtype=np.float64) * 1e6).astype(np.int64)
    def _get_counts(self):
        """How many measurements were merged into every point (default 1)."""
        counts = self.__dict__.get('_counts')
        if counts is None or counts.size != self.data.size:
            counts = np.ones(self.data.size, dtype=np.float64)
        return counts
    def insert_sorted(self, other, weight=1.0):
        """Add points, keeping the array sorted by frequency.

        Points with the same frequency (compared in whole Hz) are merged into
        one. All measurements get the same weight and are averaged as
        linear power (``magnitude``), not as dB; ``dbFS`` is recalculated
        from the merged magnitude. ``weight`` < 1 (V17): a tile measured
        again with lower gain counts only where no normal tile has the point.
        """
        data = self._check_obj_type(other)
        if not data.size:
            return
        # Scans add tiles in frequency order: only the end of the array can
        # overlap the new tile. Merge that part and keep the rest as it is
        # (copying the whole array for every tile made merging 16 times
        # slower at the end of a full-range scan).
        if self.data.size > 4 * data.size:
            new_keys = self._freq_keys(data['frequency'])
            old_keys = self._freq_keys(self.data['frequency'])
            split = int(np.searchsorted(old_keys, new_keys.min(), side='left'))
            if split > 0:
                head, head_counts = self.data[:split], self._get_counts()[:split]
                tail = SampleArray.__new__(SampleArray)
                tail.__dict__.update(self.__dict__)
                tail.data = self.data[split:]
                tail._counts = self._get_counts()[split:]
                tail.insert_sorted(data, weight)
                self.data = np.concatenate([head, tail.data])
                self._counts = np.concatenate([head_counts, tail._get_counts()])
                return
        everything = np.concatenate([self.data, data])
        # V20: ``weight`` may also be one value per new point
        weights = np.concatenate([self._get_counts(),
                                  np.broadcast_to(np.asarray(weight, dtype=np.float64), (data.size,))])
        keys = self._freq_keys(everything['frequency'])
        uniq, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
        if uniq.size == everything.size:
            order = np.argsort(keys, kind='stable')
            self.data = everything[order]
            self._counts = weights[order]
            return
        n = uniq.size
        wsum = np.bincount(inverse, weights=weights, minlength=n)
        merged = np.bincount(inverse, minlength=n) > 1
        out = everything[first]
        mag = np.bincount(inverse, weights=weights * everything['magnitude'], minlength=n) / wsum
        out['magnitude'][merged] = mag[merged]
        out['dbFS'][merged] = dbmath.to_dB(mag[merged])
        self.data = out
        self._counts = wsum
    def crop(self, start_freq, end_freq):
        """Keep only points from ``start_freq`` to ``end_freq`` (MHz, inclusive)."""
        keys = self._freq_keys(self.frequency)
        lo = int(round(start_freq * 1e6))
        hi = int(round(end_freq * 1e6))
        ix = np.flatnonzero((keys >= lo) & (keys <= hi))
        counts = self._get_counts()
        self.data = self.data[ix]
        self._counts = counts[ix]
    def keep_ranges(self, ranges):
        """V18: keep only points inside any of ``ranges`` [(lo, hi)] MHz."""
        keys = self._freq_keys(self.frequency)
        keep = np.zeros(keys.size, dtype=bool)
        for lo, hi in ranges:
            keep |= (keys >= int(round(lo * 1e6))) & (keys <= int(round(hi * 1e6)))
        ix = np.flatnonzero(keep)
        counts = self._get_counts()
        self.data = self.data[ix]
        self._counts = counts[ix]
    def counts_if_weighted(self):
        """V22.13 (external audit AUD-21): the weights of the points (1.0;
        0.01 for a point measured again; 1e-3 for a product of the dongle)
        when any differs from 1, else None - for saving with the points."""
        counts = self.__dict__.get('_counts')
        if counts is None or counts.size != self.data.size or np.all(counts == 1.):
            return None
        return np.asarray(counts, dtype=np.float64)
    def set_counts(self, counts):
        """Weights read back with the points (ignored when the size differs)."""
        counts = np.asarray(counts, dtype=np.float64)
        if counts.ndim == 1 and counts.size == self.data.size and np.all(np.isfinite(counts)) \
                and np.all(counts > 0):
            self._counts = counts.copy()
            return True
        return False
    def _serialize(self):
        d = {'data':self.data, 'keep_sorted':self.keep_sorted}
        counts = self.counts_if_weighted()
        if counts is not None:
            d['counts'] = counts                    # V22.13 (AUD-21)
        return d
    def __repr__(self):
        return '<{self.__class__.__name__}: {self}>'.format(self=self)
    def __str__(self):
        return str(self.data)

@numpyjson.register
class JSONEncoder(object):
    def encode(self, o):
        if isinstance(o, SampleArray):
            d = o._serialize()
            d['__class__'] = o.__class__.__name__
            return d
        return None
    def decode(self, d):
        if d.get('__class__') == 'SampleArray':
            a = SampleArray(d['data'], d['keep_sorted'])
            if d.get('counts') is not None:
                a.set_counts(d['counts'])           # V22.13 (AUD-21)
            return a
        return d
