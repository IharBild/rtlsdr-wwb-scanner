"""V22.13: the rest of the external audit of V22.8 (AUD-11, 16, 20, 21, 22,
25, 26 = P3-16), item 4 of the DLL audit of 12.11, and what the checks on the
PC of 23.11 found (.npz lost the time of the scan; sdb3 header error without
the file name)."""
import datetime
import logging
import os

import numpy as np
import pytest

from wwb_scanner.file_handlers import importers
from wwb_scanner.scan_objects import Spectrum
from wwb_scanner.scan_objects.samplearray import SampleArray
from wwb_scanner.scan_objects.passes import ScanPasses

WHEN = datetime.datetime(2026, 11, 23, 15, 46, 1)


def _spectrum(n=200, start=470.):
    sp = Spectrum(step_size=0.025, timestamp_utc=WHEN.timestamp())
    f = start + np.arange(n) * 0.025
    sp.sample_data = SampleArray.create(frequency=f, dbFS=-95. + np.sin(np.arange(n)))
    return sp


# ---------------------------------------------------------------- .npz (PC 23.11)

def test_npz_keeps_the_time_of_the_scan(tmpdir):
    fn = str(tmpdir.join('a.npz'))
    _spectrum().export_to_file(filename=fn)
    back = importers.BaseImporter.import_file(fn)
    assert back.timestamp_utc == pytest.approx(WHEN.timestamp())


def test_npz_without_time_gets_the_time_of_the_import(tmpdir):
    sp = _spectrum()
    fn = str(tmpdir.join('old.npz'))
    np.savez(fn, sample_data=sp.sample_data.data)
    back = importers.BaseImporter.import_file(fn)
    assert abs(back.timestamp_utc - datetime.datetime.now().timestamp()) < 120


# ---------------------------------------------------------------- AUD-21 weights

def _weighted():
    a = SampleArray.create(frequency=np.array([470., 470.025, 470.05]), dbFS=np.array([-90., -91., -92.]))
    a._counts = np.array([1., 0.01, 1e-3])
    return a


def test_weights_survive_json():
    from wwb_scanner.utils import numpyjson
    a = _weighted()
    b = numpyjson.loads(numpyjson.dumps(a))
    assert np.allclose(b._get_counts(), [1., 0.01, 1e-3])
    plain = SampleArray.create(frequency=np.array([1., 2.]), dbFS=np.array([0., 0.]))
    assert 'counts' not in plain._serialize()


def test_weights_kept_by_unsorted_append():
    a = SampleArray(keep_sorted=False)
    a.append(_weighted())
    a.append(SampleArray.create(frequency=np.array([480.]), dbFS=np.array([-80.])))
    assert np.allclose(a._get_counts(), [1., 0.01, 1e-3, 1.])


def test_weights_kept_in_npz_and_history(monkeypatch, tmpdir):
    sp = _spectrum(n=3)
    sp.sample_data._counts = np.array([1., 0.01, 1e-3])
    fn = str(tmpdir.join('w.npz'))
    sp.export_to_file(filename=fn)
    assert np.allclose(importers.BaseImporter.import_file(fn).sample_data._get_counts(), [1., 0.01, 1e-3])
    from wwb_scanner.utils import dbstore
    monkeypatch.setenv('WWB_SCANNER_DATA', str(tmpdir.join('data')))
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', None)
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', None)
    store = dbstore.DBStore()
    monkeypatch.setattr(dbstore, 'db_store', store)
    eid = sp.save_to_dbstore(store)
    back = Spectrum.from_dbstore(eid=eid)
    assert np.allclose(back.sample_data._get_counts(), [1., 0.01, 1e-3])


def test_wrong_weights_are_ignored():
    a = _weighted()
    assert not a.set_counts([1., 2.])
    assert not a.set_counts([1., -1., 1.])
    assert np.allclose(a._get_counts(), [1., 0.01, 1e-3])


# ---------------------------------------------------------------- AUD-22 passes

def test_empty_passes_round_trip():
    p = ScanPasses()
    q = ScanPasses.from_arrays(p.to_arrays())
    assert q is not None and len(q) == 0 and q.size == 0


def test_passes_without_times():
    q = ScanPasses.from_arrays({'pass_freqs': np.arange(3.), 'pass_levels': np.zeros((2, 3))})
    assert len(q) == 2 and q.times == [None, None]


# ---------------------------------------------------------------- AUD-16 damaged history

@pytest.mark.parametrize('text', [
    '{"scans_performed": {"1": {"x": {"__npy__": "!!!not base64"}}}}',
    '{"scans_performed": {"1": {"x": {"__ndarray__": "gARnYXJiYWdl"}}}}',
    '{"scans_performed": [1, 2',
])
def test_damaged_history_index_is_kept_and_a_new_one_started(tmpdir, text):
    from wwb_scanner.utils import dbstore
    p = tmpdir.join('scan_db.json')
    p.write(text)
    db = dbstore.JsonDB(str(p))
    assert db._read() == {}
    assert [n for n in os.listdir(str(tmpdir)) if '.broken-' in n]


