# -*- coding: utf-8 -*-
"""Move the scans of the old scan database (scan_db.json, before V14) to
the new layout: one file per scan in wwb_scanner_data\\scans\\*.npz and a
small index scan_index.json.

    python migrate_scan_db.py            copy all scans (old file is kept)
    python migrate_scan_db.py --check    only show what is in the old file

The old scan_db.json is NOT changed or deleted. After checking that the
scans are in the scans folder (File > Import opens any *.npz), you can
move scan_db.json to an archive or delete it yourself.
Running it twice does not copy a scan twice.
"""
from __future__ import print_function

import argparse
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--src', default=None, help='old scan_db.json (default: the program folder)')
    args = ap.parse_args()

    import numpy as np
    from wwb_scanner.utils import numpyjson
    from wwb_scanner.utils.dbstore import db_store, doc_id
    import wwb_scanner.scan_objects  # noqa: F401  (registers the JSON decoders)

    src = args.src or db_store.LEGACY_SCAN_DB_PATH
    if not os.path.exists(src):
        print('Old database not found: %s - nothing to do.' % src)
        return 0
    print('Reading %s (%.0f MB)...' % (src, os.path.getsize(src) / 1e6), flush=True)
    t0 = time.time()
    with open(src) as f:
        old = numpyjson.load(f)
    table_old = old.get('scans_performed', {}) or {}
    print('  %d scans, read in %.1f s' % (len(table_old), time.time() - t0))
    if args.check:
        for k in sorted(table_old, key=lambda x: int(x)):
            rec = table_old[k]
            ts = rec.get('timestamp_utc')
            print('  #%-4s %s  %s' % (k, time.strftime('%Y-%m-%d %H:%M', time.localtime(ts)) if ts else '?',
                                      rec.get('name')))
        return 0

    table = db_store.scan_db.table('scans_performed')
    done = {r.get('legacy_eid') for r in table.all() if r.get('legacy_eid') is not None}
    n_new, n_skip, n_bad = 0, 0, 0
    t0 = time.time()
    for k in sorted(table_old, key=lambda x: int(x)):
        if int(k) in done:
            n_skip += 1
            continue
        rec = dict(table_old[k])
        sd = rec.pop('sample_data', None)
        points = getattr(sd, 'data', None)
        if points is None and isinstance(sd, dict):
            points = sd.get('data')
        if points is None or not isinstance(points, np.ndarray):
            n_bad += 1
            print('  #%s: no points, skipped' % k)
            continue
        rec['legacy_eid'] = int(k)
        eid = table.insert(rec)
        rec['sample_data_file'] = db_store._write_points(eid, rec, points)
        from wwb_scanner.utils.dbstore import update_ids
        update_ids(table, rec, [eid])
        n_new += 1
        if n_new % 20 == 0:
            print('  %d scans copied...' % n_new, flush=True)
    size = sum(os.path.getsize(os.path.join(db_store.scans_dir, f)) for f in os.listdir(db_store.scans_dir)) \
        if os.path.isdir(db_store.scans_dir) else 0
    print('Done in %.0f s: copied %d, already there %d, without points %d.' % (time.time() - t0, n_new, n_skip, n_bad))
    print('Scans folder: %s (%.0f MB)' % (db_store.scans_dir, size / 1e6))
    print('Index: %s' % db_store.SCAN_DB_PATH)
    print('Old file kept unchanged: %s' % src)
    print('Check a few scans with File > Import (*.npz), then archive or delete the old file.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
