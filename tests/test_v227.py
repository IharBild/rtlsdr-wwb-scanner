"""V22.7: own gain per band ("Suggest gain A+", scanner/gain_plan.py and
Scanner._run_banded)."""
import logging

import numpy as np
import pytest

from wwb_scanner.scanner import gain_plan as gp

import sim

NOMINAL = (lambda g_hi, g_lo, f=None: float(g_hi) - float(g_lo))


def test_split_at_band_edges():
    assert gp.split([(470., 700.)]) == [(470., 560.), (560., 700.)]
    assert gp.split([(500., 520.)]) == [(500., 520.)]
    assert gp.split([(470., 534.), (823., 832.)]) == [(470., 534.), (823., 832.)]
    assert gp.split([(30., 120.)]) == [(30., 88.), (88., 108.), (108., 120.)]


def test_choose_by_headroom():
    cands = [36.4, 40.2, 43.4, 49.6]
    # 20 dB headroom at 33.8: up to 14 dB more -> 43.4 (+9.6), not 49.6 (+15.8)
    assert gp.choose(33.8, cands, 20., NOMINAL) == 43.4
    assert gp.choose(33.8, cands, 30., NOMINAL) == 49.6
    assert gp.choose(33.8, cands, 8., NOMINAL) == 33.8        # 2 dB of room: none fits
    assert gp.choose(33.8, cands, 40., NOMINAL, clipped=True) == 33.8
    assert gp.choose(33.8, [], 40., NOMINAL) == 33.8


def test_candidates_need_a_floor_calibration():
    gains = [0., 28., 33.8, 36.4, 49.6]
    assert gp.candidates(gains, 33.8) == [36.4, 49.6]
    assert gp.candidates(gains, 33.8, has_floor=lambda g: g == 49.6) == [49.6]


def test_narrow_band_takes_the_lower_gain_and_bands_merge():
    bands = [gp.PlanBand(470, 560, 33.8), gp.PlanBand(560, 563, 49.6), ]
    out = gp._merge(bands)
    assert [(b.lo, b.hi, b.gain) for b in out] == [(470., 563., 33.8)]
    bands = [gp.PlanBand(560, 700, 49.6), gp.PlanBand(700, 790, 49.6), gp.PlanBand(790, 960, 33.8)]
    out = gp._merge(bands)
    assert [(b.lo, b.hi, b.gain) for b in out] == [(560., 790., 49.6), (790., 960., 33.8)]


def _gains(rx):
    from wwb_scanner.receivers import as_receiver
    return as_receiver(rx).gains()


def _air():
    # levels for 29.7 dB: a strong carrier in 470-560, a weak one in 560-700
    return sim.Air(noise=0.004).tone(520.0e6, 0.35).tone(600.0e6, 0.02)


def test_probe_raises_the_gain_only_in_the_quiet_band():
    rx = sim.FakeRtlSdr(air=_air(), gain_affects=True)
    rx.sample_rate = 2.4e6
    rx.gain = 33.8
    plan = gp.make_plan(rx, [(470., 700.)], 33.8, _gains(rx), 2.4e6)
    g = {(b.lo, b.hi): b.gain for b in plan.bands}
    assert g[(470., 560.)] == 33.8, plan.bands
    assert g[(560., 700.)] > 40., plan.bands
    assert plan.banded and plan.groups()[0][0] == 33.8
    assert rx.gain == 33.8                       # set back
    assert plan.seconds < 30


def test_probe_checks_the_new_gain(monkeypatch):
    """The probe step at 33.8 looked quiet, the check at the new gain clips:
    the next lower gain is used."""
    rx = sim.FakeRtlSdr(air=_air(), gain_affects=True)
    rx.sample_rate = 2.4e6
    rx.gain = 33.8
    real = rx.read_samples

    def read(n):
        x = real(n)
        if rx.gain >= 49.5 and n == gp.PROBE_SAMPLES:
            x = x * 0 + (1 + 1j)                  # full scale
        return x
    monkeypatch.setattr(rx, 'read_samples', read)
    plan = gp.make_plan(rx, [(560., 700.)], 33.8, _gains(rx), 2.4e6)
    b = plan.bands[0]
    assert 33.8 < b.gain < 49.6 and 'too strong' in b.why, b


