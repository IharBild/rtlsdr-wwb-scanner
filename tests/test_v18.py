"""V18 "Bands": system bands, country rules, several ranges in one scan,
export without invented values in the gaps."""
import io
import json
import math

import numpy as np
import pytest

import sim
from wwb_scanner import bands as B

CLEAN = {'clean_ripple': True, 'clean_spurs': True, 'clean_floor': False}

EU27 = ['AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT',
        'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE']
SCHENGEN_EEA = ['NO', 'IS', 'CH', 'LI']
ASKED = ['GB', 'UA', 'BY', 'RS']


# ------------------------------------------------------------------ data

def test_B1_all_asked_countries_and_valid_rules():
    assert set(B.COUNTRIES) == set(EU27 + SCHENGEN_EEA + ASKED)
    assert sorted(B.COUNTRY_ORDER) == sorted(B.COUNTRIES)
    for code, c in B.COUNTRIES.items():
        assert c['source'], code
        for lo, hi, st, note in c['rules']:
            assert lo < hi and st in B.STATUS, (code, lo, hi, st)


def test_B2_systems_valid_and_unique():
    ids = [s['id'] for s in B.SYSTEMS]
    assert len(ids) == len(set(ids))
    names = set(s['system'] for s in B.SYSTEMS)
    for want in ('PSM 300', 'PSM 900', 'PSM 1000', 'QLX-D', 'ULX-D', 'SLX-D', 'Axient Digital',
                 'BLX', 'PGX', 'GLX-D', 'ew 100 G3/G4', 'ew 300 G4', 'UHF-R'):
        assert want in names, want
    for s in B.SYSTEMS:
        for lo, hi in s['ranges']:
            assert 30 <= lo < hi <= 2500, s['id']


@pytest.mark.parametrize('code', sorted(B.COUNTRIES))
def test_B3_regions_cover_without_holes(code):
    r = B.regions(code, 24, 1766)
    assert r[0]['lo'] == 24 and r[-1]['hi'] == 1766
    for a, b in zip(r[:-1], r[1:]):
        assert abs(a['hi'] - b['lo']) < 1e-9
        assert (a['status'], a['note']) != (b['status'], b['note'])     # merged
    for x in r:
        assert x['status'] in B.STATUS


def _at(code, f):
    for x in B.regions(code, f - 0.01, f + 0.01):
        return x['status']


@pytest.mark.parametrize('code,f,want', [
    ('PL', 500, 'free'), ('PL', 720, 'no'), ('PL', 737, 'limited'), ('PL', 800, 'no'),
    ('PL', 827, 'allowed'), ('PL', 864, 'free'), ('PL', 1380, 'no'),
    ('DE', 611, 'no'), ('DE', 620, 'free'), ('DE', 745, 'free'), ('DE', 1500, 'limited'),
    ('LT', 600, 'light'), ('NL', 557, 'no'), ('NL', 610, 'licence'), ('RO', 578, 'licence'),
    ('RO', 600, 'free'), ('UA', 750, 'licence'), ('BY', 600, 'unknown'), ('ES', 175.5, 'limited'),
    ('ES', 200, 'no'), ('GB', 610, 'licence'), ('NO', 480, 'unknown'), ('NO', 500, 'free'),
])
def test_B4_known_rules(code, f, want):
    assert _at(code, f) == want


def test_B5_system_summary_warns():
    s = B.system_summary('DE', 'Shure|PSM 1000|L9E')           # 670-742 crosses LTE 700
    assert s['status'] == 'no' and any('LTE' in l for l in s['lines'])
    s = B.system_summary('PL', 'Shure|QLX-D|G51')
    assert s['status'] == 'free'
    s = B.system_summary('PL', 'Sennheiser|ew 300 G4|1G8')
    assert any('can not scan' in l for l in s['lines'])


def test_B6_segments_merge_and_clip():
    sel = B.selection(['Shure|QLX-D|G51', 'Shure|PSM 300|S8', 'Sennheiser|ew 100 G3/G4|A1',
                       'Sennheiser|ew 300 G4|1G8', 'no|such|band'])
    segs, outside = B.scan_segments(sel)
    assert segs == [(470.0, 534.0), (823.0, 832.0), (863.0, 865.0)]
    assert outside == [(1785.0, 1800.0)]
    segs, _ = B.scan_segments([(823, 832), (833, 840)])        # 1 MHz apart: one range
    assert segs == [(823.0, 840.0)]
    t = B.estimate_seconds([(470, 800)])
    assert 20 < t < 50


@pytest.mark.parametrize('value,want', [
    ('', []), (None, []), ('xx', []), ('[[470, 534], [823, 832]]', [(470.0, 534.0), (823.0, 832.0)]),
    ('[[534, 470]]', []), ('[["a", 1]]', []), ('[[1]]', []), ('[[NaN, 5]]', []), ([[1, 2]], [(1.0, 2.0)]),
])
def test_B7_parse_ranges(value, want):
    assert B.parse_ranges(value) == want


# ------------------------------------------------------------------ scanning several ranges

RANGES = [(480.0, 500.0), (530.0, 545.0)]


def _air():
    a = sim.Air(noise=0.01, seed=5)
    a.tone(28.8e6 * 17, 0.003)          # own spur 489.6 (inside range 1)
    a.tone(490.5e6, 0.05).tone(515.0e6, 0.05).tone(540.0e6, 0.05)
    return a


