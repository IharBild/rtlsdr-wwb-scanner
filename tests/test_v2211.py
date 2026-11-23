"""V22.11: hardening after the external audits (plan of 22.11.2026, item 5).

P2-NPZ1 (npz with raw values and passes), P2-RES1 (device names),
P2-CSV1 (limits of an imported file), P3-11 (CRC of sdb3), P3-19 (missing
points file of the history), AUD-15 (res_bandwidth), the date of a scan in
sdb3 / WSM export and import, atomic export, the CI and the portable build.
"""
import datetime
import logging
import os
import re

import numpy as np
import pytest

from wwb_scanner.file_handlers import exporters, importers
from wwb_scanner.scan_objects import Spectrum
from wwb_scanner.scan_objects.samplearray import SampleArray

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WWB_SDB3 = os.path.join(HERE, 'reference', 'wwb79_sdb3_from_our_sdb2.sdb3')


def _spectrum(n=41, when=None, start=470.0):
    when = when or datetime.datetime(2026, 5, 3, 21, 15, 7)
    sp_ = Spectrum(step_size=0.025, timestamp_utc=when.timestamp())
    f = start + np.arange(n) * 0.025
    y = -95. + np.sin(np.arange(n)) * 3.
    sp_.sample_data = SampleArray.create(frequency=f, dbFS=y)
    return sp_


def _import(path):
    return importers.BaseImporter.import_file(str(path))


# ---------------------------------------------------------------- P2-NPZ1

def test_npz_export_and_import_keep_raw_values_and_passes(tmpdir):
    from wwb_scanner.scan_objects.passes import ScanPasses
    sp_ = _spectrum()
    f = np.asarray(sp_.sample_data['frequency'])
    sp_.raw_dbFS = np.asarray(sp_.sample_data['dbFS']) + 1.5
    sp_.passes = ScanPasses(f, np.vstack([np.full(f.size, -96.), np.full(f.size, -94.)]), [10., None])
    fn = str(tmpdir.join('scan.npz'))
    sp_.export_to_file(filename=fn)
    back = _import(fn)
    assert back.sample_data.size == f.size
    assert np.allclose(back.raw_dbFS, sp_.raw_dbFS)
    assert back.passes is not None and len(back.passes) == 2
    assert back.passes.times == [10., None]
    assert np.allclose(back.passes.levels[1], -94.)


def test_npz_with_raw_values_of_another_size_imports_without_them(tmpdir, caplog):
    sp_ = _spectrum()
    fn = str(tmpdir.join('scan.npz'))
    np.savez(fn, sample_data=sp_.sample_data, raw_dbFS=np.zeros(3))
    with caplog.at_level(logging.WARNING):
        back = _import(fn)
    assert back.sample_data.size == sp_.sample_data.size
    assert getattr(back, 'raw_dbFS', None) is None
    assert 'not used' in caplog.text


def test_npz_with_damaged_passes_imports_the_points(tmpdir, caplog):
    sp_ = _spectrum()
    fn = str(tmpdir.join('scan.npz'))
    np.savez(fn, sample_data=sp_.sample_data, pass_freqs=np.arange(5.),
             pass_levels=np.zeros((2, 3)), pass_times=np.zeros(2))
    with caplog.at_level(logging.WARNING):
        back = _import(fn)
    assert back.sample_data.size == sp_.sample_data.size
    assert back.passes is None
    assert 'passes' in caplog.text


def test_old_npz_with_points_only_still_imports(tmpdir):
    sp_ = _spectrum()
    fn = str(tmpdir.join('old.npz'))
    np.savez(fn, sample_data=sp_.sample_data)
    back = _import(fn)
    assert back.sample_data.size == sp_.sample_data.size and back.passes is None


# ---------------------------------------------------------------- P2-RES1

@pytest.mark.parametrize('name', ['NUL.csv', 'nul.sdb2', 'CON.sdb3', 'com1.npz', 'LPT9.csv',
                                  'aux.tar.csv', 'PRN .csv'])
def test_device_names_are_refused(tmpdir, name):
    assert exporters.reserved_name(name)
    with pytest.raises(ValueError, match='Windows keeps for a device'):
        _spectrum().export_to_file(filename=str(tmpdir.join(name)), frequency_format='khz')


@pytest.mark.parametrize('name', ['null.csv', 'console.csv', 'COM10.csv', 'scan NUL.csv', 'LPT.sdb2'])
def test_ordinary_names_are_not_device_names(name):
    assert not exporters.reserved_name(name)


# ---------------------------------------------------------------- atomic export

