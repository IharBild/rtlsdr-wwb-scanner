"""V20.9: second wave of the code audit of 11.11.2026 and the user's scans of
11-12.11.2026.

A1  The spur cleaning erased ANY narrow carrier on a multiple of 28.8 MHz,
    however strong (up to +47 dB over the floor): a microphone exactly there
    disappeared from the scan and from the files for WWB. Now a narrow
    carrier on the grid is hidden only up to +20 dB over the floor; a
    stronger one is kept and named.
    The odd multiples of 14.4 MHz (475.2, 504.0, 532.8 ... MHz) were not in
    the list at all: +6.8 dB without antenna at 49.6 dB, +16.5 dB with it.
A2  The image check erased a real signal 2.4 MHz (or 28.8 MHz) from a strong
    carrier when it was 36...52 dB (60...80 dB) below it. Now an image is
    only marked; "Hide images of strong signals" replaces it as before.
A6  The floor calibration (no antenna) kept the transmitters that leak into
    the dongle anyway (12.11.2026, 49.6 dB: TV 518-526 MHz +5.6 dB, 423-424
    MHz +11 dB): every scan was lowered by that much there."""
import json
import os
import subprocess
import sys
import textwrap

import numpy as np
import pytest

import sim
import wwb_scanner
from wwb_scanner.scanner import cleanup as C

QML = os.path.join(os.path.dirname(os.path.abspath(wwb_scanner.__file__)), 'ui', 'pyside', 'qml')
BIN = 0.025


def flat(lo=460.0, hi=620.0, seed=0, level=-100.0):
    rng = np.random.RandomState(seed)
    f = np.round(np.arange(lo, hi + 1e-9, BIN), 4)
    return f, level + rng.randn(f.size) * 0.1


def tone(f, y, mhz, db, floor=-100.0, wide_mhz=0.0):
    """A carrier ``db`` over the floor: 3 points (hann window, 25 kHz) or a
    transmitter ``wide_mhz`` wide."""
    y = y.copy()
    i = int(np.argmin(np.abs(f - mhz)))
    if wide_mhz:
        m = np.abs(f - mhz) <= wide_mhz / 2.
        y[m] = 10 * np.log10(10 ** (y[m] / 10.) + 10 ** ((floor + db) / 10.))
        return y
    for k, drop in ((-1, 6.), (0, 0.), (1, 6.)):
        y[i + k] = 10 * np.log10(10 ** (y[i + k] / 10.) + 10 ** ((floor + db - drop) / 10.))
    return y


def at(f, y, mhz):
    return float(y[int(np.argmin(np.abs(f - mhz)))])


# ------------------------------------------------------------------ A1: grid

def test_spur_grid_is_every_14_4_mhz_and_480():
    got = C.spur_freqs(470., 540.)
    assert got == [475.2, 480.0, 489.6, 504.0, 518.4, 532.8]


@pytest.mark.parametrize('mhz', [475.2, 504.0, 532.8, 561.6, 590.4])
def test_odd_multiples_of_14_4_mhz_are_hidden(mhz):
    # the user's scans of 11-12.11.2026: these stayed in every scan
    f, y = flat()
    y = tone(f, y, mhz, 10.)
    assert at(f, y, mhz) > -91.
    c, hidden, kept = C.hide_spurs(f, y)
    assert hidden == [mhz] and kept == []
    assert abs(at(f, c, mhz) + 100.) < 0.5


# ----------------------------------------------------------------- A1: limit

@pytest.mark.parametrize('mhz', [475.2, 489.6, 504.0, 518.4, 480.0])
@pytest.mark.parametrize('db', [3., 8., 12., 16., 19., 22., 30., 40., 46.])
def test_narrow_carrier_on_the_grid_hidden_only_up_to_the_limit(mhz, db):
    f, y = flat()
    y = tone(f, y, mhz, db)
    c, hidden, kept = C.hide_spurs(f, y)
    if db <= 19.:
        assert hidden == [mhz] and kept == [], (hidden, kept)
        assert abs(at(f, c, mhz) + 100.) < 0.5
    else:
        # too strong for an own spur: may be a microphone - it stays
        assert hidden == [] and len(kept) == 1, (hidden, kept)
        assert kept[0][0] == mhz and kept[0][1] == pytest.approx(db, abs=1.0)
        assert np.array_equal(c, y) or np.allclose(c, y, atol=1e-9)


