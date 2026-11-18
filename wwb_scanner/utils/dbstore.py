"""History of scans and the settings of past scans (V22.0: without tinydb).

Two JSON files in the data folder (wwb_scanner/paths.py):

- ``db.json``: table ``scan_configs`` (settings of every scan);
- ``scan_index.json``: table ``scans_performed`` (description of every scan;
  its points are in ``scans/*.npz``, V14).

The files keep the layout TinyDB used before V22.0 (``{"table": {"1":
{...}}}``), so the files of earlier versions are read as they are and
earlier versions can still read the new ones. TinyDB re-read and re-wrote
the whole file on every operation; this store keeps the tables in memory,
reads the file again only when it changed on disk and writes through a
temporary file, so a crash while saving leaves the old file whole.
"""
from wwb_scanner import events
import os
import datetime
import time

import numpy as np

from wwb_scanner.utils import numpyjson


class Document(dict):
    """A stored record: a dict with its id (``doc_id``)."""
    def __init__(self, value, doc_id):
        super(Document, self).__init__(value)
        self.doc_id = doc_id


class Table(object):
    def __init__(self, db, name):
        self._db = db
        self.name = name
    def _rows(self):
        return self._db._tables().setdefault(self.name, {})
    def all(self):
        return [Document(v, int(k)) for k, v in sorted(self._rows().items(), key=lambda kv: int(kv[0]))]
    def __len__(self):
        return len(self._rows())
    def insert(self, data):
        rows = self._rows()
        new_id = max([int(k) for k in rows] or [0]) + 1
        rows[str(new_id)] = dict(data)
        self._db._write()
        return new_id
    def get(self, doc_id=None, **match):
        """By id, or the first record whose fields equal ``match``."""
        rows = self._rows()
        if doc_id is not None:
            v = rows.get(str(int(doc_id)))
            return None if v is None else Document(v, int(doc_id))
        for d in self.all():
            if all(k in d and d[k] == v for k, v in match.items()):
                return d
        return None
    def update(self, data, doc_ids):
        rows = self._rows()
        for i in doc_ids:
            key = str(int(i))
            if key in rows:
                rows[key].update(data)
        self._db._write()
    def remove(self, doc_ids):
        rows = self._rows()
        for i in doc_ids:
            rows.pop(str(int(i)), None)
        self._db._write()
    def last_id(self):
        return max([int(k) for k in self._rows()] or [0])


