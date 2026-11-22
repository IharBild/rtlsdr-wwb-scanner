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
import contextlib
import os
import datetime
import tempfile
import threading
import time

import numpy as np

from wwb_scanner.utils import numpyjson

# V22.9 (external audit P3-03 / P2-HIST1): two programs (two windows, the
# window and a tool) on one data folder lost records, gave two scans one
# number and failed with PermissionError when one replaced the index while
# the other had it open. Every change is now made under a lock file, on the
# file as it is on disk at that moment; replacing the file is tried again
# for a while (Windows: an antivirus or the search indexer may hold it).
LOCK_TIMEOUT_S = 10.
REPLACE_TRIES = 20
REPLACE_PAUSE_S = 0.1


class HistoryBusy(IOError):
    """The history is locked by another program for too long."""


class FileLock(object):
    """A lock between programs (a locked byte of ``path``), and between the
    threads of this program; re-entrant in one thread."""
    def __init__(self, path):
        self.path = path
        self._tl = threading.RLock()
        self._depth = 0
        self._fh = None
    def _try(self, fh):
        try:
            if os.name == 'nt':
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    def _unlock(self, fh):
        try:
            if os.name == 'nt':
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    def acquire(self, timeout=None):
        timeout = LOCK_TIMEOUT_S if timeout is None else timeout
        deadline = time.monotonic() + timeout
        if not self._tl.acquire(timeout=timeout):
            raise HistoryBusy('the history is busy in this program (%s)' % self.path)
        if self._depth:
            self._depth += 1
            return
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            fh = open(self.path, 'a+b')
            while not self._try(fh):
                if time.monotonic() >= deadline:
                    fh.close()
                    raise HistoryBusy('the history is locked by another program for %.0f s (%s)'
                                      % (timeout, self.path))
                time.sleep(0.05)
        except BaseException:
            self._tl.release()
            raise
        self._fh = fh
        self._depth = 1
    def release(self):
        self._depth -= 1
        if not self._depth:
            fh, self._fh = self._fh, None
            self._unlock(fh)
            fh.close()
        self._tl.release()
    def __enter__(self):
        self.acquire()
        return self
    def __exit__(self, *exc):
        self.release()


def _replace(src, dst):
    """os.replace, tried again while Windows says the file is in use."""
    for i in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE_S)


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
        with self._db.changing() as tables:
            rows = tables.setdefault(self.name, {})
            new_id = max([int(k) for k in rows] or [0]) + 1
            rows[str(new_id)] = dict(data)
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
        with self._db.changing() as tables:
            rows = tables.setdefault(self.name, {})
            for i in doc_ids:
                key = str(int(i))
                if key in rows:
                    rows[key].update(data)
    def remove(self, doc_ids):
        with self._db.changing() as tables:
            rows = tables.setdefault(self.name, {})
            for i in doc_ids:
                rows.pop(str(int(i)), None)
    def last_id(self):
        return max([int(k) for k in self._rows()] or [0])


class JsonDB(object):
    """Tables in one JSON file (the TinyDB layout)."""
    def __init__(self, path):
        self.path = str(path)
        self._data = None
        self._stamp = None
        self.lock = FileLock(self.path + '.lock')
    @contextlib.contextmanager
    def changing(self):
        """V22.9: change the tables as they are on disk now, under the lock,
        and write them."""
        with self.lock:
            self.refresh()
            data = self._tables()
            yield data
            self._write()
    def refresh(self):
        """Read the file again at the next use."""
        self._data = None
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
            tables = {k: v for k, v in data.items() if isinstance(v, dict)}
            # V22.9 (external audit P3-13): a record whose key is not a number
            # stopped every read of the history (int('x')); left out
            bad = 0
            for name, rows in tables.items():
                keep = {k: v for k, v in rows.items()
                        if isinstance(k, str) and k.isdigit() and isinstance(v, dict)}
                bad += len(rows) - len(keep)
                tables[name] = keep
            if bad:
                events.console('!!! %s: %d damaged record(s) left out' % (self.path, bad))
            return tables
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
        """V22.9: a temporary file of its own (two programs used one name),
        on disk (fsync) before it replaces the file."""
        d = os.path.dirname(self.path) or '.'
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        text = numpyjson.dumps(self._tables())
        fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(self.path) + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            _replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self._stamp = self._file_stamp()
    def table(self, name):
        return Table(self, name)
    def tables(self):
        return set(self._tables().keys())
    def drop_table(self, name):
        with self.changing() as tables:
            tables.pop(name, None)


