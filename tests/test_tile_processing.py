"""Plan step B2: the PSD of a tile must not depend on the centre frequency,
tile edges and the DC spike are dropped."""
import numpy as np
import pytest

from wwb_scanner.scanner import sample_processing as sp
from wwb_scanner.scanner.config import ScanConfig

RS = 2.4e6
WIN = 96


class FakeCollection(object):
    def __init__(self):
        self.done = []
    def on_sample_set_processed(self, s):
        self.done.append(s)


class FakeScanner(object):
    def __init__(self, window_type='boxcar'):
        self.config = ScanConfig()
        self.sampling_config = self.config.sampling
        self.sampling_config.window_type = window_type
        self.sample_rate = RS
        self.window_size = WIN
        self.sweeps_per_scan = 20
        self.samples_per_sweep = 8192


def run_tile(fc_hz, tone_hz=None, tone_amp=0.1, dc=0.0, seed=1, window_type='boxcar'):
    scanner = FakeScanner(window_type)
    coll = FakeCollection()
    ss = sp.SampleSet(scanner=scanner, collection=coll, center_frequency=fc_hz)
    n = scanner.sweeps_per_scan * scanner.samples_per_sweep
    rng = np.random.RandomState(seed)
    x = (rng.randn(n) + 1j * rng.randn(n)) * 0.01
    t = np.arange(n) / RS
    if tone_hz is not None:
        x = x + tone_amp * np.exp(2j * np.pi * (tone_hz - fc_hz) * t)
    x = x + dc
    ss.raw = x.reshape(scanner.sweeps_per_scan, scanner.samples_per_sweep)
    ss.process_samples()
    return ss.frequencies, ss.powers


def test_bins_are_inside_kept_range_and_without_center():
    f, p = run_tile(600e6)
    off = (f - 600.) * 1e6
    assert np.all(np.abs(off) <= sp.DEFAULT_TILE_KEEP_RATIO * RS + 1)
    assert np.all(np.abs(off) > sp.DEFAULT_TILE_CENTER_GAP_HZ)
    assert 0. not in np.round(off)
    assert f.size == p.size


@pytest.mark.parametrize('fc', [500e6, 600e6, 750e6, 1200e6])
def test_flat_noise_is_flat_at_any_centre_frequency(fc):
    f, p = run_tile(fc)
    d = 10 * np.log10(p)
    assert np.ptp(d) < 1.0


def test_same_level_at_every_centre_frequency():
    levels = [np.median(10 * np.log10(run_tile(fc)[1])) for fc in (500e6, 600e6, 750e6)]
    assert np.ptp(levels) < 0.1


def test_tone_seen_by_two_tiles_has_same_level():
    tone = 700.4e6
    f1, p1 = run_tile(700.0e6, tone)
    f2, p2 = run_tile(700.8e6, tone)
    i1 = np.argmin(np.abs(f1 - tone / 1e6))
    i2 = np.argmin(np.abs(f2 - tone / 1e6))
    assert f1[i1] == pytest.approx(f2[i2])
    assert 10 * np.log10(p1[i1] / p2[i2]) == pytest.approx(0., abs=0.3)


def test_dc_spike_is_not_in_output():
    f, p = run_tile(600e6, dc=0.2)
    d = 10 * np.log10(p)
    assert np.ptp(d) < 1.0


def stitch(start_mhz, end_mhz, step_mhz, tone_mhz=None, tone_amp=0.1):
    from wwb_scanner.scan_objects import Spectrum
    spectrum = Spectrum()
    fc = int(round(start_mhz * 1e6))
    step = int(round(step_mhz * 1e6))
    while fc <= int(round(end_mhz * 1e6)):
        # a real tuner does not see tones outside its 2.4 MHz band
        in_band = tone_mhz is not None and abs(tone_mhz * 1e6 - fc) < 1.1e6
        f, p = run_tile(float(fc), tone_mhz * 1e6 if in_band else None,
                        tone_amp=tone_amp, seed=fc % 1000)
        spectrum.add_sample_set(frequency=f, magnitude=p, center_frequency=float(fc),
                                force_lower_freq=True)
        fc += step
    spectrum.crop(start_mhz, end_mhz)
    return spectrum


