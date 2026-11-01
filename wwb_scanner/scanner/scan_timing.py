"""Time measurement of a scan (plan step V1).

Records, for every tile, when the stages started and finished, and writes a
short report to ``~/wwb_scan_timing.txt`` at the end of the scan. It never
raises: a problem with the measurement must not break a scan.
"""
import os
import time
import threading

import logging
logger = logging.getLogger(__name__)

STAGES = ('start', 'tuned', 'got_data', 'processed', 'inserted', 'returned')
REPORT_FILENAME = 'wwb_scan_timing.txt'


class ScanTiming(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.tiles = {}
    def tick(self, key, stage):
        try:
            with self.lock:
                self.tiles.setdefault(key, {})[stage] = time.perf_counter()
        except Exception:
            pass
    def _durations(self):
        rows = []
        for key in sorted(self.tiles):
            t = self.tiles[key]
            if not all(s in t for s in STAGES):
                continue
            rows.append(dict(
                key=key,
                tune=t['tuned'] - t['start'],
                read=t['got_data'] - t['tuned'],
                process=t['processed'] - t['got_data'],
                insert=t['inserted'] - t['processed'],
                stop=t['returned'] - t['inserted'],
                start=t['start'], end=t['returned'],
            ))
        return rows
    def build_report(self):
        rows = self._durations()
        if not rows:
            return 'no complete tiles measured'
        n = len(rows)
        total = rows[-1]['end'] - rows[0]['start']
        lines = ['Scan timing (seconds)', 'tiles measured: %d' % n,
                 'wall time first tile start -> last tile end: %.1f' % total,
                 'average per tile: %.3f' % (total / n), '',
                 'stage                    mean      max     share of wall time']
        names = (('tune', 'set_center_freq'), ('read', 'waiting for samples'),
                 ('process', 'welch + crop'), ('insert', 'merge into spectrum'),
                 ('stop', 'stop reading, return'))
        for field, label in names:
            vals = [r[field] for r in rows]
            share = sum(vals) / total * 100. if total else 0.
            lines.append('%-24s %.3f    %.3f    %.0f %%' % (label, sum(vals) / n, max(vals), share))
        gaps = [rows[i + 1]['start'] - rows[i]['end'] for i in range(n - 1)]
        if gaps:
            share = sum(gaps) / total * 100. if total else 0.
            lines.append('%-24s %.3f    %.3f    %.0f %%' % ('between tiles', sum(gaps) / len(gaps), max(gaps), share))
        # does it get slower during the scan?
        k = max(1, n // 5)
        first = sum(r['end'] - r['start'] for r in rows[:k]) / k
        last = sum(r['end'] - r['start'] for r in rows[-k:]) / k
        lines += ['', 'average tile time, first 20%%: %.3f, last 20%%: %.3f' % (first, last)]
        return '\n'.join(lines)
    def write_report(self, path=None):
        try:
            report = self.build_report()
            logger.info(report)
            if path is None:
                path = os.path.join(os.path.expanduser('~'), REPORT_FILENAME)
            with open(path, 'w') as f:
                f.write(report + '\n')
            return path
        except Exception:
            logger.exception('could not write timing report')
            return None
