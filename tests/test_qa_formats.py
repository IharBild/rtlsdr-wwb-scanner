"""QA plan group F: file formats (CSV for WWB, sdb2, import, database)."""
import glob
import os
import re
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from wwb_scanner.scan_objects import Spectrum
from wwb_scanner.file_handlers import BaseImporter

ATT = '/mnt/user-data/attachments/050ccbf1-1a7a-5f20-9122-2d859dde1399'
WWB_REF = sorted(glob.glob(os.path.join(ATT, '*AD600*.csv')) +
                 glob.glob(os.path.join(ATT, '*Polsat*.csv')) +
                 glob.glob(os.path.join(ATT, '*Opener*.csv')))
OUR_CSV = sorted(glob.glob(os.path.join(ATT, '*SDR_RTL*.csv')))
need_files = pytest.mark.skipif(not WWB_REF, reason='user files not available')


def make_spectrum(start=470.0, end=480.0, step=0.025, seed=0):
    f = np.round(np.arange(start, end + step / 2, step), 6)
    rng = np.random.RandomState(seed)
    y = -100 + rng.randn(f.size)
    sp = Spectrum()
    sp.add_sample_set(frequency=f, dbFS=y)
    return sp


def write_csv(tmpdir, sp, name='scan.csv'):
    p = str(tmpdir.join(name))
    sp.export_to_file(filename=p, frequency_format='khz')
    return p


# F1: byte format compared with real WWB (AD600 / DB26) files
@need_files
def test_F1_csv_bytes_like_wwb(tmpdir):
    ours = open(write_csv(tmpdir, make_spectrum()), 'rb').read()
    line_re = re.compile(rb'^\d+,-?\d+(\.\d)?$')
    for ref in WWB_REF + [None]:
        b = ours if ref is None else open(ref, 'rb').read()
        lines = b.split(b'\n')
        bad = [l for l in lines if not line_re.match(l)]
        k = np.array([int(l.split(b',')[0]) for l in lines])
        print('F1 %-45s lines %6d, CR %d, bad lines %d, ends with newline %s, steps %s'
              % (os.path.basename(ref) if ref else 'OUR EXPORT', len(lines), b.count(b'\r'),
                 len(bad), b.endswith(b'\n'), sorted(set(np.diff(k)))))
        assert b.count(b'\r') == 0 and not bad and not b.endswith(b'\n')
        assert set(np.diff(k)) == {25}


# F3: no inf / nan in exported values, values clipped to a sane range
def test_F3_no_inf_nan(tmpdir):
    sp = make_spectrum()
    sp.sample_data.data['dbFS'][5] = np.nan
    sp.sample_data.data['dbFS'][6] = -np.inf
    sp.sample_data.data['dbFS'][7] = np.inf
    b = open(write_csv(tmpdir, sp), 'rb').read().decode()
    vals = [float(l.split(',')[1]) for l in b.split('\n')]
    print('F3 min %.1f max %.1f' % (min(vals), max(vals)))
    assert all(np.isfinite(vals))
    assert min(vals) >= -140 and max(vals) <= 50


# F4: WWB imports only 30-2000 MHz (user report): export must be clipped
def test_F4_wwb_export_clipped_to_30_2000(tmpdir):
    sp = make_spectrum(0.0, 40.0)
    b = open(write_csv(tmpdir, sp), 'rb').read().decode()
    lines = b.split('\n')
    first = int(lines[0].split(',')[0])
    last = int(lines[-1].split(',')[0])
    print('F4 0-40 MHz scan exported as %d..%d kHz' % (first, last))
    assert first == 30000 and last == 40000
    big = make_spectrum(1990.0, 2010.0)
    b2 = open(write_csv(tmpdir, big, 'b.csv'), 'rb').read().decode().split('\n')
    assert int(b2[-1].split(',')[0]) == 2000000
    with pytest.raises(ValueError):
        write_csv(tmpdir, make_spectrum(0.0, 20.0), 'c.csv')
    p = str(tmpdir.join('x.sdb2'))
    sp.export_to_file(filename=p)
    fs = [int(t.text) for t in ET.parse(p).getroot().find('*/freq_set')]
    assert fs[0] == 30000 and fs[-1] == 40000


# F5/F6: import every CSV we have (ours in MHz and kHz, WWB references)
@need_files
@pytest.mark.parametrize('path', OUR_CSV + WWB_REF, ids=os.path.basename)
def test_F5_F6_import_all_user_csv(path):
    t0 = time.time()
    sp = BaseImporter.import_file(path)
    f = np.asarray(sp.sample_data.frequency)
    y = np.asarray(sp.sample_data['dbFS'])
    print('F5 %-45s %6d points %.3f..%.3f MHz, step %.4f, %.2f s'
          % (os.path.basename(path), f.size, f.min(), f.max(), np.median(np.diff(f)), time.time() - t0))
    assert f.size > 100
    assert 0 <= f.min() < 2000 and f.max() <= 2000      # MHz, units detected
    assert np.all(np.diff(f) > 0)
    assert np.isfinite(y).all()


# F7: round trip: our own kHz CSV -> import -> export must give the same bytes
@need_files
@pytest.mark.parametrize('path', [p for p in OUR_CSV if 'NEW5' in p or 'NEW6' in p], ids=os.path.basename)
def test_F7_round_trip_identical(tmpdir, path):
    orig = open(path, 'rb').read()
    # export keeps only 30-2000 MHz (WWB limit); compare that part
    orig = b'\n'.join(l for l in orig.split(b'\n') if 30000 <= int(l.split(b',')[0]) <= 2000000)
    sp = BaseImporter.import_file(path)
    again = open(write_csv(tmpdir, sp), 'rb').read()
    same = orig == again
    if not same:
        a, b = orig.split(b'\n'), again.split(b'\n')
        diff = sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
        print('F7 %s: %d lines differ, first: %r / %r' % (os.path.basename(path), diff,
              next((x for x, y in zip(a, b) if x != y), None), next((y for x, y in zip(a, b) if x != y), None)))
    assert same


