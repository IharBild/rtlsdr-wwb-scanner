"""Dongle simulator for sandbox tests (plan: tools I1-I4).

FakeRtlSdr has the part of the pyrtlsdr RtlSdr interface the scanner uses.
The "air" it receives is described by an Air object (absolute frequencies,
so a tone shows up in every tile that covers it, at the right offset), or
replayed from real IQ recordings of the user's RTL-SDR V4.

Faults can be injected per call (exception, hang, close, PLL unlock).
"""
import glob
import os
import threading
import time

import numpy as np

R828D_GAINS = [0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254,
               280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439, 445, 480, 496]

_HERE = os.path.dirname(os.path.abspath(__file__))
IQ_DIR = os.environ.get('WWBSCAN_IQ_DIR') or (
    os.path.join(_HERE, 'iq') if os.path.isdir(os.path.join(_HERE, 'iq')) else '/tmp/iqchk')


class Air(object):
    """What the antenna sees. Levels are complex amplitudes relative to full
    scale of the 8-bit ADC (1.0 = clipping)."""
    def __init__(self, noise=0.01, seed=0):
        self.noise = noise
        self.tones = []          # (freq_hz, amp)
        self.bands = []          # (lo_hz, hi_hz, amp)  noise-like band (TV)
        self.dc = 0.0            # DC offset of the dongle (at fc)
        self.seed = seed
    def tone(self, f_hz, amp):
        self.tones.append((float(f_hz), float(amp)))
        return self
    def band(self, lo_hz, hi_hz, amp):
        self.bands.append((float(lo_hz), float(hi_hz), float(amp)))
        return self

    def iq(self, fc, fs, n, seed):
        rng = np.random.RandomState(seed % (2 ** 31))
        x = (rng.randn(n) + 1j * rng.randn(n)) * (self.noise / np.sqrt(2))
        t = np.arange(n) / fs
        half = fs / 2.
        for f, a in self.tones:
            off = f - fc
            if abs(off) < half * 0.98:
                x += a * np.exp(1j * (2 * np.pi * off * t + rng.uniform(0, 2 * np.pi)))
        if self.bands:
            freqs = np.fft.fftfreq(n, 1. / fs) + fc
            for lo, hi, a in self.bands:
                m = (freqs >= lo) & (freqs <= hi)
                if m.any():
                    w = np.fft.fft((rng.randn(n) + 1j * rng.randn(n)) * (a / np.sqrt(2)))
                    w[~m] = 0
                    x += np.fft.ifft(w)
        x += self.dc
        return x

    def iq_wide(self, fc, fs_w, n, seed):
        """V20: the same air sampled at the rate of the dongle's internal ADC
        (``fs_w``, several times the output rate), so tones outside the
        output band are present too. Noise keeps the same density as iq()
        at the output rate after decimation (``noise`` is per output sample
        of 2.4 MS/s-like rates: scaled by sqrt(fs_w / out rate) by caller)."""
        rng = np.random.RandomState(seed % (2 ** 31))
        x = (rng.randn(n) + 1j * rng.randn(n)) * (self.noise / np.sqrt(2))
        t = np.arange(n) / fs_w
        half = fs_w / 2.
        for f, a in self.tones:
            off = f - fc
            if abs(off) < half * 0.98:
                x += a * np.exp(1j * (2 * np.pi * off * t + rng.uniform(0, 2 * np.pi)))
        if self.bands:
            freqs = np.fft.fftfreq(n, 1. / fs_w) + fc
            for lo, hi, a in self.bands:
                m = (freqs >= lo) & (freqs <= hi)
                if m.any():
                    w = np.fft.fft((rng.randn(n) + 1j * rng.randn(n)) * (a / np.sqrt(2)))
                    w[~m] = 0
                    x += np.fft.ifft(w)
        return x