def test_a_failed_export_keeps_the_old_file_and_leaves_no_part(tmpdir, monkeypatch):
    fn = str(tmpdir.join('scan.csv'))
    _spectrum().export_to_file(filename=fn, frequency_format='khz')
    old = open(fn, 'rb').read()

    def broken(*a, **k):
        raise OSError('disk full')
    from wwb_scanner.utils import dbstore
    monkeypatch.setattr(dbstore.os, 'replace', broken)
    with pytest.raises(OSError):
        _spectrum(n=80).export_to_file(filename=fn, frequency_format='khz')
    assert open(fn, 'rb').read() == old
    assert [p for p in os.listdir(str(tmpdir)) if p.endswith('.part')] == []


@pytest.mark.parametrize('ext,fmt', [('csv', 'khz'), ('csv', 'wsm'), ('csv', None), ('sdb2', None),
                                     ('sdb3', None), ('npz', None)])
def test_every_format_is_written_whole(tmpdir, ext, fmt):
    fn = str(tmpdir.join('scan.' + ext))
    _spectrum().export_to_file(filename=fn, frequency_format=fmt)
    assert os.path.getsize(fn) > 0
    assert os.listdir(str(tmpdir)) == ['scan.' + ext]
    assert _import(fn).sample_data.size > 0


def test_export_waits_for_the_points_to_stop_changing(tmpdir):
    import threading
    sp_ = _spectrum()
    seen = []
    real = exporters.CSVExporter.build_data

    def spy(self):
        seen.append(sp_.data_update_lock._is_owned())
        return real(self)
    exporters.CSVExporter.build_data = spy
    try:
        sp_.export_to_file(filename=str(tmpdir.join('a.csv')), frequency_format='khz')
    finally:
        exporters.CSVExporter.build_data = real
    assert seen and all(seen)
    assert isinstance(sp_.data_update_lock, type(threading.RLock()))


# ---------------------------------------------------------------- date of the scan

WHEN = datetime.datetime(2026, 5, 3, 21, 15, 7)


def test_sdb3_export_carries_the_time_of_the_scan(tmpdir):
    fn = str(tmpdir.join('t.sdb3'))
    _spectrum(when=WHEN).export_to_file(filename=fn)
    head = open(fn, 'rb').read().split(b'@Binary:')[0].decode('utf-8')
    assert '"StartDate" : "05/03/2026"' in head and '"StartTime" : "21:15:07"' in head
    back = _import(fn)
    assert back.timestamp_utc == pytest.approx(WHEN.timestamp())


def test_wsm_export_carries_the_time_of_the_scan(tmpdir):
    fn = str(tmpdir.join('t.csv'))
    _spectrum(when=WHEN).export_to_file(filename=fn, frequency_format='wsm')
    text = open(fn, encoding='utf-8').read()
    assert 'Date/Time;03-05-2026 09-15-07 PM' in text
    back = _import(fn)
    assert back.timestamp_utc == pytest.approx(WHEN.timestamp())


@pytest.mark.parametrize('text,want', [
    ('08-11-2026 02-44-57 PM', datetime.datetime(2026, 11, 8, 14, 44, 57)),
    ('08-11-2026 12-05-00 AM', datetime.datetime(2026, 11, 8, 0, 5, 0)),
    ('08-11-2026 12-05-00 PM', datetime.datetime(2026, 11, 8, 12, 5, 0)),
    ('08-11-2026 23-05-00', datetime.datetime(2026, 11, 8, 23, 5, 0)),
    ('not a date', None)])
def test_wsm_time_is_read(text, want):
    assert importers.parse_wsm_time(text) == want


def test_sdb2_of_wwb_without_epoch_is_local_time(tmpdir):
    fn = str(tmpdir.join('t.sdb2'))
    _spectrum(when=WHEN).export_to_file(filename=fn)
    text = open(fn, encoding='utf-8').read()
    text = re.sub(r' date_time="\d+"', '', text)        # as in a file of WWB 7.9
    open(fn, 'w', encoding='utf-8').write(text)
    assert _import(fn).timestamp_utc == pytest.approx(WHEN.timestamp())


# ---------------------------------------------------------------- P3-11

def test_crc_of_the_wwb_file_is_right(caplog):
    with caplog.at_level(logging.WARNING, logger='wwb_scanner.status'):
        imp = importers.WWB3Importer(filename=WWB_SDB3)
        imp()
    assert imp.bad_crc == 0
    assert 'check sum' not in caplog.text