# V22.8 (external audit B1): marks a scan puts on its spectrum for the graph
# (sample_processing.report_overload), kept in the history with the scan
MARK_KEYS = ('image_marks', 'product_marks', 'loss_bands', 'unmeasurable_bands')


def scan_marks(spectrum):
    """{key: [mark, ...]} of the marks ``spectrum`` has (only those it has)."""
    out = {}
    for k in MARK_KEYS:
        v = getattr(spectrum, k, None)
        if v:
            out[k] = [dict(x) for x in v]
    return out


# helpers kept from V13 (tools and tests use them)
def doc_id(doc):
    """id of a stored record"""
    if doc is None:
        return None
    return getattr(doc, 'doc_id', None)


def get_by_id(table, i):
    if i is None:
        return None             # V22.9: Table.get() without an id gave the first record
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
        own = self._safe_name(name)
        name = own or self._scan_file_name(eid, data, sample_data)
        if not own:
            # V22.8: a new file never takes the place of another scan's points
            # (numbers start at 1 again after a damaged index was replaced)
            base, n = name[:-len('.npz')], 1
            while os.path.exists(os.path.join(self.scans_dir, name)):
                n += 1
                name = '%s_%d.npz' % (base, n)
        path = os.path.join(self.scans_dir, name)
        tmp = path + '.tmp.npz'
        arrays = {'sample_data': sample_data}
        if raw_dbFS is not None:
            # V17: values as measured, before the clean scan corrections
            arrays['raw_dbFS'] = np.asarray(raw_dbFS)
        if passes is not None:
            arrays.update(passes.to_arrays())       # V22.2: every pass of the scan
        np.savez_compressed(tmp, **arrays)
        _replace(tmp, path)
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
        # V22.8 (external audit B1): the marks of the scan (images, products
        # of the dongle, bands with less sensitivity, bands not measurable)
        # are kept with it; the raw values are in the points file
        marks = scan_marks(spectrum)
        if marks:
            data['marks'] = marks
        sd = data.pop('sample_data', None)
        points = getattr(sd, 'data', sd)
        # V22.9 (external audit P3-03): number, points file and record under
        # one lock, with the index as it is on disk now
        with self.scan_db.lock:
            self.scan_db.refresh()
            return self._add_scan_locked(spectrum, data, points)
    def _add_scan_locked(self, spectrum, data, points):
        table = self.scan_db.table('scans_performed')
        new = spectrum.eid is None
        if not new:
            eid = spectrum.eid
            old = get_by_id(table, eid)
            name = old.get('sample_data_file') if old is not None else None
        else:
            # V22.8 (external audit B4): the points file first, then one record
            # in the index - a record never points to a file not yet written
            eid = table.last_id() + 1
            name = None
        if points is not None:
            data['sample_data_file'] = self._write_points(
                eid, data, points, name, raw_dbFS=getattr(spectrum, 'raw_dbFS', None),
                passes=getattr(spectrum, 'passes', None))
            if getattr(spectrum, 'passes', None) is not None:
                data['passes'] = len(spectrum.passes)
        if new:
            eid = table.insert(data)
            spectrum.eid = eid
        else:
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
            scan_data[doc_id(scan)]['eid'] = doc_id(scan)      # V22.9 (audit AUD-03)
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
                if 'raw_dbFS' in z.files:
                    raw = np.asarray(z['raw_dbFS'], dtype=np.float64)
                    if raw.shape == (rec['sample_data'].size,):
                        rec['raw_dbFS'] = raw      # V22.8 (audit B1): read back too
                p = ScanPasses.from_arrays(z)
                if p is not None:
                    rec['passes'] = p              # V22.2
        return rec
    def update_scan(self, eid, **kwargs):
        table = self.scan_db.table('scans_performed')
        update_ids(table, kwargs, [eid])

db_store = DBStore()