class WideAdc(object):
    """V20: model of the dongle's internal path for a strong signal just
    outside the tile (user's series 08.11.2026, mic 632.925 MHz at 49.6 dB):

    - the analog IF filter lets signals beyond +-fs/2 through, falling
      ``rolloff_db_per_mhz`` per MHz beyond the band edge;
    - the internal ADC (``ratio`` x the output rate) clips at +-1.0 - this
      clipping is NOT visible in the 8 output bits (the decimation filter
      removes the strong carrier), but its products fall into the band;
    - the decimation filter keeps +-fs/2, components from the next zones
      leak in with ``alias_db`` (the real 635.325 = 632.925 + fs alias;
      V20.2: -38 gives the user's -44 dBc in a 0 dB scan).
    """
    def __init__(self, ratio=8, rolloff_db_per_mhz=12., alias_db=-38., adc_noise_lsb=1.0, seed=7):
        self.ratio = int(ratio)
        # noise of the ADC itself (rms, in 8-bit steps of the output): it
        # sets the floor at low gain (user: -105.6...-105.9 dBFS at 0-8.7 dB)
        # and dithers the quantization of a strong carrier
        self.adc_noise = float(adc_noise_lsb) / 127.5
        self.rng = np.random.RandomState(seed)
        self.rolloff = float(rolloff_db_per_mhz)
        self.alias_db = float(alias_db)
        self.clipped = []          # (fc, share of internal samples clipped) per read

    def read(self, air, fc, fs, n, seed, gain_factor):
        r = self.ratio
        fs_w = fs * r
        nw = n * r
        saved = air.noise
        air.noise = saved * np.sqrt(r)      # same noise density in the output band
        try:
            xw = air.iq_wide(fc, fs_w, nw, seed)
        finally:
            air.noise = saved
        X = np.fft.fft(xw)
        f = np.fft.fftfreq(nw, 1. / fs_w)
        beyond = np.maximum(np.abs(f) - fs / 2., 0.) / 1e6
        X *= 10. ** (-self.rolloff * beyond / 20.)
        xw = np.fft.ifft(X) * gain_factor
        if self.adc_noise:
            # white over the internal band: the output band gets 1/ratio of it
            sd = self.adc_noise * np.sqrt(r) / np.sqrt(2)
            xw = xw + (self.rng.randn(nw) + 1j * self.rng.randn(nw)) * sd
        over = (np.abs(xw.real) >= 1.0) | (np.abs(xw.imag) >= 1.0)
        self.clipped.append((float(fc), float(np.count_nonzero(over)) / nw))
        xw = np.clip(xw.real, -1., 1.) + 1j * np.clip(xw.imag, -1., 1.)
        X = np.fft.fft(xw)
        k = np.fft.fftfreq(n, 1. / n).astype(int)           # output bins -n/2 .. n/2-1
        out = X[k % nw].copy()
        a = 10. ** (self.alias_db / 20.)
        out += a * (X[(k + n) % nw] + X[(k - n) % nw])
        return np.fft.ifft(out) / r          # same amplitude as at the internal ADC


class Lna(object):
    """V21.2: third-order nonlinearity of the tuner after the gain stage
    (LNA / mixer compression without any ADC clipping): y = x - k3 * x |x|^2.
    Two carriers f1, f2 of amplitude A give products at 2 f1 - f2 and
    2 f2 - f1 of k3 * A^3, i.e. k3 * A^2 relative to the carriers. As the
    nonlinearity is after the gain, 1 dB less gain lowers a product by 3 dB
    (2 dB relative to the carriers, after scaling back) - a real signal at
    the antenna by exactly 1 dB (0 dB after scaling back). The air is seen
    ``ratio`` x wider than the output band, so carriers outside the tile
    make products inside it."""
    def __init__(self, k3=1.0, ratio=4):
        self.k3 = float(k3)
        self.ratio = int(ratio)

    def read(self, air, fc, fs, n, seed, gain_factor):
        r = self.ratio
        nw = n * r
        saved = air.noise
        air.noise = saved * np.sqrt(r)      # same noise density in the output band
        try:
            xw = air.iq_wide(fc, fs * r, nw, seed)
        finally:
            air.noise = saved
        y = xw * gain_factor
        y = y - self.k3 * y * (np.abs(y) ** 2)
        X = np.fft.fft(y)
        k = np.fft.fftfreq(n, 1. / n).astype(int)           # output bins -n/2 .. n/2-1
        return np.fft.ifft(X[k % nw].copy()) / r


class IQReplay(object):
    """Real recordings: the tile at fc gets a slice of the recording whose
    centre is nearest (the RF content is not shifted, only the noise and
    the dongle's own artefacts are real)."""
    def __init__(self, directory=IQ_DIR):
        self.rec = {}
        for p in sorted(glob.glob(os.path.join(directory, 'iq_*.npz'))):
            d = np.load(p)
            raw = d['raw']
            iq = raw.astype(np.float64).view(np.complex128) / 127.5 - (1 + 1j)
            self.rec[int(d['fc_hz'])] = (iq, float(d['fs_hz']))
    def available(self):
        return bool(self.rec)
    def iq(self, fc, fs, n, seed):
        key = min(self.rec, key=lambda k: abs(k - fc))
        iq, rfs = self.rec[key]
        start = (seed * 7919) % max(1, iq.size - n)
        return iq[start:start + n].copy()