def test_unreadable_history_is_not_replaced(tmpdir, monkeypatch):
    """A file that can not be read now (in use, no access) is not damaged:
    no copy, no empty history - the error goes up."""
    from wwb_scanner.utils import dbstore
    p = tmpdir.join('scan_db.json')
    p.write('{"scans_performed": {}}')
    db = dbstore.JsonDB(str(p))
    real = open

    def locked(path, *a, **k):
        if str(path) == str(p):
            raise PermissionError('in use')
        return real(path, *a, **k)
    monkeypatch.setattr('builtins.open', locked)
    with pytest.raises(PermissionError):
        db._read()
    monkeypatch.undo()
    assert not [n for n in os.listdir(str(tmpdir)) if '.broken-' in n]


# ---------------------------------------------------------------- AUD-20 keep ratio

def test_sensitivity_bands_use_the_keep_ratio_of_the_settings():
    from wwb_scanner.scanner import sample_processing as spm
    from wwb_scanner.scanner.config import ScanConfig

    class Sc(object):
        sample_rate = 2.4e6
    for ratio in (0.39, 0.45):
        sc = Sc()
        sc.sampling_config = ScanConfig({'sampling': {'tile_keep_ratio': ratio}}).sampling
        coll = spm.SampleCollection.__new__(spm.SampleCollection)
        coll.scanner = sc
        assert coll._keep_ratio() == pytest.approx(ratio)
    src = open(spm.__file__, encoding='utf-8').read()
    assert 'half = 0.39 * fs' not in src
    assert src.count('DEFAULT_TILE_KEEP_RATIO * ') == 0


# ---------------------------------------------------------------- AUD-11/25/26 graph

@pytest.fixture
def graph():
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from wwb_scanner.ui.pyside import graph as g
    return g


def _arr(g, n, step=0.025):
    a = np.zeros(n, dtype=g.GRAPH_DTYPE)
    a['x'] = 470. + np.arange(n) * step
    a['y'] = -100.
    return a


def test_15001_points_give_15000_chart_points(graph):
    g = graph
    assert g.decimate_max(_arr(g, 15000)).size == 15000
    assert g.decimate_max(_arr(g, 15001)).size == 15000      # was 7501
    assert g.decimate_max(_arr(g, 30000)).size == 15000
    assert g.decimate_max(_arr(g, 70640)).size == 15000


def test_one_missing_tile_is_drawn_as_a_hole(graph):
    g = graph
    a = _arr(g, 2000)
    keep = (a['x'] < 490.) | (a['x'] >= 490.775)              # one tile of 31 points missing
    a = a[keep]
    k = g.group_size_for(2000)
    pts = g.with_gaps(g.decimate_max(a, k), g.gap_limit(0.025, k))
    assert int((pts['y'] < -200).sum()) == 2
    # without the hole: no false one
    b = _arr(g, 2000)
    assert int((g.with_gaps(g.decimate_max(b, k), g.gap_limit(0.025, k))['y'] < -200).sum()) == 0


def test_no_false_holes_after_decimation(graph):
    g = graph
    a = _arr(g, 70640)
    rng = np.random.default_rng(1)
    a['y'] = -100. + rng.normal(0, 3, a.size)
    k = g.group_size_for(a.size)
    pts = g.with_gaps(g.decimate_max(a, k), g.gap_limit(0.025, k))
    assert int((pts['y'] < -200).sum()) == 0


def test_group_size_from_the_real_point_spacing(graph):
    """Window 1024 at 2.4 MS/s: points every 2.34 kHz, ten times more than
    the 25 kHz the planned size assumed (17 056 chart points before)."""
    g = graph
    gd = g.SpectrumGraphData()
    gd._decim_span = 40.
    gd._decim_k = g.group_size_for(40. / 0.025)                # 1600 points planned -> 1
    gd._decim_checked = False
    step = 2.4 / 1024
    gd.xy_data = _arr(g, int(40. / step), step)
    assert gd._point_step() == pytest.approx(step)
    gd._set_series_from_data()
    assert gd._decim_k == pytest.approx(40. / step / g.MAX_GRAPH_POINTS, rel=0.01)
    assert g.decimate_max(gd.xy_data, gd._decim_k).size <= g.MAX_GRAPH_POINTS


# ---------------------------------------------------------------- DLL audit, item 4

def test_stock_library_is_named_in_the_window_once(caplog):
    from wwb_scanner import scan_dll
    scan_dll._cache.pop('stock_warned', None)
    try:
        with caplog.at_level(logging.WARNING, logger='wwb_scanner.status'):
            assert scan_dll.warn_stock_once('win32') is True
            assert scan_dll.warn_stock_once('win32') is False
        assert caplog.text.count('not the scanner build') == 1
        scan_dll._cache.pop('stock_warned', None)
        assert scan_dll.warn_stock_once('linux') is False
    finally:
        scan_dll._cache.pop('stock_warned', None)


# ---------------------------------------------------------------- sdb3 header

@pytest.mark.parametrize('data,what', [(b'//@ShureScan\n{"BitWidth":16 @Binary:xx', 'header'),
                                       (b'//@ShureScan\n{}\n', 'no data part'),
                                       (b'//@ShureScan\n[1]\n@Binary:', 'header')])
def test_damaged_sdb3_header_names_the_file(tmpdir, data, what):
    p = tmpdir.join('broken.sdb3')
    p.write_binary(data)
    with pytest.raises(ValueError) as e:
        importers.BaseImporter.import_file(str(p))
    assert 'broken.sdb3' in str(e.value) and what in str(e.value)