class JsonDB(object):
    """Tables in one JSON file (the TinyDB layout)."""
    def __init__(self, path):
        self.path = str(path)
        self._data = None
        self._stamp = None
    def _file_stamp(self):
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
    def _tables(self):
        stamp = self._file_stamp()
        if self._data is None or stamp != self._stamp:
            self._data = self._read()
            self._stamp = self._file_stamp()
        return self._data
    def _read(self):
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            return {}
        try:
            with open(self.path, encoding='utf-8') as fh:
                data = numpyjson.load(fh)
            if not isinstance(data, dict):
                raise ValueError('not a table file')
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        except (ValueError, UnicodeDecodeError) as exc:
            # A cut-off file (power loss, crash while saving) used to stop
            # the program from starting. Keep a copy and start a new one.
            backup = '%s.broken-%s' % (self.path, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
            try:
                import shutil
                shutil.copyfile(self.path, backup)
            except Exception:
                backup = None
            events.console('!!! database file is damaged (%s), saved as %s, starting a new one'
                  % (exc, backup))
            return {}
    def _write(self):
        d = os.path.dirname(self.path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(numpyjson.dumps(self._tables()))
        os.replace(tmp, self.path)
        self._stamp = self._file_stamp()
    def table(self, name):
        return Table(self, name)
    def tables(self):
        return set(self._tables().keys())
    def drop_table(self, name):
        if self._tables().pop(name, None) is not None:
            self._write()


# helpers kept from V13 (tools and tests use them)
def doc_id(doc):
    """id of a stored record"""
    if doc is None:
        return None
    return getattr(doc, 'doc_id', None)


def get_by_id(table, i):
    return table.get(doc_id=i)


def update_ids(table, data, ids):
    return table.update(data, doc_ids=ids)


def remove_ids(table, ids):
    return table.remove(doc_ids=ids)


def last_id(table):
    return table.last_id()


class DBStore(object):
    # V22.0: None = the file in the data folder (wwb_scanner/paths.py), found
    # when first used. Tests and tools may set a path here.
    DB_PATH = None
    # V14: the scan database holds only the description of each scan; the
    # points are in <folder of this file>/scans/*.npz (File > Import opens
    # them). Before V14 every scan (4.7 MB of JSON for 31-1766 MHz) was inside
    # scan_db.json, re-read and re-written on every save: 152 scans = 271 MB,
    # ~11 s after each scan. The old file is left as it is
    # (tools/migrate_scan_db.py moves its scans to the new layout).
    SCAN_DB_PATH = None
    LEGACY_SCAN_DB_PATH = None
    TABLES = ['scan_configs', 'scans_performed', 'scans_imported']
    def __init__(self):
        self._db = None
        self._scan_db = None
    @property
    def db_path(self):
        if self.DB_PATH:
            return str(self.DB_PATH)
        from wwb_scanner import paths
        return paths.file_path('db.json')
    @property
    def scan_db_path(self):
        if self.SCAN_DB_PATH:
            return str(self.SCAN_DB_PATH)
        from wwb_scanner import paths
        return paths.file_path('scan_index.json')
    @property
    def legacy_scan_db_path(self):
        if self.LEGACY_SCAN_DB_PATH:
            return str(self.LEGACY_SCAN_DB_PATH)
        return os.path.join(os.path.dirname(self.scan_db_path), 'scan_db.json')
    @property
    def db(self):
        db = self._db
        if db is None or db.path != self.db_path:
            db = self._db = JsonDB(self.db_path)
        return db
    @property
    def scan_db(self):
        db = self._scan_db
        if db is None or db.path != self.scan_db_path:
            db = self._scan_db = JsonDB(self.scan_db_path)
        return db
    def add_scan_config(self, config, force_insert=False):
        if config.get('datetime') is None:
            # V21.3: utcnow() is deprecated since Python 3.12; same naive UTC value
            config['datetime'] = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        table = self.db.table('scan_configs')
        if config.get('eid') is not None:
            dbconfig = get_by_id(table, config.eid)
        else:
            dbconfig = table.get(datetime=config['datetime'])
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
            dbconfig = table.get(datetime=kwargs.get('datetime'))
        elif kwargs.get('eid'):
            dbconfig = get_by_id(table, kwargs.get('eid'))
        elif kwargs.get('name'):
            dbconfig = table.get(name=kwargs.get('name'))
        elif last_id(table) > 0:
            dbconfig = get_by_id(table, last_id(table))
        else:
            dbconfig = None
        return dbconfig
    @property
    def scans_dir(self):
        return os.path.join(os.path.dirname(self.scan_db_path), 'scans')
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
    @staticmethod
    def _safe_name(name):
        """V18.1 (audit 2.4): a points file name from scan_index.json must be
        a plain file name inside the scans folder (no ../, no other drive)."""
        if not name or not isinstance(name, str):
            return None
        bad_chars = any(c in name for c in ':/\\')
        if bad_chars or os.path.basename(name) != name or name in ('.', '..') or os.path.isabs(name):
            return None
        return name
    def _write_points(self, eid, data, sample_data, name=None, raw_dbFS=None, passes=None):
        if not os.path.isdir(self.scans_dir):
            os.makedirs(self.scans_dir, exist_ok=True)
        name = self._safe_name(name) or self._scan_file_name(eid, data, sample_data)
        path = os.path.join(self.scans_dir, name)
        tmp = path + '.tmp.npz'
        arrays = {'sample_data': sample_data}
        if raw_dbFS is not None:
            # V17: values as measured, before the clean scan corrections
            arrays['raw_dbFS'] = np.asarray(raw_dbFS)
        if passes is not None:
            arrays.update(passes.to_arrays())       # V22.2: every pass of the scan
        np.savez_compressed(tmp, **arrays)
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
        info = getattr(spectrum, 'cleanup_info', None)
        if info:
            data['cleanup'] = info
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
            data['sample_data_file'] = self._write_points(
                eid, data, points, name, raw_dbFS=getattr(spectrum, 'raw_dbFS', None),
                passes=getattr(spectrum, 'passes', None))
            if getattr(spectrum, 'passes', None) is not None:
                data['passes'] = len(spectrum.passes)
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
        if name and self._safe_name(name) is None:
            import logging
            logging.getLogger(__name__).error(
                'scan %s: points file name %r is not a plain file name, not read', eid, name)
            name = None
        if name:
            from wwb_scanner.scan_objects.samplearray import SampleArray
            from wwb_scanner.scan_objects.passes import ScanPasses
            with np.load(os.path.join(self.scans_dir, name)) as z:
                rec['sample_data'] = SampleArray(data=z['sample_data'])
                p = ScanPasses.from_arrays(z)
                if p is not None:
                    rec['passes'] = p              # V22.2
        return rec
    def update_scan(self, eid, **kwargs):
        table = self.scan_db.table('scans_performed')
        update_ids(table, kwargs, [eid])

db_store = DBStore()