def test_before_v209_the_strong_carrier_was_erased():
    # the defect itself (limit switched off = the old rule)
    f, y = flat()
    y = tone(f, y, 518.4, 40.)
    c, hidden, kept = C.hide_spurs(f, y, limit_db=float('inf'))
    assert hidden == [518.4] and at(f, c, 518.4) < -99.
    c, hidden = C.remove_spurs(f, y)                 # the old name: new rule
    assert hidden == [] and at(f, c, 518.4) > -61.


def test_wide_transmitter_on_the_grid_is_neither_hidden_nor_marked():
    f, y = flat()
    y = tone(f, y, 504.0, 15., wide_mhz=0.2)
    c, hidden, kept = C.hide_spurs(f, y)
    assert hidden == [] and kept == []
    assert np.allclose(c, y, atol=1e-9)


def test_carrier_off_the_grid_is_never_touched():
    f, y = flat()
    for mhz in (500.0, 504.1, 511.2, 525.6):
        y = tone(f, y, mhz, 10.)
    c, hidden, kept = C.hide_spurs(f, y)
    assert hidden == [] and kept == [] and np.allclose(c, y, atol=1e-9)


def test_limit_follows_the_calibration_where_the_own_spur_is_stronger():
    # 86.4 MHz at 49.6 dB gain: +21.5 dB without antenna (user's calibration)
    f, y = flat(60., 120.)
    y = tone(f, y, 86.4, 25.)
    assert C.hide_spurs(f, y)[1] == []                           # 25 > 20: kept
    c, hidden, kept = C.hide_spurs(f, y, known={86.4: 21.5})     # limit 27.5
    assert hidden == [86.4] and kept == []
    y2 = tone(f, flat(60., 120.)[1], 86.4, 30.)
    assert C.hide_spurs(f, y2, known={86.4: 21.5})[2][0][0] == 86.4


def test_known_spur_levels_reads_old_and_new_calibrations():
    # before V20.9 the odd multiples of 14.4 MHz were listed as "external"
    prof = {'spurs': [[86.4, 21.5], [489.6, 9.6], [480.0, 5.8]],
            'external': [[475.2, 6.8], [504.0, 6.4], [936.625, 16.3], [423.175, 4.3]]}
    got = C.known_spur_levels(prof)
    assert got == {86.4: 21.5, 489.6: 9.6, 480.0: 5.8, 475.2: 6.8, 504.0: 6.4}
    assert C.known_spur_levels(None) == {} and C.known_spur_levels({}) == {}


def test_clean_reports_hidden_kept_and_the_limit():
    f, y = flat()
    y = tone(f, y, 489.6, 9.)
    y = tone(f, y, 504.0, 12.)
    y = tone(f, y, 532.8, 33.)
    c, info = C.clean(f, y, 460.0, 31, ripple=False, flatten=False)
    assert info['spurs_removed'] == [489.6, 504.0]
    assert info['spurs_kept'] == [[532.8, pytest.approx(33., abs=1.)]]
    assert info['spur_limit_db'] == 20.0
    json.dumps(info)                                             # saved with the scan
    assert at(f, c, 532.8) > -68.


# --------------------------------------------------- A1: real scans, simulator

def _air(tones):
    a = sim.Air(noise=0.005)
    for mhz, amp in tones:
        a.tone(mhz * 1e6, amp)
    return a


def _excess(f, y, mhz):
    i = int(np.argmin(np.abs(f - mhz)))
    base = np.median(np.r_[y[i - 10:i - 4], y[i + 5:i + 11]])
    return float(y[i - 1:i + 2].max() - base)