def test_no_calibrated_gain_above_means_one_gain():
    rx = sim.FakeRtlSdr(air=_air(), gain_affects=True)
    plan = gp.make_plan(rx, [(470., 700.)], 33.8, _gains(rx), 2.4e6, has_floor=lambda g: False)
    assert not plan.banded and 'floor calibration' in plan.note
    assert rx.calls['read_samples'] == 0        # nothing probed


def test_level_shift_follows_the_real_gain():
    f = np.array([560., 600., 700.])
    sh = gp.level_shift(f, 49.6, 33.8, lambda a, b, fz: (a - b) - 1.0 * (fz < 650e6))
    assert np.allclose(sh, [14.8, 14.8, 15.8])
    assert np.allclose(gp.level_shift(f, 33.8, 33.8, NOMINAL), 0)


def _peak(f, y, at, half=0.05):
    m = np.abs(f - at) <= half
    return float(y[m].max())


def test_banded_scan(monkeypatch, tmpdir, caplog):
    """The whole scan through the real Scanner: two bands, two gains, one
    spectrum; a carrier keeps its level (at the gain of the scan)."""
    kw = dict(sdr_kwargs={'air': _air(), 'gain_affects': True}, auto_gain=True)
    plain, sdr0, _ = sim.run_scan(monkeypatch, tmpdir.mkdir('a'), 470., 700., **kw)
    f0, y0 = sim.spectrum_arrays(plain)
    with caplog.at_level(logging.INFO, logger='wwb_scanner.status'):
        sc, sdr, secs = sim.run_scan(monkeypatch, tmpdir.mkdir('b'), 470., 700., band_gain=True, **kw)
    assert sc.error is None
    plan = sc.gain_plan
    assert plan is not None and plan.banded, plan and plan.bands
    f, y = sim.spectrum_arrays(sc)
    assert f.min() <= 470.05 and f.max() >= 699.95
    assert np.all(np.diff(f) > 0)                       # one point per frequency
    gaps = np.diff(f)
    assert gaps.max() < 0.1, 'hole at %.3f MHz' % f[np.argmax(gaps)]
    # the dongle was read at both gains, and is back at the gain of the scan
    assert 33.8 in sdr.read_gains and max(sdr.read_gains) > 40.
    assert sdr.gain == 33.8 and sc.gain == 33.8
    assert sc.config.scan_range == [470., 700.] and not sc.config.get('scan_ranges')
    # levels at the gain of the scan: the same carriers as in the plain scan
    assert _peak(f, y, 600.0) == pytest.approx(_peak(f0, y0, 600.0), abs=1.0)
    assert _peak(f, y, 520.0) == pytest.approx(_peak(f0, y0, 520.0), abs=1.0)
    # the quiet band is measured more sensitively: its floor is not higher
    q = (f > 610) & (f < 690)
    q0 = (f0 > 610) & (f0 < 690)
    assert np.median(y[q]) <= np.median(y0[q0]) + 0.5
    info = sc.spectrum.cleanup_info
    assert info['ref_gain'] == 33.8 and len(info['gain_plan']) == 2
    assert [b['gain'] for b in info['bands']] == [33.8, max(g for g in sdr.read_gains)]
    assert 'Gain per band:' in caplog.text and '(gain per band: 470-560 33.8' in caplog.text
    # one scan in the history
    from wwb_scanner.utils import dbstore
    assert len(dbstore.db_store.get_scan_index()) == 1 if hasattr(dbstore.db_store, 'get_scan_index') else True


def test_stop_during_the_probe(monkeypatch, tmpdir, caplog):
    from wwb_scanner.scanner import main

    def plan_then_stop(self):
        self.stop_requested = True
        return gp.Plan(33.8, [gp.PlanBand(470, 700, 33.8)])
    monkeypatch.setattr(main.Scanner, 'make_gain_plan', plan_then_stop)
    with caplog.at_level(logging.INFO, logger='wwb_scanner.status'):
        sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 500., band_gain=True,
                                  sdr_kwargs={'air': _air()})
    assert 'stopped during the gain probe' in caplog.text
    assert sc.spectrum.sample_data.size == 0


