"""V22.12 (decisions of 23.11.2026 after qa_pc on the V4 with qa-kit-9.35):
DLL scanner-v8 is the default driver, "Own gain per band" is on by default,
qa_hw H13 compares with one-gain scans before and after."""
import importlib.util
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _tool(name):
    p = os.path.join(ROOT, 'tools', name + '.py')
    if not os.path.exists(p):
        pytest.skip('no tools in this copy')
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    try:
        spec = importlib.util.spec_from_file_location(name + '_v2212', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


# ---------------------------------------------------------------- driver v8

def test_portable_build_takes_driver_v8():
    p = os.path.join(ROOT, 'tools', 'portable', 'build.py')
    if not os.path.exists(p):
        pytest.skip('no tools/portable in this copy')
    src = open(p, encoding='utf-8').read()
    assert "DRIVER = os.path.join(ROOT, 'drivers', 'rtlsdr-scanner-v8', 'x64')" in src
    assert os.path.exists(os.path.join(ROOT, 'drivers', 'rtlsdr-scanner-v8', 'x64', 'rtlsdr.dll'))
    wf = os.path.join(ROOT, '.github', 'workflows', 'portable.yml')
    if os.path.exists(wf):
        assert "drivers/rtlsdr-scanner-v8/x64/**" in open(wf, encoding='utf-8').read()


def test_readme_of_v8_says_checked_on_the_dongle():
    p = os.path.join(ROOT, 'drivers', 'rtlsdr-scanner-v8', 'README_RU.txt')
    if not os.path.exists(p):
        pytest.skip('no drivers in this copy')
    text = open(p, encoding='utf-8').read()
    assert 'проверено' in text and '23.11.2026' in text and 'по умолчанию' in text
    assert 'НЕ проверено' not in text


# ---------------------------------------------------------------- own gain per band on

def test_band_gain_is_on_by_default():
    from wwb_scanner.scanner import config
    assert config.TESTED['band_gain'] is True
    assert config.ScanConfig().sampling.band_gain is True


def test_window_switches_it_on_once():
    p = os.path.join(ROOT, 'wwb_scanner', 'ui', 'pyside', 'qml', 'main.qml')
    qml = open(p, encoding='utf-8').read()
    assert 'property bool bandGainOn2212: false' in qml
    assert 'function enableBandGainOnce()' in qml
    assert 'enableBandGainOnce(); }' in qml


def test_a_default_scan_runs_with_own_gain_per_band(monkeypatch, tmpdir):
    """Settings left at the defaults: the probe runs (one gain when no band
    can take more - no floor calibrations in the simulator)."""
    import sim
    from wwb_scanner.scanner import main, sdrwrapper
    from wwb_scanner.scanner.config import ScanConfig
    from wwb_scanner import scan_dll
    monkeypatch.setattr(sdrwrapper, 'RtlSdr', lambda serial_number=None, device_index=None:
                        sim.FakeRtlSdr(serial_number=serial_number))
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    monkeypatch.setenv('WWB_SCANNER_DATA', str(tmpdir.join('data')))
    conf = ScanConfig({'scan_range': [470., 476.]})
    assert conf.sampling.band_gain is True
    sc = main.Scanner(config=conf, history=None)
    sc.run_scan()
    assert sc.error is None and sc.spectrum.sample_data.size > 0
    assert getattr(sc, 'gain_plan', None) is not None


# ---------------------------------------------------------------- H13

def _scan(f, y):
    return {'f': np.asarray(f, float), 'y': np.asarray(y, float)}


def _air(f, carriers):
    y = np.full(f.size, -100.)
    for c, lvl in carriers:
        y[np.abs(f - c) < 0.5] = lvl
    return y


F = np.arange(470., 800., 0.025)
BANDS = [(470., 560., 38.6), (560., 800., 33.8)]


def test_h13_levels_where_the_air_is_stable():
    q = _tool('qa_hw')
    air = _air(F, [(505., -80.), (545., -76.), (600., -70.)])
    band = air.copy()
    band[F < 560] -= 0.3
    band[(F < 560) & (band < -95)] = -104.    # lower floor in the raised band
    st, text = q.h13_compare(F, band, BANDS, 33.8, _scan(F, air), _scan(F, air + 0.2))
    assert st == 'PASS', text
    assert '470-560: пол -100.0 / -99.8 (до / после) -> -104.0' in text


def test_h13_air_that_changed_is_left_out():
    """PC 23.11: two bands vanished between H9 and H13; with a scan after
    H13 they are not 'stable' and do not count."""
    q = _tool('qa_hw')
    before = _air(F, [(505., -80.), (545., -76.), (600., -70.), (700., -72.)])
    after = _air(F, [(505., -97.), (545., -93.), (600., -70.), (700., -72.)])
    band = after.copy()
    st, text = q.h13_compare(F, band, BANDS, 33.8, _scan(F, before), _scan(F, after))
    assert st == 'PASS', text
    assert 'разница +0.00 дБ' in text


def test_h13_wrong_level_is_a_warning():
    q = _tool('qa_hw')
    air = _air(F, [(600., -70.), (700., -72.)])
    band = air.copy()
    band[band > -90] -= 5.
    st, text = q.h13_compare(F, band, BANDS, 33.8, _scan(F, air), _scan(F, air))
    assert st == 'WARN' and 'разница -5.00 дБ' in text


def test_h13_without_stable_points_says_so():
    q = _tool('qa_hw')
    before = _air(F, [(600., -70.)])
    after = _air(F, [(600., -80.)])
    st, text = q.h13_compare(F, after, BANDS, 33.8, _scan(F, before), _scan(F, after))
    assert st == 'INFO' and 'эфир менялся' in text


def test_h13_higher_floor_in_a_raised_band_is_a_warning():
    q = _tool('qa_hw')
    air = _air(F, [(600., -70.), (700., -72.)])
    band = air.copy()
    band[(F < 560)] = -98.
    st, text = q.h13_compare(F, band, BANDS, 33.8, _scan(F, air), _scan(F, air))
    assert st == 'WARN' and '(+2.0)' in text