@pytest.mark.parametrize('mhz', [475.2, 489.6, 504.0, 518.4])
@pytest.mark.parametrize('amp', [0.002, 0.006, 0.05, 0.3])
def test_scan_with_a_carrier_on_the_grid(monkeypatch, tmpdir, mhz, amp):
    """The extended test asked for on 11.11.2026: carriers on the spur grid,
    from weaker than an own spur to a microphone, through the whole scan."""
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, mhz - 6., mhz + 6., sdr_kwargs={'air': _air([(mhz, amp)])},
                              clean={'clean_spurs': True, 'clean_ripple': True})
    assert sc.error is None
    sp = sc.spectrum
    f, y = sim.spectrum_arrays(sc)
    raw = np.asarray(sp.raw_dbFS, float)
    was = _excess(f, raw, mhz)
    info = sp.cleanup_info
    if was > C.SPUR_HIDE_MAX_DB + 1.5:
        assert info['spurs_removed'] == [] and [k[0] for k in info['spurs_kept']] == [mhz], info
        assert _excess(f, y, mhz) == pytest.approx(was, abs=0.6)        # still there, at its level
    elif 1.0 < was < C.SPUR_HIDE_MAX_DB - 1.5:
        assert info['spurs_removed'] == [mhz] and info['spurs_kept'] == [], info
        assert _excess(f, y, mhz) < 1.0
    else:
        pytest.skip('level %.1f dB is at the limit or in the noise' % was)
    assert _excess(f, raw, mhz) == pytest.approx(was)                   # as measured: kept with the scan


def test_levels_of_the_parametrized_scan_cover_both_sides(monkeypatch, tmpdir):
    # the test above must not silently skip everything
    seen = []
    for amp in (0.002, 0.006, 0.05, 0.3):
        sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 498., 510., sdr_kwargs={'air': _air([(504.0, amp)])})
        f, y = sim.spectrum_arrays(sc)
        seen.append(_excess(f, y, 504.0))
    assert min(seen) < C.SPUR_HIDE_MAX_DB - 1.5 and max(seen) > C.SPUR_HIDE_MAX_DB + 10., seen
    assert sum(1 for s in seen if 1.0 < s < C.SPUR_HIDE_MAX_DB - 1.5) >= 1, seen
    assert sum(1 for s in seen if s > C.SPUR_HIDE_MAX_DB + 1.5) >= 2, seen


def test_status_line_names_what_was_kept(monkeypatch, tmpdir, caplog):
    import logging
    caplog.set_level(logging.INFO)
    air = _air([(489.6, 0.004), (504.0, 0.3)])
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 484., 510., sdr_kwargs={'air': air},
                              clean={'clean_spurs': True})
    status = [r.getMessage() for r in caplog.records if r.name == 'wwb_scanner.status']
    assert status, caplog.records
    assert '1 own spurs hidden' in status[-1] and 'kept on the spur grid: 504.000 (+' in status[-1], status[-1]


def test_spurs_switched_off_hides_nothing(monkeypatch, tmpdir):
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 498., 510., sdr_kwargs={'air': _air([(504.0, 0.006)])},
                              clean={'clean_spurs': False, 'clean_ripple': True})
    f, y = sim.spectrum_arrays(sc)
    assert _excess(f, y, 504.0) > 3. and not sc.spectrum.cleanup_info.get('spurs')


# ------------------------------------------------------------------------ A2

MIC = 632.925


def _mic_scan(monkeypatch, tmpdir, gain=0.0, extra=(), clean=None, auto_gain=True):
    a = sim.Air(noise=0.005)
    a.tone(MIC * 1e6, 20.)
    for mhz, amp in extra:
        a.tone(mhz * 1e6, amp)
    sk = {'air': a, 'gain_affects': True, 'wide_adc': sim.WideAdc()}
    return sim.run_scan(monkeypatch, tmpdir, 626, 640, gain=gain, auto_gain=auto_gain, sdr_kwargs=sk, clean=clean)


def _peak(f, y, mhz):
    ring = (np.abs(f - mhz) > 0.15) & (np.abs(f - mhz) < 0.4)
    return float(y[np.abs(f - mhz) <= 0.05].max() - np.median(y[ring]))


