"""V22.2: scans of several passes (audit 16.11.2026, item 7)."""
import numpy as np
import pytest

import sim
from test_v221 import store, Recorder, _raw  # noqa: F401  (fixture)


def test_passes_on_one_grid_and_views():
    from wwb_scanner.scan_objects.passes import ScanPasses
    p = ScanPasses()
    f = np.array([470.0, 470.025, 470.05])
    p.add(f, [-90., -60., -95.], time=1.)
    p.add(f + 0.0, [-92., -70., -80.], time=2.)
    p.add(np.array([470.025, 470.05, 470.075]), [-65., -85., -99.], time=3.)   # one point more
    assert len(p) == 3 and p.size == 4 and list(p.count()) == [2, 3, 3, 1]
    f_max, y_max = p.view('max_hold')
    assert np.allclose(f_max, [470.0, 470.025, 470.05, 470.075]) and np.allclose(y_max, [-90, -60, -80, -99])
    f_avg, y_avg = p.view('average')
    want = 10 * np.log10(np.mean(10 ** (np.array([-60., -70., -65.]) / 10)))
    assert np.isclose(y_avg[1], want) and np.isclose(y_avg[0], 10 * np.log10((1e-9 + 10 ** -9.2) / 2))
    f_last, y_last = p.view('last')
    assert np.allclose(y_last, [-92, -65, -85, -99])       # 470.0: the latest pass that has it
    with pytest.raises(ValueError):
        p.view('median')
    # float noise in the frequencies does not make new points
    p.add(f + 1e-10, [-91., -91., -91.])
    assert p.size == 4
    assert p.levels.dtype == np.float32


def test_passes_survive_an_npz_round_trip(tmpdir):
    from wwb_scanner.scan_objects.passes import ScanPasses
    p = ScanPasses()
    p.add([470., 470.025], [-90., -80.], time=5.)
    p.add([470., 470.025], [-85., -88.], time=None)
    path = str(tmpdir.join('p.npz'))
    np.savez_compressed(path, **p.to_arrays())
    with np.load(path) as z:
        q = ScanPasses.from_arrays(z)
    assert np.array_equal(q.levels, p.levels) and q.times == [5.0, None]
    np.savez(str(tmpdir.join('n.npz')), sample_data=np.zeros(1))
    with np.load(str(tmpdir.join('n.npz'))) as z:
        assert ScanPasses.from_arrays(z) is None


def test_spectrum_views_do_not_change_the_passes():
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.scan_objects.passes import ScanPasses
    p = ScanPasses()
    p.add([470., 470.025], [-90., -60.])
    p.add([470., 470.025], [-80., -70.])
    sp = Spectrum()
    sp.set_passes(p, 'max_hold')
    assert list(sp.sample_data['dbFS']) == [-80., -60.] and sp.pass_view == 'max_hold'
    before = p.levels.copy()
    sp.show_view('last')
    assert list(sp.sample_data['dbFS']) == [-80., -70.]
    assert np.array_equal(p.levels, before)
    with pytest.raises(ValueError):
        Spectrum().show_view('last')


def test_service_scans_several_passes_and_keeps_them(store):
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.utils import dbstore
    rec = Recorder(('pass_started', 'pass_finished', 'scan_finished'))
    try:
        svc = service.ScanService()
        svc.start(_raw(ScanConfig({'scan_range': [470., 474.]})), passes=3)
        res = svc.wait(120)
    finally:
        rec.close()
    assert res.ok and res.passes == 3 and res.view == 'max_hold' and res.points == 161
    assert [e['kind'] for e in rec.events] == ['pass_started', 'pass_finished'] * 3 + ['scan_finished']
    sp = res.spectrum
    assert len(sp.passes) == 3 and sp.passes.size == 161
    f, y = sp.passes.view('max_hold')
    assert np.allclose(sp.sample_data['dbFS'], y)
    # max-hold is at or above every pass
    assert np.all(sp.sample_data['dbFS'][None, :] >= sp.passes.levels - 1e-4)
    # the history keeps every pass; the index says how many
    assert list(dbstore.db_store.get_all_scans()) == [res.saved_eid]
    rec_idx = dbstore.db_store.get_all_scans()[res.saved_eid]
    assert rec_idx['passes'] == 3 and rec_idx['pass_view'] == 'max_hold'
    again = Spectrum.from_dbstore(eid=res.saved_eid)
    assert len(again.passes) == 3 and np.array_equal(again.passes.levels, sp.passes.levels)
    assert np.allclose(again.sample_data['dbFS'], sp.sample_data['dbFS'])


def test_export_of_a_multi_pass_scan_is_the_view(store, tmpdir):
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    from wwb_scanner.scan_objects import Spectrum
    svc = service.ScanService(history=None)
    svc.start(_raw(ScanConfig({'scan_range': [470., 472.]})), passes=2, view='average')
    res = svc.wait(60)
    out = str(tmpdir.join('two.csv'))
    res.spectrum.export_to_file(filename=out)
    back = Spectrum.import_from_file(out)
    assert back.sample_data.size == 81
    assert np.allclose(back.sample_data['dbFS'], np.round(res.spectrum.sample_data['dbFS'], 1), atol=0.051)


def test_stop_ends_after_the_running_pass(store):
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    svc = service.ScanService(history=None)
    svc.start(_raw(ScanConfig({'scan_range': [470., 520.]})), passes=50)
    svc.stop()
    res = svc.wait(120)
    assert res.stopped_by_user and res.passes <= 1


def test_passes_only_for_ordinary_scans():
    from wwb_scanner import service
    from wwb_scanner.scanner.config import ScanConfig
    svc = service.ScanService(history=None)
    with pytest.raises(service.ScanError):
        svc.start(ScanConfig(), kind=service.FLOOR_CALIBRATION, passes=2)
    with pytest.raises(service.ScanError):
        svc.start(ScanConfig(), passes=0)
    with pytest.raises(service.ScanError):
        svc.start(ScanConfig(), passes=2, view='median')