class Fault(object):
    """When ``when(call_no, freq)`` is true, do ``action`` instead of the call.
    action: 'raise', 'hang', 'close', 'unlock', 'slow'."""
    def __init__(self, method, action, when, hang_s=5.0, slow_s=0.05):
        self.method = method
        self.action = action
        self.when = when
        self.hang_s = hang_s
        self.slow_s = slow_s
        self.hits = 0


class FakeRtlSdr(object):
    """Stand-in for rtlsdr.RtlSdr."""
    instances = []

    def __init__(self, serial_number=None, air=None, faults=(), quantize=True,
                 tune_delay=0.0, read_delay=0.0, fail_open=False, gain_affects=False,
                 wide_adc=None, gain_error=None, lna=None):
        if fail_open:
            raise IOError('No devices found')
        self.air = air if air is not None else Air()
        self.faults = list(faults)
        self.quantize = quantize
        self.gain_affects = gain_affects      # V17: the gain scales what is received
        self.wide_adc = wide_adc              # V20: WideAdc model (None = old model)
        self.gain_error = gain_error          # V20.5: f(fc_hz, gain) -> dB the real gain differs
        self.lna = lna                        # V21.2: Lna model (tuner intermodulation)
        self.gains_set = []
        self.read_gains = []                  # V20: gain of every read_samples call
        self.tune_delay = tune_delay
        self.read_delay = read_delay
        self.device_opened = True
        self.dev_p = 0x1234
        self._fs = 2.048e6
        self._fc = 100e6
        self._gain = 30.0
        self.freq_correction = 0
        self.calls = {'set_center_freq': 0, 'read_samples': 0}
        self.tuned = []
        self.reads = []
        self.locked = True
        self.unlocks = 0
        self.hung_threads = 0
        self.closed_calls = 0
        FakeRtlSdr.instances.append(self)

    # --- properties used by the scanner
    @property
    def sample_rate(self):
        return self._fs
    @sample_rate.setter
    def sample_rate(self, v):
        self._check_open()
        self._fs = float(v)
    @property
    def gain(self):
        return self._gain
    @gain.setter
    def gain(self, v):
        self._check_open()
        g = np.array(R828D_GAINS) / 10.
        self._gain = float(g[np.abs(g - float(v)).argmin()])
        self.gains_set.append(self._gain)
    @property
    def center_freq(self):
        return self._fc

    def get_gains(self):
        return list(R828D_GAINS)

    def _check_open(self):
        if not self.device_opened:
            self.closed_calls += 1
            raise IOError('device closed (use after close)')

    def _fault(self, method, freq):
        n = self.calls[method]
        for f in self.faults:
            if f.method == method and f.when(n, freq):
                f.hits += 1
                if f.action == 'raise':
                    raise IOError('LIBUSB_ERROR_IO (injected)')
                if f.action == 'close':
                    self.close()
                    raise IOError('LIBUSB_ERROR_IO (injected, device closed)')
                if f.action == 'hang':
                    self.hung_threads += 1
                    time.sleep(f.hang_s)
                if f.action == 'unlock':
                    self.locked = False
                    self.unlocks += 1
                    return 'unlock'
                if f.action == 'slow':
                    time.sleep(f.slow_s)
        return None

    def set_center_freq(self, freq):
        self._check_open()
        self.calls['set_center_freq'] += 1
        self.locked = True
        self._fault('set_center_freq', freq)
        if self.tune_delay:
            time.sleep(self.tune_delay)
        self._fc = float(freq)
        self.tuned.append(float(freq))

    def read_samples(self, num_samples):
        self._check_open()
        self.calls['read_samples'] += 1
        self._fault('read_samples', self._fc)
        if self.read_delay:
            time.sleep(self.read_delay)
        self.reads.append(int(num_samples))
        self.read_gains.append(self._gain)
        seed = int(self._fc // 1000) + self.calls['read_samples']
        # levels of the Air are for gain 29.7 dB
        g_real = self._gain + (self.gain_error(self._fc, self._gain) if self.gain_error else 0.)
        gf = 10. ** ((g_real - 29.7) / 20.) if self.gain_affects else 1.
        if self.lna is not None:
            gl = gf if self.gain_affects else 1.
            x = self.lna.read(self.air, self._fc, self._fs, int(num_samples), seed, gl)
            x = x + self.air.dc
        elif self.wide_adc is not None:
            x = self.wide_adc.read(self.air, self._fc, self._fs, int(num_samples), seed, gf)
            x = x + self.air.dc
        else:
            x = self.air.iq(self._fc, self._fs, int(num_samples), seed)
            if self.gain_affects:
                x = x * gf
        if self.quantize:
            i = np.clip(np.round((x.real + 1) * 127.5), 0, 255)
            q = np.clip(np.round((x.imag + 1) * 127.5), 0, 255)
            x = (i + 1j * q) / 127.5 - (1 + 1j)
        return x

    def close(self):
        self.device_opened = False


def vhf_gain_error(fc_hz, gain):
    """V20.5: like the user's V4 (09.11.2026): below 300 MHz the gain steps
    above 29.7 dB give only half of their nominal gain."""
    if fc_hz < 300e6 and gain > 29.7:
        return -0.5 * (gain - 29.7)
    return 0.


class FakeLockLib(object):
    """Stands in for the scanner-v4 DLL extras (scan_dll._lib())."""
    def __init__(self):
        import ctypes
        self._ct = ctypes
        def pll(dev, late_p, unl_p):
            sdr = FakeRtlSdr.instances[-1] if FakeRtlSdr.instances else None
            if sdr is None:
                return -1
            late_p._obj.value = 0
            unl_p._obj.value = sdr.unlocks
            return 1 if sdr.locked else 0
        self.rtlsdr_get_tuner_pll_lock = pll
        self.rtlsdr_get_build_info = lambda: b'fake scanner-v4'


def make_config(start, end, fs=2.4e6, window=96, sweeps=40, per_sweep=2048,
                overlap=0.65, window_type='hann', gain=33.8, auto_gain=False, clean=None,
                ranges=None, gain_calibration=False, verify_peaks=False):
    conf = {
        'scan_range': [float(start), float(end)],
        'device': {'gain': gain},
        'sampling': {
            'sample_rate': fs, 'sweep_overlap_ratio': overlap,
            'sweeps_per_scan': sweeps, 'samples_per_sweep': per_sweep,
            'window_size': window, 'window_type': window_type,
            'auto_gain': auto_gain,
            'verify_peaks': verify_peaks,     # V21.2
        },
    }
    if clean:
        conf['processing'] = dict(clean)
    if ranges:
        conf['scan_ranges'] = [list(r) for r in ranges]     # V18: several ranges
    if gain_calibration:
        conf['gain_calibration'] = True                     # V20.5
    return conf


def run_scan(monkeypatch, tmpdir, start, end, sdr_kwargs=None, lock_lib=False,
             timeout=None, **cfg):
    """Run a real Scanner.run_scan() against FakeRtlSdr. Returns (scanner, sdr, seconds)."""
    from wwb_scanner.scanner import sdrwrapper, main, sample_processing
    from wwb_scanner import scan_dll
    from wwb_scanner.utils import dbstore
    sdr_kwargs = dict(sdr_kwargs or {})
    made = []
    def factory(serial_number=None):
        s = FakeRtlSdr(serial_number=serial_number, **sdr_kwargs)
        made.append(s)
        return s
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', factory)
    monkeypatch.setitem(scan_dll._cache, 'lib', FakeLockLib() if lock_lib else None)
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    if timeout is not None:
        monkeypatch.setattr(sample_processing, 'TUNE_TIMEOUT', timeout)
        monkeypatch.setattr(sample_processing, 'READ_TIMEOUT', timeout)
    scanner = main.Scanner(config=make_config(start, end, **cfg))
    t0 = time.time()
    th = threading.Thread(target=scanner.run_scan, daemon=True)
    th.start()
    th.join(120)
    assert not th.is_alive(), 'scan did not finish in 120 s'
    sdr = made[-1] if made else None
    return scanner, sdr, time.time() - t0


def spectrum_arrays(scanner):
    sd = scanner.spectrum.sample_data
    return np.asarray(sd.frequency, float), np.asarray(sd['dbFS'], float)


def copy_tests(dst):
    """A copy of this tests folder (for a kit laid out in a temporary folder).
    os.symlink fails on Windows without the symbolic link privilege."""
    import shutil
    here = os.path.dirname(os.path.abspath(__file__))
    shutil.copytree(here, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'reference', 'data'))
    return dst