def test_image_is_marked_and_left_in_the_scan(monkeypatch, tmpdir):
    sc, sdr, _ = _mic_scan(monkeypatch, tmpdir)
    coll = sc.sample_collection
    assert 635.325 in [p for p, d, c in coll.images], coll.images
    assert coll.images_hidden is False
    f, y = sim.spectrum_arrays(sc)
    assert _peak(f, y, 635.325) > 6.                           # still in the data (and in the files)
    marks = sc.spectrum.image_marks
    assert marks and marks[0]['hidden'] is False and marks[0]['of'] == pytest.approx(MIC, abs=0.05)
    assert 'possible images of the strong signal at' in coll.overload_note
    assert '(marked, not removed)' in coll.overload_note
    assert 'removed at' not in coll.overload_note


def test_image_is_hidden_only_on_request(monkeypatch, tmpdir):
    sc, sdr, _ = _mic_scan(monkeypatch, tmpdir, clean={'clean_images': True})
    coll = sc.sample_collection
    assert 635.325 in [p for p, d, c in coll.images] and coll.images_hidden is True
    f, y = sim.spectrum_arrays(sc)
    assert _peak(f, y, 635.325) < 3.
    assert sc.spectrum.image_marks[0]['hidden'] is True
    assert 'images of the strong signal removed at' in coll.overload_note


def test_weak_real_signal_at_the_image_frequency_survives(monkeypatch, tmpdir):
    """Audit A2: a far microphone 2.4 MHz above a near one, about 44 dB
    weaker. Before V20.9 it was erased as an image."""
    quiet = _mic_scan(monkeypatch, tmpdir)[0]
    fq, yq = sim.spectrum_arrays(quiet)
    image_only = at(fq, yq, 635.325)
    weak = (635.325, 20. * 10 ** (-41. / 20.))               # a real carrier, -41 dBc
    sc, sdr, _ = _mic_scan(monkeypatch, tmpdir, extra=[weak])
    f, y = sim.spectrum_arrays(sc)
    assert at(f, y, 635.325) > image_only + 1.5              # the real signal is in the scan
    assert _peak(f, y, 635.325) > 8.
    # with hiding switched on it is taken for an image - the known cost
    sc2, sdr, _ = _mic_scan(monkeypatch, tmpdir, extra=[weak], clean={'clean_images': True})
    f2, y2 = sim.spectrum_arrays(sc2)
    assert _peak(f2, y2, 635.325) < 3.


def test_raw_scan_has_no_image_marks(monkeypatch, tmpdir):
    sc, sdr, _ = _mic_scan(monkeypatch, tmpdir, auto_gain=False)
    assert sc.sample_collection.images == []


# ------------------------------------------------------------------------ A6

def _floor_with_leaks():
    f = np.round(np.arange(31.0, 1000.0 + 1e-9, BIN), 4)
    floor = np.full(f.size, -92.0) - 0.004 * (f - 31.0)
    # the dongle's own steps: up and slowly down again (75, 278 MHz)
    for edge, up in ((75., 1.9), (278., 3.7)):
        m = f >= edge
        floor[m] += up * np.exp(-(f[m] - edge) / 45.)
    own = floor.copy()
    for lo, hi, db in ((423.0, 424.4, 11.), (486., 494., 1.5), (518., 526., 5.6), (534., 542., 2.), (930., 942., 3.5)):
        m = (f >= lo) & (f <= hi)
        floor[m] += db - 1.0 * (f[m] - lo) / (hi - lo)      # a multiplex is not flat
    return f, floor, own


def test_leaked_signals_are_left_out_of_the_floor_shape():
    f, floor, own = _floor_with_leaks()
    got, leaks = C.ignore_leaks(f, floor)
    assert np.max(np.abs(got - own)) < 0.35, float(np.max(np.abs(got - own)))
    assert len(leaks) == 5, leaks
    for (lo, hi, db), want in zip(leaks, (423.7, 490., 522., 538., 936.)):
        assert lo < want < hi
    assert leaks[0][2] == pytest.approx(11., abs=0.6) and leaks[2][2] == pytest.approx(5.6, abs=0.6)


def test_own_steps_of_the_floor_stay():
    f, floor, own = _floor_with_leaks()
    got, leaks = C.ignore_leaks(f, own)
    assert leaks == [] and np.array_equal(got, own)
    got, leaks = C.ignore_leaks(f, floor)
    for edge in (75., 278.):
        m = (f > edge - 10.) & (f < edge + 30.)
        assert np.array_equal(got[m], floor[m])              # exactly as measured around a step


