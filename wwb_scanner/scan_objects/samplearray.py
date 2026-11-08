import numpy as np
from scipy.interpolate import CubicSpline
import jsonfactory

from wwb_scanner.core import JSONMixin
from wwb_scanner.utils import dbmath

class SampleArray(JSONMixin):
    dtype = np.dtype([
        ('frequency', np.float64),
        ('iq', np.complex128),
        ('magnitude', np.float64),
        ('dbFS', np.float64)
    ])
    def __init__(self, data=None, keep_sorted=True):
        self.keep_sorted = keep_sorted
        if data is None:
            data = np.empty([0], dtype=self.dtype)
        self.data = data
        if keep_sorted:
            self.data = np.sort(self.data, order='frequency')
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

        if data is None:
            return

        iq = kwargs.get('iq')
        mag = kwargs.get('magnitude')
        dbFS = kwargs.get('dbFS')

        if iq is not None and mag is None:
            mag = data['magnitude'] = np.abs(iq)
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
            data = other.data
        else:
            if isinstance(other, np.ndarray) and other.dtype == self.dtype:
                data = other
            else:
                raise Exception('Cannot extend this object type: {}'.format(other))
        return data
    def append(self, other):
        if self.keep_sorted:
            self.insert_sorted(other)
        else:
            data = self._check_obj_type(other)
            self.data = np.append(self.data, data)
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
        weights = np.concatenate([self._get_counts(), np.full(data.size, float(weight))])
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
        iq_re = np.bincount(inverse, weights=weights * everything['iq'].real, minlength=n) / wsum
        iq_im = np.bincount(inverse, weights=weights * everything['iq'].imag, minlength=n) / wsum
        out['magnitude'][merged] = mag[merged]
        out['iq'][merged] = (iq_re + 1j * iq_im)[merged]
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
    def smooth(self, window_size):
        window_size = int(window_size)
        if window_size < 3 or self.data.size < 3 or window_size > self.data.size:
            return
        x = self.magnitude
        w = np.hanning(window_size)

        s = np.r_[x[window_size-1:0:-1], x, x[-2:-window_size-1:-1]]

        y = np.convolve(w/w.sum(), s, mode='valid')
        m = y[(window_size//2-1):-(window_size//2)]

        if m.size != x.size:
            raise Exception('Smooth result size {} != data size {}'.format(m.size, x.size))

        self.data['magnitude'] = m
        self.data['dbFS'] = dbmath.to_dB(m)
    def interpolate(self, spacing=0.025):
        fmin = np.ceil(self.frequency.min())
        fmax = np.floor(self.frequency.max())

        x = self.frequency
        y = self.magnitude
        cs = CubicSpline(x, y)
        xs = np.arange(fmin, fmax+spacing, spacing)
        s = str(spacing)
        n_dec = len(s.split('.')[1]) if '.' in s else 0
        xs = np.around(xs, n_dec)

        ys = cs(xs)
        data = np.zeros(xs.size, dtype=self.dtype)
        data['frequency'] = xs
        data['magnitude'] = ys
        data['dbFS'] = dbmath.to_dB(ys)
        self.data = data

    def _serialize(self):
        return {'data':self.data, 'keep_sorted':self.keep_sorted}
    def __repr__(self):
        return '<{self.__class__.__name__}: {self}>'.format(self=self)
    def __str__(self):
        return str(self.data)

@jsonfactory.register
class JSONEncoder(object):
    def encode(self, o):
        if isinstance(o, SampleArray):
            d = o._serialize()
            d['__class__'] = o.__class__.__name__
            return d
        return None
    def decode(self, d):
        if d.get('__class__') == 'SampleArray':
            return SampleArray(d['data'], d['keep_sorted'])
        return d