def test_stitched_scan_has_no_holes_and_a_flat_floor():
    spectrum = stitch(600., 606., 0.8)
    f = spectrum.sample_data['frequency']
    d = spectrum.sample_data['dbFS']
    assert f.size == int(round((606. - 600.) / 0.025)) + 1
    assert np.allclose(np.diff(f), 0.025, atol=1e-6)
    assert np.all(d > -200)
    assert np.ptp(d) < 1.5


def test_stitched_scan_shows_narrow_tone_at_right_place():
    spectrum = stitch(600., 606., 0.8, tone_mhz=603.1)
    f = spectrum.sample_data['frequency']
    d = spectrum.sample_data['dbFS']
    peak = f[np.argmax(d)]
    assert abs(peak - 603.1) <= 0.025
    assert d.max() - np.median(d) > 20


class FakeSdr(object):
    """Hands out the same noise + tone a real tuner would, one blocking read at a time."""
    def __init__(self, fail_first=False):
        self.freq = None
        self.reads = []
        self.fail_first = fail_first
    def set_center_freq(self, f):
        self.freq = f
    def read_samples(self, n):
        self.reads.append(n)
        if self.fail_first:
            self.fail_first = False
            raise IOError('usb')
        rng = np.random.RandomState(3)
        return (rng.randn(n) + 1j * rng.randn(n)) * 0.01


def sync_tile(sdr):
    scanner = FakeScanner()
    scanner.sdr = sdr
    coll = FakeCollection()
    ss = sp.SampleSet(scanner=scanner, collection=coll, center_frequency=600e6)
    ss.read_samples()
    return ss, coll


def test_sync_read_gives_a_processed_tile():
    sdr = FakeSdr()
    ss, coll = sync_tile(sdr)
    assert ss.complete.is_set() and coll.done == [ss]
    assert sdr.reads[0] == sp.NUM_SAMPLES_DISCARDED
    assert sdr.reads[1] % 512 == 0 and sdr.reads[1] >= 20 * 8192
    assert np.ptp(10 * np.log10(ss.powers)) < 1.0


def test_sync_read_retries_once_after_usb_error():
    sdr = FakeSdr(fail_first=True)
    ss, coll = sync_tile(sdr)
    assert coll.done == [ss]


def test_sync_read_gives_up_without_hanging():
    class Dead(FakeSdr):
        def read_samples(self, n):
            raise IOError('gone')
    ss, coll = sync_tile(Dead())
    assert ss.complete.is_set() and coll.done == []


def test_tune_failure_does_not_raise_and_completes_tile():
    class NoLock(FakeSdr):
        def set_center_freq(self, f):
            raise IOError('Could not set center_freq')
    ss, coll = sync_tile(NoLock())
    assert ss.complete.is_set() and coll.done == []


def test_tune_retries_once():
    class Flaky(FakeSdr):
        n = 0
        def set_center_freq(self, f):
            Flaky.n += 1
            if Flaky.n == 1:
                raise IOError('pll')
    ss, coll = sync_tile(Flaky())
    assert coll.done == [ss]


def test_no_tile_centres_above_tuner_limit():
    from wwb_scanner.scanner import main as m
    built = []
    class Coll(object):
        def build_sample_set(self, f):
            built.append(f)
    class Fake(object):
        pass
    obj = Fake()
    obj.config = Fake(); obj.config.scan_range = [1700., 2000.]
    obj.sample_collection = Coll()
    obj.step_size = 0.775
    m.ScannerBase.build_sample_sets(obj)
    assert built and max(built) <= m.MAX_TUNER_FREQ_HZ