def test_short_or_other_spacing_is_returned_as_it_is():
    f = np.round(np.arange(500., 510., BIN), 4)
    y = np.full(f.size, -95.)
    y[150:250] += 5.
    got, leaks = C.ignore_leaks(f, y)
    assert leaks == [] and np.array_equal(got, y)
    f2 = np.arange(31., 1000., 0.1)
    got, leaks = C.ignore_leaks(f2, np.full(f2.size, -95.))
    assert leaks == []


def _calibration_scan(seed=3):
    rng = np.random.RandomState(seed)
    f = np.round(np.arange(31.0, 1000.0 + 1e-9, BIN), 4)
    y = -95.7 + rng.randn(f.size) * 0.15
    for lo, hi, db in ((518., 526., 5.6), (423.0, 424.4, 11.)):
        m = (f >= lo) & (f <= hi)
        y[m] += db
    return f, y


def test_calibration_no_longer_lowers_the_tv_channel(tmpdir, monkeypatch):
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    f, y = _calibration_scan()
    meta = {'gain': 49.6, 'sample_rate': 2.4e6}
    saved, text = C.calibrate(f, y, 31.0, 0.775, meta)
    assert saved, text
    assert 'outside signals left out of the floor shape: 420-426, 514-528 MHz' in text, text
    prof, why = C.find_profile(49.6, 2.4e6)
    assert prof is not None and prof['version'] == 2 and len(prof['leaks']) == 2, (why, prof.get('leaks'))
    # a scan with a real signal in the TV channel keeps its level
    g = np.round(np.arange(500., 540. + 1e-9, BIN), 4)
    scan = np.full(g.size, -95.7)
    scan[(g >= 518.) & (g <= 526.)] = -60.
    c, info = C.clean(g, scan, 500.0, 31, profile=prof, ripple=False, spurs=False, flatten=True)
    assert at(g, c, 522.) == pytest.approx(-60., abs=0.3)     # was -65.6
    assert at(g, c, 510.) == pytest.approx(-95.7, abs=0.3)


def test_calibration_made_before_v209_is_corrected_when_it_is_used(tmpdir, monkeypatch):
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    f, y = _calibration_scan()
    monkeypatch.setattr(C, 'ignore_leaks', lambda fr, fl: (np.asarray(fl, float).copy(), []))
    old = C.build_profile(f, y, 31.0, 31, meta={'gain': 49.6, 'sample_rate': 2.4e6})
    monkeypatch.undo()
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    old['version'] = 1
    old.pop('leaks', None)
    assert at(f, old['floor_db'], 522.) > -91.                # the TV is in the floor shape
    path = C.save_profile(old, C.profile_path(49.6, 2.4e6))
    before = open(path, 'rb').read()
    prof, why = C.find_profile(49.6, 2.4e6)
    assert prof is not None, why
    assert at(f, prof['floor_db'], 522.) == pytest.approx(-95.7, abs=0.3)
    assert len(prof['leaks']) == 2
    assert prof['floor_ref_db'] == old['floor_ref_db']        # levels elsewhere do not move
    assert open(path, 'rb').read() == before                  # the file is not rewritten


# -------------------------------------------------------------------- window

def test_new_switch_in_the_settings_files():
    conf = open(os.path.join(QML, 'ScanConfig.qml'), encoding='utf-8').read()
    assert conf.count('cleanImages') == 5 and 'cleanImages: false' in conf
    dlg = open(os.path.join(QML, 'ScanControlsDialog.qml'), encoding='utf-8').read()
    assert "objectName: 'cleanImagesSwitch'" in dlg and "text: \"Hide dongle's own spurs\"" in dlg
    assert 'config.cleanImages = root.cleanImages;' in dlg and 'root.cleanImages = config.cleanImages;' in dlg
    graph = open(os.path.join(QML, 'Graph.qml'), encoding='utf-8').read()
    assert "objectName: 'spurMarkRepeater'" in graph and 'function spurMarkModel()' in graph


