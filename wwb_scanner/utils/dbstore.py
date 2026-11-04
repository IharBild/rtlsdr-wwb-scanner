import os
import datetime
import time

import numpy as np

import tinydb
from tinydb import TinyDB, where

from wwb_scanner.utils import numpyjson

APP_PATH = os.path.expanduser('~/wwb_scanner_data')

# setup.cfg pins tinydb 3.13, but pip happily installs tinydb 4 (the user
# has 4.8.0). TinyDB 4 renamed eid -> doc_id, eids= -> doc_ids= and has no
# Table._last_id. These helpers work with both.
TINYDB4 = int(getattr(tinydb, '__version__', '3').split('.')[0]) >= 4


def doc_id(doc):
    """id of a stored document (Document.doc_id in 4, Element.eid in 3)"""
    if doc is None:
        return None
    return getattr(doc, 'doc_id', None) if TINYDB4 else doc.eid


def get_by_id(table, i):
    return table.get(doc_id=i) if TINYDB4 else table.get(eid=i)


def update_ids(table, data, ids):
    return table.update(data, doc_ids=ids) if TINYDB4 else table.update(data, eids=ids)


def remove_ids(table, ids):
    return table.remove(doc_ids=ids) if TINYDB4 else table.remove(eids=ids)


def last_id(table):
    if not TINYDB4:
        return table._last_id
    ids = [d.doc_id for d in table.all()]
    return max(ids) if ids else 0