def test_one_gain_when_the_plan_is_flat(monkeypatch, tmpdir):
    """Nothing to raise (a strong carrier everywhere): the ordinary scan."""
    air = sim.Air(noise=0.004).tone(480e6, 0.4).tone(495e6, 0.4).tone(510e6, 0.4)
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 515., band_gain=True,
                              sdr_kwargs={'air': air, 'gain_affects': True})
    assert sc.gain_plan is not None and not sc.gain_plan.banded
    assert 'gain_plan' not in (getattr(sc.spectrum, 'cleanup_info', None) or {})
    assert set(sdr.read_gains) == {33.8}


def test_calibrations_never_use_band_gain():
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    base = ScanConfig({'scan_range': [470, 700]})
    base.sampling.band_gain = True
    for kind in (service.FLOOR_CALIBRATION, service.GAIN_CALIBRATION):
        conf, _ = service.prepare(kind, base)
        assert conf.sampling.band_gain is False
    conf, _ = service.prepare(service.SCAN, base)
    assert conf.sampling.band_gain is True


def test_default_is_off_until_checked_on_the_pc():
    from wwb_scanner.scanner import config
    assert config.TESTED['band_gain'] is False
    assert config.SamplingConfig().band_gain is False


def test_merge_band_infos():
    from wwb_scanner.scanner.main import merge_band_infos
    plan = gp.Plan(33.8, [gp.PlanBand(470, 560, 33.8), gp.PlanBand(560, 700, 49.6)], seconds=3.2)
    a = {'ranges': [[470, 560]], 'gain': 33.8, 'spurs': True, 'spurs_removed': [475.2], 'flatten': True,
         'ripple': True, 'calibration': '18.11'}
    b = {'ranges': [[560, 700]], 'gain': 49.6, 'spurs': True, 'spurs_removed': [590.4, 619.2],
         'flatten': False, 'floor_skipped': 'no calibration for gain 49.6 dB'}
    m = merge_band_infos([a, b], plan)
    assert m['spurs_removed'] == [475.2, 590.4, 619.2] and m['spurs'] and m['ripple']
    assert m['flatten'] is False and m['floor_skipped'].startswith('560-700: no calibration')
    assert m['gain_plan'] == [[470, 560, 33.8], [560, 700, 49.6]] and m['gain_probe_s'] == 3.2


def _flat_profile(gain, floor_db=-104.):
    from wwb_scanner.scanner import cleanup
    f = np.arange(31., 1766.0001, 0.025)
    y = np.full(f.size, floor_db) + 0.05 * np.sin(2 * np.pi * np.arange(f.size) / 31.)
    return cleanup.build_profile(f, y, 31., 31, meta={'gain': gain, 'sample_rate': 2.4e6,
                                                      'created': '2026-11-19 12:00:00'})


def test_banded_scan_levels_each_band_with_its_own_calibration(monkeypatch, tmpdir):
    """Floor levelled: only gains with a floor calibration are candidates
    (49.6 here, not 36.4 ... 48.0), and every band is levelled with the
    calibration of its own gain."""
    from wwb_scanner import paths
    from wwb_scanner.scanner import cleanup
    paths.set_data_dir(str(tmpdir.join('data')))
    try:
        for g in (33.8, 49.6):
            cleanup.save_profile(_flat_profile(g), cleanup.profile_path(g, 2.4e6))
        sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 700., band_gain=True, auto_gain=True,
                                  clean={'clean_ripple': True, 'clean_spurs': True, 'clean_floor': True},
                                  sdr_kwargs={'air': _air(), 'gain_affects': True})
        plan = sc.gain_plan
        assert plan.banded and sorted({b.gain for b in plan.bands}) == [33.8, 49.6]
        info = sc.spectrum.cleanup_info
        assert info['flatten'] is True and 'floor_skipped' not in info
        assert [b['flatten'] for b in info['bands']] == [True, True]
        assert info['calibration'] == '2026-11-19 12:00:00'
        assert np.size(sc.spectrum.raw_dbFS) == sc.spectrum.sample_data.size
    finally:
        paths.set_data_dir(None)