def test_library_defaults():
    from wwb_scanner.scanner import config
    assert config.ProcessingConfig.DEFAULTS['clean_images'] is False
    assert config.ProcessingConfig.DEFAULTS['clean_spurs'] is False


def _isolated(code, timeout=180):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p),
               QT_QPA_PLATFORM='offscreen')
    prelude = textwrap.dedent('''
        import sys, types, os, tempfile
        os.environ['HOME'] = os.environ['USERPROFILE'] = tempfile.mkdtemp(prefix='v209_')
        try:
            import PySide2
        except ImportError:
            print('NO_PYSIDE2'); raise SystemExit(0)
        try:
            import rtlsdr
        except Exception:
            m = types.ModuleType('rtlsdr')
            class RtlSdr(object):
                def __init__(self, *a, **k):
                    raise IOError('No devices found')
                @staticmethod
                def get_device_serial_addresses():
                    return []
            m.RtlSdr = RtlSdr
            m.RtlSdrTcpClient = None
            sys.modules['rtlsdr'] = m
        from wwb_scanner import scan_dll
        scan_dll._cache['lib'] = None
    ''')
    r = subprocess.run([sys.executable, '-c', prelude + textwrap.dedent(code)], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = r.stdout.decode('utf-8', 'replace')
    if 'NO_PYSIDE2' in out:
        pytest.skip('PySide2 not installed')
    assert r.returncode == 0 and 'ALL_OK' in out, 'exit code %s\n%s' % (r.returncode, out[-3000:])
    return out


def test_window_settings_reach_the_scan_and_the_graph_gets_the_marks():
    _isolated("""
    import types
    from PySide2 import QtCore
    from PySide2.QtWidgets import QApplication
    from wwb_scanner.ui.pyside import scanner as S, graph as G
    app = QApplication([])
    class Dev(QtCore.QObject):
        device_serial = '00000001'
    si = S.ScannerInterface()
    si.scanConfig = S.ScanConfigData()
    si.scanConfig.startFreq, si.scanConfig.endFreq = 470., 474.
    si.gain = 33.8
    si.sampleRate = 2400.
    si.deviceInfo = Dev()
    # images: marked only, unless switched on
    assert si.scanConfig.cleanImages is False and si.scanConfig.cleanSpurs is True
    conf = si.build_scan_config()
    assert conf.processing.clean_images is False and conf.processing.clean_spurs is True
    si.scanConfig.cleanImages = True
    assert si.build_scan_config().processing.clean_images is True
    # calibration scans are never cleaned
    si.calibrating = True
    c = si.build_scan_config()
    assert c.processing.clean_images is False and c.processing.clean_spurs is False
    si.calibrating = False
    # graph marks from what the cleaning did
    sp = types.SimpleNamespace(cleanup_info={'spurs': True, 'spurs_removed': [489.6, 504.0],
                                             'spurs_kept': [[518.4, 32.4]]})
    marks = G.SpectrumGraphData.spur_marks_of(sp)
    assert marks == [{'f': 489.6, 'db': 0., 'kept': False}, {'f': 504.0, 'db': 0., 'kept': False},
                     {'f': 518.4, 'db': 32.4, 'kept': True}], marks
    assert G.SpectrumGraphData.spur_marks_of(types.SimpleNamespace(cleanup_info={'spurs': False})) == []
    assert G.SpectrumGraphData.spur_marks_of(types.SimpleNamespace()) == []
    assert G.SpectrumGraphData.spur_marks_of(None) == []
    print('ALL_OK')
    """)


def test_scan_opened_from_the_history_knows_what_was_hidden(monkeypatch, tmpdir):
    air = _air([(489.6, 0.004), (504.0, 0.3)])
    sc, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 484., 510., sdr_kwargs={'air': air},
                              clean={'clean_spurs': True})
    info = sc.spectrum.cleanup_info
    assert info['spurs_removed'] == [489.6] and info['spurs_kept'][0][0] == 504.0
    from wwb_scanner.scan_objects import Spectrum
    back = Spectrum.from_dbstore(eid=sc.spectrum.eid)
    assert back.cleanup_info['spurs_removed'] == [489.6]
    assert back.cleanup_info['spurs_kept'][0][0] == 504.0