def _multi(monkeypatch, tmpdir, clean=None):
    return sim.run_scan(monkeypatch, tmpdir, 480, 545, ranges=RANGES, clean=clean,
                        sdr_kwargs={'air': _air()})


def test_M1_only_the_chosen_ranges_are_measured(monkeypatch, tmpdir):
    scanner, sdr, _ = _multi(monkeypatch, tmpdir)
    f, y = sim.spectrum_arrays(scanner)
    assert scanner.segments == RANGES
    assert f.min() >= 480 and f.max() <= 545
    assert not ((f > 500.001) & (f < 529.999)).any(), 'points in the gap'
    for tone in (490.5, 540.0):
        i = int(np.argmin(np.abs(f - tone)))
        assert abs(f[i] - tone) < 0.03 and y[i] > np.median(y) + 15
    tuned = sorted(set(round(x / 1e6, 3) for x in sdr.tuned))
    assert not any(503 < x < 527 for x in tuned), 'tile tuned inside the gap'
    full = (545 - 480) / scanner.step_size + 1
    assert len(tuned) < 0.75 * full


def test_M2_tiles_on_one_grid(monkeypatch, tmpdir):
    scanner, sdr, _ = _multi(monkeypatch, tmpdir)
    step = int(round(scanner.step_size * 1e6))
    g0 = int(round(RANGES[0][0] * 1e6))
    for c in set(int(round(x)) for x in sdr.tuned):
        assert (c - g0) % step == 0, c
    assert scanner.config.scan_range == [480.0, 545.0]
    assert 'ranges' in scanner.range_text()


def test_M3_clean_scan_works_on_several_ranges(monkeypatch, tmpdir):
    scanner, _, _ = _multi(monkeypatch, tmpdir, clean=CLEAN)
    info = scanner.spectrum.cleanup_info
    assert info['ripple'] in ('scan', 'default')
    assert 489.6 in info['spurs_removed']


def test_M4_export_keeps_the_gap_empty(monkeypatch, tmpdir):
    from wwb_scanner.file_handlers.importers import BaseImporter
    from wwb_scanner.file_handlers import exporters as E
    scanner, _, _ = _multi(monkeypatch, tmpdir)
    sp = scanner.spectrum
    p = str(tmpdir.join('m.csv'))
    sp.export_to_file(filename=p, frequency_format='khz')
    rows = [l.split(',') for l in io.open(p).read().split('\n')]
    fk = np.array([int(r[0]) for r in rows])
    assert not ((fk > 500100) & (fk < 529900)).any(), 'CSV invents values in the gap'
    assert fk.min() <= 480100 and fk.max() >= 544900
    for ext in ('sdb2', 'sdb3'):
        q = str(tmpdir.join('m.' + ext))
        sp.export_to_file(filename=q)
        back = BaseImporter.import_file(q)
        fb = np.asarray(back.sample_data.frequency, float)
        yb = np.asarray(back.sample_data['dbFS'], float)
        gap = (fb > 501) & (fb < 529)
        assert gap.any() and np.all(yb[gap] <= E.NO_DATA_DB + 0.5), ext
        i = int(np.argmin(np.abs(fb - 540.0)))
        assert yb[i] > E.NO_DATA_DB + 40, ext


def test_M5_one_range_unchanged(monkeypatch, tmpdir):
    s1, sdr1, _ = sim.run_scan(monkeypatch, tmpdir, 480, 500, sdr_kwargs={'air': _air()})
    assert s1.segments == [(480.0, 500.0)]
    s2, sdr2, _ = sim.run_scan(monkeypatch, tmpdir.mkdir('b'), 480, 500, ranges=[(480, 500)],
                               sdr_kwargs={'air': _air()})
    assert sorted(set(sdr1.tuned)) == sorted(set(sdr2.tuned))
    f1, y1 = sim.spectrum_arrays(s1)
    f2, y2 = sim.spectrum_arrays(s2)
    assert np.array_equal(f1, f2)


def test_M6_bad_ranges_fall_back_to_one_range(monkeypatch, tmpdir):
    s, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 480, 490, ranges=None, sdr_kwargs={'air': _air()})
    s.config['scan_ranges'] = 'garbage'
    assert s.segments == [(480.0, 490.0)]


def test_G1_graph_shows_gaps():
    pytest.importorskip('PySide2')
    from wwb_scanner.ui.pyside import graph
    x = np.r_[np.arange(480, 500, 0.025), np.arange(530, 545, 0.025)]
    pts = np.zeros(x.size, dtype=graph.GRAPH_DTYPE)
    pts['x'] = x
    pts['y'] = -90
    out = graph.with_gaps(pts)
    assert out.size == pts.size + 2
    low = out[out['y'] < -200]
    assert 499.9 < low["x"][0] < low["x"][1] < 530.1
    one = pts[:500]
    assert graph.with_gaps(one).size == one.size


def test_U1_window_tables():
    pytest.importorskip('PySide2')
    from wwb_scanner.ui.pyside import bands_qt
    d = bands_qt.BandsData()
    assert len(d.countries()) == len(B.COUNTRIES)
    rows = d.systems('PL')
    assert len(rows) == len(B.SYSTEMS) and all(r['label'] for r in rows)
    s = d.summary('PL', json.dumps(['Shure|QLX-D|G51', 'Shure|PSM 300|S8']))
    assert s['count'] == 3 and json.loads(s['segments'])[0] == [470.0, 534.0]
    assert s['seconds'] < s['fullSeconds']
    assert d.summary('PL', 'not json')['count'] == 0
    assert d.regions('XX') == []
