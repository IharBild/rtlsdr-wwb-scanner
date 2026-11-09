"""V20.5: real gain steps by frequency (H of the 09.11.2026 analysis).

On the user's V4 the step 49.6 -> 33.8 dB gave ~8 dB at 150-300 MHz and
~18 dB at 700-1000 MHz (15.8 nominal). Auto gain scaled re-measured tiles by
the nominal step, so they were off against their neighbours (DAB 211 MHz
11 dB). The simulator gets the same kind of error (sim.vhf_gain_error).

The tests fail on V20.4 (checked); the safety ones (marked) check what must
stay as it was and use the new names.
"""
import json
import os

import numpy as np
import pytest

import sim
from wwb_scanner.scanner import gain_cal, sample_processing as sp

GAINS = [g / 10. for g in sim.R828D_GAINS]


@pytest.fixture
def caldir(monkeypatch, tmpdir):
    from wwb_scanner.scanner import cleanup
    d = str(tmpdir.join('cal'))
    monkeypatch.setattr(cleanup, 'calibration_dir', lambda: d)
    gain_cal._cache.clear()
    yield d
    gain_cal._cache.clear()


def _exact_table(fs=2.4e6):
    """The table sim.vhf_gain_error would give if every step were measured."""
    groups = []
    for f in (150., 600.):
        real = [g + sim.vhf_gain_error(f * 1e6, g) for g in GAINS]
        groups.append({'f_mhz': f, 'anchors_mhz': [f], 'steps': list(np.diff(real))})
    return {'version': gain_cal.VERSION, 'date': 'test', 'gains': GAINS, 'groups': groups,
            'measured': 2 * (len(GAINS) - 1), 'total': 2 * (len(GAINS) - 1), 'sample_rate': fs}


def test_without_a_table_the_steps_are_nominal(caldir):
    # safety: nothing changes until the user calibrates
    assert gain_cal.real_drop(49.6, 33.8, 200e6, sample_rate=2.4e6) == pytest.approx(15.8)
    assert 'not calibrated' in gain_cal.status_text(2.4e6)


def test_table_gives_the_real_step_by_frequency(caldir):
    gain_cal.save_table(_exact_table(), 2.4e6)
    assert gain_cal.real_drop(49.6, 29.7, 150e6, sample_rate=2.4e6) == pytest.approx(9.95, abs=0.01)
    assert gain_cal.real_drop(49.6, 29.7, 600e6, sample_rate=2.4e6) == pytest.approx(19.9, abs=0.01)
    assert gain_cal.real_drop(29.7, 0.0, 150e6, sample_rate=2.4e6) == pytest.approx(29.7, abs=0.01)
    # between the groups: linear; outside them: the nearest group
    assert gain_cal.real_drop(49.6, 29.7, 375e6, sample_rate=2.4e6) == pytest.approx(14.925, abs=0.01)
    assert gain_cal.real_drop(49.6, 29.7, 60e6, sample_rate=2.4e6) == pytest.approx(9.95, abs=0.01)
    # another sample rate has no table
    assert gain_cal.real_drop(49.6, 29.7, 150e6, sample_rate=2.048e6) == pytest.approx(19.9)


def test_unmeasured_and_impossible_steps_stay_nominal():
    rows = []
    floor = 1e-8
    for g in GAINS:
        p = np.full(400, floor)
        lvl = 1e-9 * 10 ** (g / 10.)                  # +1 dB per dB of gain
        p[100:140] = max(lvl, floor)
        rows.append((g, p, 0.0 if g < 45 else 0.1))   # clipped at the top gains
    t = gain_cal.build_table([600e6], [rows], GAINS)
    steps = t['groups'][0]['steps']
    for (ga, gb), s in zip(zip(GAINS, GAINS[1:]), steps):
        if s is not None:
            assert s == pytest.approx(gb - ga, abs=0.01)
    assert steps[-1] is None                          # clipped: not measured
    assert steps[0] is None                           # signal under the noise at 0 dB
    gain_cal._prepare(t)
    assert gain_cal.real_drop(49.6, 0., 600e6, table=t) == pytest.approx(49.6, abs=0.01)


def test_gain_setting_without_effect_gives_no_table():
    # safety: a dongle whose level does not follow the gain (AGC on?) must
    # not get a table that would spoil every re-measured tile
    rows = []
    for g in GAINS:
        p = np.full(400, 1e-8)
        p[100:140] = 1e-5
        rows.append((g, p, 0.0))
    t = gain_cal.build_table([600e6], [rows], GAINS)
    assert t['groups'] == []


def _air():
    a = sim.Air(noise=0.002)
    # FM band, DAB blocks, a TV multiplex, mobile
    # and weaker ones next to them (the strongest clip at the top gains)
    for lo, hi, amp in ((88e6, 108e6, 0.3), (202e6, 206e6, 0.15), (600e6, 607.6e6, 0.1),
                        (950e6, 955e6, 0.08), (60e6, 61.5e6, 0.04), (150e6, 151.5e6, 0.04),
                        (230e6, 231.5e6, 0.04), (520e6, 527.6e6, 0.03), (800e6, 805e6, 0.03)):
        a.band(lo, hi, amp)
    return a


def test_calibration_measures_the_steps_of_the_dongle(monkeypatch, tmpdir, caldir):
    sk = {'air': _air(), 'gain_affects': True, 'gain_error': sim.vhf_gain_error}
    sc, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 30, 1766, gain=49.6, sdr_kwargs=sk,
                               ranges=gain_cal.survey_ranges(), gain_calibration=True)
    ok, text = sc.gain_cal_result
    assert ok, text
    t = gain_cal.load_table(2.4e6)
    assert t is not None and len(t['groups']) >= 3
    for f, want in ((60.7e6, 9.95), (150.7e6, 9.95), (230.7e6, 9.95), (603e6, 19.9), (950e6, 19.9)):
        got = gain_cal.real_drop(49.6, 29.7, f, table=t)
        assert got == pytest.approx(want, abs=1.0), (f / 1e6, got, want)
    print('calibration took %.1f s: %s' % (dt, text))
    assert dt < 120


def _tone_level(sc, f_mhz):
    f, y = sim.spectrum_arrays(sc)
    return float(y[np.abs(f - f_mhz) < 0.03].max())


@pytest.mark.parametrize('calibrated', [False, True])
def test_remeasured_tile_has_the_right_level(monkeypatch, tmpdir, caldir, calibrated):
    if calibrated:
        gain_cal.save_table(_exact_table(), 2.4e6)
    air = sim.Air(noise=0.002).tone(200.1e6, 0.5)      # clips at 49.6, not at 37.2
    base = {'air': air, 'gain_affects': True, 'gain_error': sim.vhf_gain_error}
    ref, _, _ = sim.run_scan(monkeypatch, tmpdir.mkdir('ref'), 199, 201, gain=49.6,
                             sdr_kwargs=dict(base, quantize=False))     # nothing clips
    sc, _, _ = sim.run_scan(monkeypatch, tmpdir.mkdir('auto'), 199, 201, gain=49.6, auto_gain=True,
                            sdr_kwargs=dict(base))
    assert sc.sample_collection.overload
    err = _tone_level(sc, 200.1) - _tone_level(ref, 200.1)
    print('calibrated %s: level error %+.1f dB' % (calibrated, err))
    if calibrated:
        assert abs(err) < 1.5
    else:
        assert err > 4.            # the old way: off by the nominal - real step
