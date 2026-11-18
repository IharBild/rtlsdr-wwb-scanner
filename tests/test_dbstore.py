import numpy as np

def test_dbstore_monkeypatch(tmp_db_store):
    from wwb_scanner.scan_objects import spectrum as spec_mod
    from wwb_scanner.utils import dbstore

    assert dbstore.DBStore.DB_PATH == dbstore.db_store.DB_PATH == tmp_db_store['db_path']
    assert dbstore.DBStore.SCAN_DB_PATH == dbstore.db_store.SCAN_DB_PATH == tmp_db_store['scan_db_path']
    # V22.0: Spectrum finds the store when it is used (no import-time link)
    store = spec_mod._db_store()
    assert store is dbstore.db_store
    assert store.db_path == str(tmp_db_store['db_path'])
    assert store.scan_db_path == str(tmp_db_store['scan_db_path'])


def test_default_paths_follow_the_data_folder(tmpdir, monkeypatch):
    """V22.0: db.json and scan_index.json are in the data folder (paths.py),
    resolved when used: WWB_SCANNER_DATA moves them."""
    from wwb_scanner.utils import dbstore
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', None)
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', None)
    monkeypatch.setenv('WWB_SCANNER_DATA', str(tmpdir.join('data')))
    store = dbstore.DBStore()
    assert store.db_path == str(tmpdir.join('data', 'db.json'))
    assert store.scan_db_path == str(tmpdir.join('data', 'scan_index.json'))
    assert store.scans_dir == str(tmpdir.join('data', 'scans'))
    assert store.legacy_scan_db_path == str(tmpdir.join('data', 'scan_db.json'))


def test_tinydb_files_are_read_and_written_in_the_same_layout(tmpdir):
    """V22.0: no tinydb; files of earlier versions read as they are."""
    import json
    from wwb_scanner.utils import dbstore
    p = tmpdir.join('scan_index.json')
    p.write('{"_default": {}, "scans_performed": {"1": {"name": "a"}, "3": {"name": "b"}}}')
    db = dbstore.JsonDB(str(p))
    t = db.table('scans_performed')
    assert [(dbstore.doc_id(d), d['name']) for d in t.all()] == [(1, 'a'), (3, 'b')]
    assert dbstore.last_id(t) == 3
    new = t.insert({'name': 'c'})
    assert new == 4
    dbstore.update_ids(t, {'x': 1}, [1])
    assert t.get(name='a')['x'] == 1 and t.get(doc_id=4)['name'] == 'c'
    dbstore.remove_ids(t, [3])
    on_disk = json.loads(p.read())
    assert sorted(on_disk['scans_performed']) == ['1', '4'] and '_default' in on_disk
    # another process (or an earlier version) changes the file: read again
    on_disk['scans_performed']['9'] = {'name': 'z'}
    import os, time
    time.sleep(0.01)
    p.write(json.dumps(on_disk))
    os.utime(str(p), None)
    assert t.get(doc_id=9)['name'] == 'z'
    assert not tmpdir.join('scan_index.json.tmp').exists()

def test_dbstore(tmp_db_store, data_files, random_samples):
    from wwb_scanner.file_handlers import BaseImporter
    from wwb_scanner.scan_objects import Spectrum
    from wwb_scanner.utils.dbstore import db_store

    spec = {}
    for fkey, d in data_files.items():
        for skey, fn in d.items():
            spectrum = BaseImporter.import_file(fn)
            spectrum.save_to_dbstore()
            assert spectrum.eid is not None
            spec[spectrum.eid] = spectrum

    rs = 1.024e6
    nsamp = 256
    step_size = 0.5e6
    freq_range = [572e6, 636e6]

    spectrum = Spectrum()
    fc = freq_range[0]
    while True:
        freqs, sig, Pxx = random_samples(n=nsamp, rs=rs, fc=fc)
        spectrum.add_sample_set(frequency=freqs, magnitude=np.abs(Pxx))
        if spectrum.sample_data['frequency'].max() >= freq_range[1] / 1e6:
            break
        fc += step_size

    spectrum.save_to_dbstore()
    assert spectrum.eid is not None
    spec[spectrum.eid] = spectrum

    for eid, spectrum in spec.items():
        db_spectrum = Spectrum.from_dbstore(eid=spectrum.eid)
        for attr in Spectrum._serialize_attrs:
            assert getattr(spectrum, attr) == getattr(db_spectrum, attr)
        assert np.array_equal(spectrum.sample_data, db_spectrum.sample_data)

    db_data = db_store.get_all_scans()

    assert set(db_data.keys()) == set(spec.keys())