class JSONStorage(tinydb.JSONStorage):
    def read(self):
        # Get the file size
        self._handle.seek(0, os.SEEK_END)
        size = self._handle.tell()

        if not size:
            # File is empty
            return None
        self._handle.seek(0)
        try:
            return numpyjson.load(self._handle)
        except ValueError as exc:
            # A cut-off file (power loss, crash while saving) used to stop
            # the program from starting. Keep a copy and start a new one.
            path = getattr(self._handle, 'name', None)
            backup = None
            if path:
                backup = '%s.broken-%s' % (path, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
                try:
                    import shutil
                    shutil.copyfile(path, backup)
                except Exception:
                    backup = None
            print('!!! database file is damaged (%s), saved as %s, starting a new one'
                  % (exc, backup), flush=True)
            self._handle.seek(0)
            self._handle.truncate()
            return None
    def write(self, data):
        self._handle.seek(0)
        serialized = numpyjson.dumps(data)
        self._handle.write(serialized)
        self._handle.flush()
        self._handle.truncate()

class DBStore(object):
    DB_PATH = os.path.join(APP_PATH, 'db.json')
    # V14: the scan database holds only the description of each scan; the
    # points are in <folder of this file>/scans/*.npz (File > Import opens
    # them). Before V14 every scan (4.7 MB of JSON for 31-1766 MHz) was inside
    # scan_db.json and TinyDB re-read and re-wrote the whole file on every
    # save: 152 scans = 271 MB, ~11 s after each scan. The old file is left
    # as it is (tools/migrate_scan_db.py moves its scans to the new layout).
    SCAN_DB_PATH = os.path.join(APP_PATH, 'scan_index.json')
    LEGACY_SCAN_DB_PATH = os.path.join(APP_PATH, 'scan_db.json')
    TABLES = ['scan_configs', 'scans_performed', 'scans_imported']
    def __init__(self):

        self._db = None
        self._scan_db = None
        #self.migrate_db()
    @property
    def db(self):
        db = self._db
        if db is None:
            self._check_dirs()
            db = self._db = TinyDB(self.DB_PATH, storage=JSONStorage)
        return db
    @property
    def scan_db(self):
        db = self._scan_db
        if db is None:
            self._check_dirs()
            db = self._scan_db = TinyDB(self.SCAN_DB_PATH, storage=JSONStorage)
        return db
    def _check_dirs(self):
        if not os.path.exists(os.path.dirname(self.DB_PATH)):
            os.makedirs(os.path.dirname(self.DB_PATH))
        if not os.path.exists(os.path.dirname(self.SCAN_DB_PATH)):
            os.makedirs(os.path.dirname(self.SCAN_DB_PATH))
    def migrate_db(self):
        for table_name in ['scans_performed', 'scans_imported']:
            if table_name not in self.db.tables():
                continue
            print('migrating table "{}"'.format(table_name))
            old_table = self.db.table(table_name)
            new_table = self.scan_db.table(table_name)
            eids = []
            for item in old_table.all():
                eids.append(doc_id(item))
                new_table.insert(item)
            remove_ids(old_table, eids)
            if TINYDB4:
                self.db.drop_table(table_name)
            else:
                self.db.purge_table(table_name)
    def add_scan_config(self, config, force_insert=False):
        if config.get('datetime') is None:
            config['datetime'] = datetime.datetime.utcnow()
        table = self.db.table('scan_configs')
        if config.get('eid') is not None:
            dbconfig = get_by_id(table, config.eid)
        else:
            dbconfig = table.get(where('datetime') == config['datetime'])
        if dbconfig is not None:
            if force_insert:
                update_ids(table, config._serialize(), [doc_id(dbconfig)])
            eid = doc_id(dbconfig)
        else:
            eid = table.insert(config._serialize())
        config.eid = eid
    def get_scan_config(self, **kwargs):
        table = self.db.table('scan_configs')
        if kwargs.get('datetime'):
            dbconfig = table.get(where('datetime')==kwargs.get('datetime'))
        elif kwargs.get('eid'):
            dbconfig = get_by_id(table, kwargs.get('eid'))
        elif kwargs.get('name'):
            dbconfig = table.get(where('name')==kwargs.get('name'))
        elif last_id(table) > 0:
            dbconfig = get_by_id(table, last_id(table))
        else:
            dbconfig = None
        return dbconfig
    @property
    def scans_dir(self):
        return os.path.join(os.path.dirname(self.SCAN_DB_PATH), 'scans')
    def _scan_file_name(self, eid, data, sample_data):
        ts = data.get('timestamp_utc') or time.time()
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(ts))
        rng = ''
        try:
            f = sample_data['frequency']
            if f.size:
                rng = '_%g-%gMHz' % (round(float(f.min()), 3), round(float(f.max()), 3))
        except Exception:
            pass
        return 'scan_%05d_%s%s.npz' % (eid, stamp, rng)
    def _write_points(self, eid, data, sample_data, name=None):
        if not os.path.isdir(self.scans_dir):
            os.makedirs(self.scans_dir)
        name = name or self._scan_file_name(eid, data, sample_data)
        path = os.path.join(self.scans_dir, name)
        tmp = path + '.tmp.npz'
        np.savez_compressed(tmp, sample_data=sample_data)
        os.replace(tmp, path)
        return name
    def add_scan(self, spectrum, scan_config=None):
        if scan_config is None:
            scan_config = spectrum.scan_config
        if scan_config is not None:
            if scan_config.get('eid') is None:
                self.add_scan_config(scan_config)
            spectrum.scan_config_eid = scan_config.eid
        data = spectrum._serialize()
        sd = data.pop('sample_data', None)
        points = getattr(sd, 'data', sd)
        table = self.scan_db.table('scans_performed')
        if spectrum.eid is not None:
            eid = spectrum.eid
            old = get_by_id(table, eid)
            name = old.get('sample_data_file') if old is not None else None
        else:
            eid = table.insert(data)
            spectrum.eid = eid
            name = None
        if points is not None:
            data['sample_data_file'] = self._write_points(eid, data, points, name)
        update_ids(table, data, [eid])
        return eid
    def get_all_scans(self):
        table = self.scan_db.table('scans_performed')
        scans = table.all()
        scan_data = {}
        for scan in scans:
            excluded = ['samples', 'sample_data', 'center_frequencies']
            scan_data[doc_id(scan)] = {key:scan[key] for key in scan.keys()
                                    if key not in excluded}
        return scan_data
    def get_scan(self, eid):
        table = self.scan_db.table('scans_performed')
        rec = get_by_id(table, eid)
        if rec is None:
            return None
        rec = dict(rec)
        name = rec.get('sample_data_file')
        if name:
            from wwb_scanner.scan_objects.samplearray import SampleArray
            with np.load(os.path.join(self.scans_dir, name)) as z:
                rec['sample_data'] = SampleArray(data=z['sample_data'])
        return rec
    def update_scan(self, eid, **kwargs):
        table = self.scan_db.table('scans_performed')
        update_ids(table, kwargs, [eid])

db_store = DBStore()