def test_wrong_crc_is_reported_and_the_sweep_still_read(tmpdir, caplog):
    raw = bytearray(open(WWB_SDB3, 'rb').read())
    i = raw.index(b'@Swp') + 12 + 10            # a value inside the sweep
    raw[i] ^= 0x01
    fn = str(tmpdir.join('bad.sdb3'))
    open(fn, 'wb').write(bytes(raw))
    with caplog.at_level(logging.WARNING, logger='wwb_scanner.status'):
        imp = importers.WWB3Importer(filename=fn)
        sp_ = imp()
    assert imp.bad_crc == 1
    assert 'check sum wrong in 1 of 1 sweeps' in caplog.text
    assert sp_.sample_data.size > 0


def test_our_sdb3_has_a_right_crc(tmpdir, caplog):
    fn = str(tmpdir.join('ours.sdb3'))
    _spectrum().export_to_file(filename=fn)
    imp = importers.WWB3Importer(filename=fn)
    imp()
    assert imp.bad_crc == 0


# ---------------------------------------------------------------- P2-CSV1

def test_csv_with_too_many_points_is_refused(tmpdir, monkeypatch):
    monkeypatch.setattr(importers, 'MAX_IMPORT_POINTS', 50)
    fn = str(tmpdir.join('big.csv'))
    open(fn, 'w').write('\n'.join('%d,-90.0' % (470000 + 25 * i) for i in range(60)))
    with pytest.raises(ValueError, match='too many points'):
        _import(fn)
    open(fn, 'w').write('\n'.join('%d,-90.0' % (470000 + 25 * i) for i in range(50)))
    assert _import(fn).sample_data.size == 50


def test_wsm_with_too_many_points_is_refused(tmpdir, monkeypatch):
    fn = str(tmpdir.join('w.csv'))
    _spectrum(n=200).export_to_file(filename=fn, frequency_format='wsm')
    monkeypatch.setattr(importers, 'MAX_IMPORT_POINTS', 50)
    with pytest.raises(ValueError, match='too many points'):
        _import(fn)


@pytest.mark.parametrize('ext', ['csv', 'sdb2', 'sdb3'])
def test_a_file_too_large_is_refused_before_it_is_read(tmpdir, monkeypatch, ext):
    fn = str(tmpdir.join('s.' + ext))
    _spectrum().export_to_file(filename=fn, frequency_format='khz' if ext == 'csv' else None)
    monkeypatch.setattr(importers, 'MAX_IMPORT_BYTES', 100)
    opened = []
    real = open

    def spy(*a, **k):
        opened.append(a[0])
        return real(*a, **k)
    monkeypatch.setattr(importers, 'open', spy, raising=False)
    with pytest.raises(ValueError, match='too large'):
        _import(fn)
    assert opened == []


# ---------------------------------------------------------------- P3-19

def _store(monkeypatch, tmpdir):
    from wwb_scanner.utils import dbstore
    monkeypatch.setenv('WWB_SCANNER_DATA', str(tmpdir.join('data')))
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', None)
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', None)
    store = dbstore.DBStore()
    monkeypatch.setattr(dbstore, 'db_store', store)
    return store


@pytest.mark.parametrize('damage', ['missing', 'not zip', 'no points'])
def test_history_record_without_its_points_file_is_a_clear_error(monkeypatch, tmpdir, damage, caplog):
    store = _store(monkeypatch, tmpdir)
    sp_ = _spectrum()
    eid = sp_.save_to_dbstore(store)
    name = store.get_all_scans()[eid]['sample_data_file']
    path = os.path.join(store.scans_dir, name)
    if damage == 'missing':
        os.remove(path)
    elif damage == 'not zip':
        open(path, 'wb').write(b'garbage')
    else:
        np.savez(path[:-len('.npz')], other=np.zeros(3))
    with caplog.at_level(logging.ERROR):
        rec = store.get_scan(eid)
    assert rec is not None and rec.get('sample_data') is None
    assert name in rec['points_error']
    with pytest.raises(ValueError, match='can not be opened'):
        Spectrum.from_dbstore(eid=eid)
    # the other scans of the history are not affected
    eid2 = _spectrum(start=480.).save_to_dbstore(store)
    assert Spectrum.from_dbstore(eid=eid2).sample_data.size == 41


def test_history_points_file_is_size_checked(monkeypatch, tmpdir):
    from wwb_scanner.utils import numpyjson
    store = _store(monkeypatch, tmpdir)
    eid = _spectrum().save_to_dbstore(store)
    monkeypatch.setattr(numpyjson, 'MAX_ARRAY_ITEMS', 10)
    rec = store.get_scan(eid)
    assert 'too large' in rec['points_error']