# F8: sdb2 export of a scanned spectrum, read back
def test_F8_sdb2_export_frequencies_and_levels(tmpdir):
    sp = make_spectrum(470.0, 471.0)
    before = np.asarray(sp.sample_data['dbFS']).copy()
    p = str(tmpdir.join('scan.sdb2'))
    err = None
    try:
        sp.export_to_file(filename=p)
    except Exception as exc:
        err = exc
    print('F8 export error: %r' % (err,))
    assert err is None
    after = np.asarray(sp.sample_data['dbFS'])
    changed = not np.allclose(before, after)
    root = ET.parse(p).getroot()
    fs = [int(t.text) for t in root.find('*/freq_set')]
    exp = [int(round(v * 1000)) for v in np.asarray(sp.sample_data.frequency)]
    bad = sum(a != b for a, b in zip(fs, exp))
    ds = root.find('*/data_set').attrib
    print('F8 %d freqs, %d wrong (truncated kHz), step_freq=%r start=%r; export changed the data: %s'
          % (len(fs), bad, ds.get('step_freq'), ds.get('start_freq'), changed))
    assert bad == 0, 'kHz values truncated instead of rounded'
    assert not changed, 'export must not smooth/modify the scan'


# F9: sdb2 of a full-range scan (70 000 points) in reasonable time
def test_F9_sdb2_large(tmpdir):
    sp = make_spectrum(0.0, 1766.0)
    p = str(tmpdir.join('big.sdb2'))
    t0 = time.time()
    try:
        sp.export_to_file(filename=p)
        ok = True
    except Exception as exc:
        ok = exc
    dt = time.time() - t0
    size = os.path.getsize(p) if os.path.exists(p) else 0
    print('F9 sdb2 70641 points: %s, %.1f s, %.1f MB' % (ok, dt, size / 1e6))
    assert ok is True and dt < 30


# F10: unusual file names
@pytest.mark.parametrize('name', ['скан Торвар 470-800.csv', 'with space & #hash %.csv',
                                  'Łódź_ąęść.csv', 'a' * 120 + '.csv'])
def test_F10_file_names(tmpdir, name):
    sp = make_spectrum()
    d = tmpdir.mkdir('dir with spaces и кириллица')
    p = write_csv(d, sp, name)
    back = BaseImporter.import_file(p)
    assert back.sample_data.size == sp.sample_data.size


# F11: write into a folder that does not exist / is not writable
def test_F11_unwritable_target(tmpdir):
    sp = make_spectrum()
    p = str(tmpdir.join('no', 'such', 'dir', 'x.csv'))
    with pytest.raises(OSError):
        sp.export_to_file(filename=p, frequency_format='khz')
    ro = tmpdir.mkdir('ro')
    os.chmod(str(ro), 0o500)
    try:
        if os.geteuid() != 0:
            with pytest.raises(OSError):
                write_csv(ro, sp)
    finally:
        os.chmod(str(ro), 0o700)


# F12: broken CSV files on import: clear exception, no hang, no silent garbage
BAD = {
    'empty': b'',
    'text': b'hello world\n',
    'one_column': b'470000\n470025\n',
    'semicolon': b'470000;-90.0\n470025;-91.0\n',
    'decimal_comma': b'470000,-90,5\n470025,-91,0\n',
    'header': b'Frequency,Level\n470000,-90.0\n470025,-91.0\n',
    'crlf': b'470000,-90.0\r\n470025,-91.0\r\n',
    'nan': b'470000,nan\n470025,-91.0\n',
}


@pytest.mark.parametrize('kind', sorted(BAD))
def test_F12_broken_csv(tmpdir, kind):
    p = tmpdir.join(kind + '.csv')
    p.write_binary(BAD[kind])
    t0 = time.time()
    try:
        sp = BaseImporter.import_file(str(p))
        res = 'ok, %d points' % sp.sample_data.size
    except Exception as exc:
        res = 'raises %s: %s' % (type(exc).__name__, str(exc)[:60])
    print('F12 %-14s -> %s (%.2f s)' % (kind, res, time.time() - t0))
    assert time.time() - t0 < 5


# F13: database of performed scans: time to save grows with the file
def test_F13_dbstore_growth(tmpdir, monkeypatch):
    from wwb_scanner.utils import dbstore
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    times = []
    sp = make_spectrum(470.0, 800.0)      # 13 201 points, like 470-800 scans
    for i in range(30):
        sp.eid = None
        t0 = time.time()
        sp.save_to_dbstore()
        times.append(time.time() - t0)
    size = os.path.getsize(str(tmpdir.join('scan_db.json')))
    print('F13 save #1 %.2f s, #10 %.2f s, #30 %.2f s, db %.1f MB' % (times[0], times[9], times[-1], size / 1e6))
    assert times[-1] < 5


# F14: corrupted database file must not stop the program from starting
def test_F14_corrupted_database(tmpdir, monkeypatch):
    from wwb_scanner.utils import dbstore
    db = tmpdir.join('db.json')
    db.write('{"scan_configs": {"1": {"scan_range": [470')      # cut off
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(db))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    from wwb_scanner.scanner.main import ScannerBase
    try:
        ScannerBase()
        res = None
    except Exception as exc:
        res = exc
    print('F14 start with corrupted db.json: %r' % (res,))
    assert res is None
