"""V20.7.3: a dongle overloaded everywhere does not overwrite the calibrations.

10.11.2026 20:44: every tile 30-1766 MHz clipped even at 0 dB gain (20 % of
samples); the gain step calibration said "no strong signals" and the floor
calibration was saved with a floor of -65 dB instead of -96, replacing the
good one."""
import os

import numpy as np

from wwb_scanner.scanner import cleanup as C
from wwb_scanner.scanner import gain_cal
from test_v17 import synthetic


def _home(tmpdir, monkeypatch):
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))


META = dict(gain=49.6, sample_rate=2.4e6)


def test_floor_far_above_the_dongle_is_not_saved_and_the_old_one_stays(tmpdir, monkeypatch):
    _home(tmpdir, monkeypatch)
    f, y, _ = synthetic()
    ok, _ = C.calibrate(f, y, 31.0, 0.775, dict(META))
    assert ok
    path = C.profile_path(49.6, 2.4e6)
    good = C.load_profile(path)['floor_ref_db']
    ok, text = C.calibrate(f, y + 36., 31.0, 0.775, dict(META))         # floor -65
    assert not ok and 'previous one is kept' in text and '-96' in text
    assert abs(C.load_profile(path)['floor_ref_db'] - good) < 1e-6


def test_clipping_calibration_scan_is_not_saved(tmpdir, monkeypatch):
    _home(tmpdir, monkeypatch)
    f, y, _ = synthetic()
    ok, text = C.calibrate(f, y, 31.0, 0.775, dict(META), clipped_share=0.95)
    assert not ok and '95 % of the tiles clipped' in text
    assert not os.path.exists(C.profile_path(49.6, 2.4e6))


def test_previous_calibration_is_backed_up(tmpdir, monkeypatch):
    _home(tmpdir, monkeypatch)
    f, y, _ = synthetic()
    assert C.calibrate(f, y, 31.0, 0.775, dict(META))[0]
    assert C.calibrate(f, y + 0.5, 31.0, 0.775, dict(META))[0]
    path = C.profile_path(49.6, 2.4e6)
    prev = path[:-4] + '.prev.npz'
    assert os.path.exists(prev)
    assert C.load_profile(prev)['floor_ref_db'] < C.load_profile(path)['floor_ref_db']


def test_broken_saved_calibration_is_not_used(tmpdir, monkeypatch):
    _home(tmpdir, monkeypatch)
    f, y, _ = synthetic()
    prof = C.build_profile(f, y + 36., 31.0, 31, meta=dict(META))
    C.save_profile(prof, C.profile_path(49.6, 2.4e6))            # as V20.7.2 saved it
    p, why = C.find_profile(49.6, 2.4e6)
    assert p is None and 'broken' in why


class _Set(object):
    neighbour = False

    def __init__(self, clip):
        self.clip_ratio = clip
        self.powers = 10 ** (np.full(300, -15.) / 10.)


class _Coll(object):
    aborted = False

    def __init__(self, clip):
        self.sample_sets = {30e6 + 4e6 * i: _Set(clip) for i in range(50)}


class _Scanner(object):
    sdr = object()
    gains = [0.0, 49.6]
    sample_rate = 2.4e6

    def __init__(self, clip):
        self.sample_collection = _Coll(clip)


def test_gain_calibration_says_overloaded_not_no_signals():
    ok, text = gain_cal.calibrate(_Scanner(0.2))
    assert not ok and 'overloaded everywhere' in text and 'no strong signals' not in text


def test_scan_overloaded_everywhere_says_the_result_is_not_valid(monkeypatch, tmpdir):
    import sim
    air = sim.Air(noise=1.5)                     # clips at any gain
    scanner, _, _ = sim.run_scan(monkeypatch, tmpdir, 470, 490, auto_gain=True, gain=49.6,
                                 sdr_kwargs={'air': air, 'gain_affects': False})
    note = scanner.sample_collection.overload_note
    assert note.startswith('THE RESULT IS NOT VALID')
    assert scanner.sample_collection.overload_warn