def test_history_keeps_raw_values_and_passes(monkeypatch, tmpdir):
    from wwb_scanner.scan_objects.passes import ScanPasses
    store = _store(monkeypatch, tmpdir)
    sp_ = _spectrum()
    f = np.asarray(sp_.sample_data['frequency'])
    sp_.raw_dbFS = np.asarray(sp_.sample_data['dbFS']) - 2.
    sp_.passes = ScanPasses(f, np.full((3, f.size), -90.), [1., 2., 3.])
    eid = sp_.save_to_dbstore(store)
    back = Spectrum.from_dbstore(eid=eid)
    assert np.allclose(back.raw_dbFS, sp_.raw_dbFS) and len(back.passes) == 3


# ---------------------------------------------------------------- AUD-15

def test_sdb2_res_bandwidth_is_a_number(tmpdir):
    fn = str(tmpdir.join('t.sdb2'))
    _spectrum().export_to_file(filename=fn)
    text = open(fn, encoding='utf-8').read()
    assert 'TODO' not in text
    assert float(re.search(r'res_bandwidth="([^"]*)"', text).group(1)) == 0.


# ---------------------------------------------------------------- CI and the portable build

def test_pytest_collects_only_test_files():
    p = os.path.join(ROOT, 'pytest.ini')
    if not os.path.exists(p):
        pytest.skip('no pytest.ini in this copy (qa-kit)')
    ini = open(p, encoding='utf-8').read()
    assert re.search(r'^python_files = test_\*\.py$', ini, re.M)


def test_old_upstream_workflows_are_gone():
    wf = os.path.join(ROOT, '.github', 'workflows')
    if not os.path.isdir(wf):
        pytest.skip('no .github in this copy')
    names = set(os.listdir(wf))
    assert 'ci-tests.yml' not in names and 'packaging-tests.yml' not in names
    assert {'v4-fixes.yml', 'portable.yml'} <= names


def test_portable_readme_names_the_license_and_the_source():
    p = os.path.join(ROOT, 'tools', 'portable', 'README_PORTABLE.txt')
    if not os.path.exists(p):
        pytest.skip('no tools/portable in this copy')
    text = open(p, encoding='utf-8').read()
    assert 'GNU General Public License, version 2' in text
    assert 'https://github.com/IharBild/rtlsdr-wwb-scanner' in text
    assert 'SHA-256' in text and 'GPL' in text.split('-' * 72)[1]


def test_portable_build_writes_license_and_sha256():
    p = os.path.join(ROOT, 'tools', 'portable', 'build.py')
    if not os.path.exists(p):
        pytest.skip('no tools/portable in this copy')
    src = open(p, encoding='utf-8').read()
    assert "'LICENSE'" in src and ".sha256'" in src
    import importlib.util
    spec = importlib.util.spec_from_file_location('portable_build', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import hashlib
    assert mod.sha256_of(p) == hashlib.sha256(open(p, 'rb').read()).hexdigest()
    wf = os.path.join(ROOT, '.github', 'workflows', 'portable.yml')
    if os.path.exists(wf):                       # not in the qa-kit
        wf = open(wf, encoding='utf-8').read()
        assert 'portable.zip.sha256' in wf and 'SHA-256' in wf


# ---------------------------------------------------------------- qa_hw H9 / H15 texts

def _qa_hw():
    import importlib.util
    p = os.path.join(ROOT, 'tools', 'qa_hw.py')
    if not os.path.exists(p):
        pytest.skip('no tools in this copy')
    import sys
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    try:
        spec = importlib.util.spec_from_file_location('qa_hw_v2211', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def test_h15_names_an_incomplete_scan():
    q = _qa_hw()
    assert q.h15_note(0, 321, {}) == 'закрыт до первой плитки, 0 точек'
    assert q.h15_note(321, 321, {}) == 'скан до конца'
    t = q.h15_note(162, 321, {'aborted': True, 'reason': '', 'tiles_done': 6, 'tiles_total': 11})
    assert t == 'скан неполный: точек 162 из 321, плиток 6 из 11 (чтение прервано)'


def test_h9_needs_fewer_quiet_points():
    q = _qa_hw()
    assert q.H9_MIN_POINTS == 10
    src = open(os.path.join(ROOT, 'tools', 'qa_hw.py'), encoding='utf-8').read()
    assert "мало точек (%d)" in src


# ---------------------------------------------------------------- core

def test_unknown_setting_is_named_retired_one_is_not(caplog):
    from wwb_scanner.scanner import config
    with caplog.at_level(logging.DEBUG, logger='wwb_scanner.utils.config'):
        c = config.ScanConfig({'scan_range': [470., 480.], 'save_raw_values': False,
                               'sampling': {'fft_size': 1024, 'sampel_rate': 2e6}})
    assert c.unknown_keys == [] and c.sampling.unknown_keys == ['sampel_rate']
    warn = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warn) == 1 and "'sampel_rate'" in warn[0].getMessage()
    assert 'fft_size' not in warn[0].getMessage().split('Known')[0]
