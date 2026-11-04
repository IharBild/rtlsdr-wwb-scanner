"""Integration: parts of the program working together (scanner + clean scan +
history + export/import + calibration), and the simulator against the real
pyrtlsdr interface."""
import ast
import os

import numpy as np
import pytest

import sim
from wwb_scanner.scanner import cleanup as C

CLEAN = {'clean_ripple': True, 'clean_spurs': True, 'clean_floor': True}


def _air():
    a = sim.Air(noise=0.01, seed=3)
    for k in (17, 18, 19):
        a.tone(28.8e6 * k, 0.003)            # own spurs 489.6 518.4 547.2
    a.tone(510.3e6, 0.05).tone(530.0e6, 1.6)  # carrier and an overloading IEM
    return a


def test_I1_clean_scan_export_import_roundtrip(monkeypatch, tmpdir):
    from wwb_scanner.file_handlers.importers import BaseImporter
    scanner, _, _ = sim.run_scan(monkeypatch, tmpdir, 480, 560, gain=29.7, auto_gain=True, clean=CLEAN,
                                 sdr_kwargs={'air': _air(), 'gain_affects': True})
    sp = scanner.spectrum
    f, y = sim.spectrum_arrays(scanner)
    assert 518.4 in sp.cleanup_info['spurs_removed']
    for ext in ('csv', 'sdb2', 'sdb3'):
        p = str(tmpdir.join('clean.' + ext))
        kw = {'frequency_format': 'khz'} if ext == 'csv' else {}
        sp.export_to_file(filename=p, **kw)
        back = BaseImporter.import_file(p)
        fb = np.asarray(back.sample_data.frequency, float)
        yb = np.asarray(back.sample_data['dbFS'], float)
        i = int(np.argmin(np.abs(fb - 518.4)))
        j = int(np.argmin(np.abs(f - 518.4)))
        # the file has the cleaned value (not the spur) within the file's rounding
        assert abs(yb[i] - y[j]) <= 1.01, (ext, yb[i], y[j])
        k = int(np.argmin(np.abs(fb - 510.3)))
        assert yb[k] > np.median(yb) + 20, ext                 # real carrier kept


def test_I2_history_keeps_clean_and_raw(monkeypatch, tmpdir):
    from wwb_scanner.utils.dbstore import db_store
    from wwb_scanner.scan_objects import Spectrum
    scanner, _, _ = sim.run_scan(monkeypatch, tmpdir, 500, 530, clean=CLEAN,
                                 sdr_kwargs={'air': _air()})
    f, y = sim.spectrum_arrays(scanner)
    eid = scanner.spectrum.eid
    assert eid is not None
    sp2 = Spectrum.from_dbstore(eid=eid)
    y2 = np.asarray(sp2.sample_data['dbFS'], float)
    assert y2.size == y.size and np.allclose(y2, y)
    rec = db_store.get_scan(eid)
    with np.load(os.path.join(db_store.scans_dir, rec['sample_data_file'])) as z:
        raw = z['raw_dbFS']
    i = int(np.argmin(np.abs(f - 518.4)))
    assert raw[i] - y[i] > 3                                   # the spur is in the raw values only


def test_I3_calibration_then_levelled_scan(monkeypatch, tmpdir):
    """Calibration from a scan without antenna is found by the next scan
    with the same gain and sample rate, and only by it."""
    noant = sim.Air(noise=0.01, seed=4)
    for k in range(17, 20):
        noant.tone(28.8e6 * k, 0.003)
    cal, _, _ = sim.run_scan(monkeypatch, tmpdir, 400, 720, gain=29.7, sdr_kwargs={'air': noant})
    f, y = sim.spectrum_arrays(cal)
    meta = dict(gain=float(cal.gain), sample_rate=float(cal.sample_rate), step_mhz=float(cal.step_size))
    ok, text = C.calibrate(f, y, 400.0, cal.step_size, meta)
    assert ok, text
    s1, _, _ = sim.run_scan(monkeypatch, tmpdir, 500, 530, gain=29.7, clean=CLEAN, sdr_kwargs={'air': _air()})
    assert s1.spectrum.cleanup_info['flatten'], s1.spectrum.cleanup_info
    s2, _, _ = sim.run_scan(monkeypatch, tmpdir, 500, 530, gain=38.6, clean=CLEAN, sdr_kwargs={'air': _air()})
    assert not s2.spectrum.cleanup_info['flatten']
    assert 'gain 38.6' in s2.spectrum.cleanup_info['floor_skipped']


def test_I4_autogain_with_cleanup_level(monkeypatch, tmpdir):
    ref, _, _ = sim.run_scan(monkeypatch, tmpdir, 520, 540, gain=29.7,
                             sdr_kwargs={'air': sim.Air(noise=0.01).tone(530e6, 0.1), 'gain_affects': True})
    fr, yr = sim.spectrum_arrays(ref)
    want = yr[np.abs(fr - 530) <= 0.05].max() + 20 * np.log10(16)
    s, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 520, 540, gain=29.7, auto_gain=True, clean=CLEAN,
                             sdr_kwargs={'air': sim.Air(noise=0.01).tone(530e6, 1.6), 'gain_affects': True})
    f, y = sim.spectrum_arrays(s)
    assert abs(y[np.abs(f - 530) <= 0.05].max() - want) < 1.0
    assert sdr.gain == 29.7


def _real_rtlsdr_source():
    try:
        from importlib.machinery import PathFinder
        spec = PathFinder.find_spec('rtlsdr')      # on disk, not the test stub
    except Exception:
        spec = None
    if spec is None or not spec.submodule_search_locations:
        return None
    p = os.path.join(list(spec.submodule_search_locations)[0], 'rtlsdr.py')
    return p if os.path.exists(p) else None


def test_I5_simulator_matches_real_pyrtlsdr_interface():
    """The simulator must offer what the real pyrtlsdr RtlSdr offers and the
    scanner uses (otherwise tests pass on a fake that the dongle does not have)."""
    src = _real_rtlsdr_source()
    if src is None:
        pytest.skip('pyrtlsdr is not installed here')
    tree = ast.parse(open(src, encoding='utf-8').read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in ('BaseRtlSdr', 'RtlSdr'):
            for b in node.body:
                if isinstance(b, ast.FunctionDef):
                    names.add(b.name)
                elif isinstance(b, ast.Assign):
                    names.update(t.id for t in b.targets if isinstance(t, ast.Name))
    used = ['set_center_freq', 'read_samples', 'get_gains', 'close', 'gain', 'sample_rate', 'center_freq']
    missing_real = [u for u in used if u not in names]
    assert not missing_real, 'pyrtlsdr has no %s (%s)' % (missing_real, src)
    fake = sim.FakeRtlSdr()
    missing_fake = [u for u in used if not hasattr(fake, u)]
    assert not missing_fake
