"""V22.2: several passes of one scan (audit 16.11.2026, item 7).

A scan was one array of points. Max-hold over N passes (the "Precise"
mode), an average, a waterfall in the live mode and the project file all
need the passes themselves. ``ScanPasses`` keeps them on one frequency grid:

- ``freqs``  frequencies of the grid, MHz (sorted; keys compared in whole Hz
  like SampleArray, so float noise does not split a point);
- ``levels`` dB, one row per pass (float32), NaN where a pass has no point
  (a tile without data, a range of another pass);
- ``times``  start time of every pass (s since the epoch).

Views are computed from the rows and never change them: ``max_hold``,
``average`` (mean of the linear power), ``last`` and ``count`` (passes with
a value at every point). Spectrum.passes holds this object; the points of
the spectrum (sample_data) are the chosen view.
"""
import numpy as np

VIEWS = ('max_hold', 'average', 'last')


def _keys(freqs):
    return np.rint(np.asarray(freqs, dtype=np.float64) * 1e6).astype(np.int64)


class ScanPasses(object):
    def __init__(self, freqs=None, levels=None, times=None):
        if freqs is None:
            self.freqs = np.empty(0, dtype=np.float64)
            self.levels = np.empty((0, 0), dtype=np.float32)
            self.times = []
        else:
            self.freqs = np.asarray(freqs, dtype=np.float64).ravel()
            levels = np.asarray(levels, dtype=np.float32)
            if self.freqs.size == 0:
                # V22.13 (external audit AUD-22): an empty set of passes comes
                # back from a file as (0, 0) levels (reshape(-1, 0) failed)
                n = levels.shape[0] if levels.ndim == 2 else 0
                self.levels = np.empty((n, 0), dtype=np.float32)
            else:
                self.levels = levels.reshape(-1, self.freqs.size)
            self.times = list(times) if times is not None else [None] * self.levels.shape[0]
            if len(self.times) != self.levels.shape[0]:
                raise ValueError('one time per pass')
    def __len__(self):
        return self.levels.shape[0]
    @property
    def size(self):
        return self.freqs.size
    def add(self, freqs, levels_db, time=None):
        """Add one pass (its points: frequencies MHz and levels dB)."""
        f = np.asarray(freqs, dtype=np.float64)
        y = np.asarray(levels_db, dtype=np.float64)
        if f.shape != y.shape:
            raise ValueError('frequencies and levels differ in size')
        new_k, old_k = _keys(f), _keys(self.freqs)
        if not np.array_equal(new_k, old_k):
            allk = np.union1d(old_k, new_k)
            grid = np.full((len(self), allk.size), np.nan, dtype=np.float32)
            if len(self):
                grid[:, np.searchsorted(allk, old_k)] = self.levels
            f_all = np.empty(allk.size, dtype=np.float64)
            f_all[np.searchsorted(allk, old_k)] = self.freqs
            f_all[np.searchsorted(allk, new_k)] = f
            self.freqs, self.levels = f_all, grid
        row = np.full(self.freqs.size, np.nan, dtype=np.float32)
        row[np.searchsorted(_keys(self.freqs), new_k)] = y
        self.levels = np.vstack([self.levels, row[None, :]]) if len(self) else row[None, :]
        self.times.append(time)
        return len(self) - 1
    def count(self):
        return np.sum(np.isfinite(self.levels), axis=0)
    def view(self, name='max_hold'):
        """(freqs, levels dB) of a view; points no pass has are left out."""
        if name not in VIEWS:
            raise ValueError('unknown view %r (%s)' % (name, ', '.join(VIEWS)))
        if not len(self):
            return self.freqs.copy(), np.empty(0)
        have = self.count() > 0
        lv = self.levels[:, have].astype(np.float64)
        if name == 'max_hold':
            y = np.nanmax(lv, axis=0)
        elif name == 'average':
            p = np.power(10., lv / 10.)
            y = 10. * np.log10(np.nanmean(p, axis=0))
        else:
            y = lv[-1].copy()
            miss = ~np.isfinite(y)
            if miss.any():                      # last pass without data there: the latest pass that has it
                for row in lv[::-1]:
                    take = miss & np.isfinite(row)
                    y[take] = row[take]
                    miss &= ~take
        return self.freqs[have].copy(), y
    def to_arrays(self):
        """For an npz file (the history)."""
        t = np.array([np.nan if v is None else float(v) for v in self.times], dtype=np.float64)
        return {'pass_freqs': self.freqs, 'pass_levels': self.levels, 'pass_times': t}
    @classmethod
    def from_arrays(cls, z):
        """From an npz file; None when it has no passes."""
        try:
            files = z.files
        except AttributeError:
            files = list(z.keys())
        if 'pass_levels' not in files:
            return None
        # V22.13 (external audit AUD-22): no pass_times -> no times (was KeyError)
        raw_t = np.asarray(z['pass_times'], dtype=np.float64) if 'pass_times' in files else None
        freqs = z['pass_freqs'] if 'pass_freqs' in files else np.empty(0)
        levels = np.asarray(z['pass_levels'])
        if raw_t is None:
            n = levels.shape[0] if levels.ndim == 2 else 0
            t = [None] * n
        else:
            t = [None if not np.isfinite(v) else float(v) for v in raw_t]
        return cls(freqs, levels, t)
