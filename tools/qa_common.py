# -*- coding: utf-8 -*-
"""Shared parts of qa_sandbox.py and qa_pc.py: test categories, results,
report, version markers, file manifest of a release."""
from __future__ import print_function

import hashlib
import io
import json
import os
import sys
import time

CATEGORIES = [
    ('SMOKE', 'Дымовое (Smoke)'),
    ('SANITY', 'Санити (Sanity)'),
    ('UNIT', 'Модульное (Unit)'),
    ('INTEGRATION', 'Интеграционное (Integration)'),
    ('FUNCTIONAL', 'Функциональное (Functional)'),
    ('SYSTEM', 'Системное (System)'),
    ('REGRESSION', 'Регрессионное (Regression)'),
    ('PERFORMANCE', 'Нагрузочное / скорость (Performance / Load)'),
    ('SECURITY', 'Безопасность (Security)'),
    ('ACCEPTANCE', 'Приёмочное (Acceptance)'),
]
CAT_NAMES = dict(CATEGORIES)
BAD = ('FAIL',)
STATUS_ORDER = ['FAIL', 'FINDING', 'WARN', 'PARTIAL', 'MANUAL', 'INFO', 'SKIP', 'PASS']

# (version, file inside wwb_scanner, text that only this version has)
MARKERS = [
    ('V9', 'scanner/scan_timing.py', 'class ScanTiming'),
    ('V10', 'scanner/sample_processing.py', 'run_with_timeout'),
    ('V11.2', 'ui/pyside/qml/ExportDialog.qml', 'withExtension'),
    ('V12', 'file_handlers/exporters.py', 'class WWB3Exporter'),
    ('V13', 'utils/dbstore.py', 'TINYDB4'),
    ('V14', 'utils/dbstore.py', 'savez_compressed'),
    ('V15', 'ui/pyside/qml/Graph.qml', 'niceStep'),
    ('V16', 'ui/pyside/status.py', 'class StatusHub'),
    ('V16.1', 'ui/pyside/main.py', 'def apply_style'),
    ('V17', 'scanner/cleanup.py', 'def remove_spurs'),
    ('V17.1', 'utils/numpyjson.py', 'class _SafeUnpickler'),
    ('V18', 'bands.py', 'def scan_segments'),
    ('V18.1', 'ui/pyside/main.py', 'def install_error_hooks'),
    ('V19', 'file_handlers/exporters.py', 'def wsm_csv'),
    ('V19.1', 'ui/pyside/graph.py', "uri.fragment().lower() == 'wsm'"),
    ('V20', 'scanner/sample_processing.py', 'def remeasure_neighbours'),
    ('V20.1', 'scanner/sample_processing.py', 'def carrier_zones'),
    ('V20.2', 'scanner/sample_processing.py', 'def remove_images'),
    ('V20.3', 'ui/pyside/qml/main.qml', 'showLossBands'),
    ('V20.4', 'scanner/sample_processing.py', 'def reopen_dongle'),
    ('V20.5', 'scanner/gain_cal.py', 'def real_drop'),
    ('V20.5.1', 'scanner/gain_cal.py', 'def _reopen'),
    ('V20.5.2', 'scanner/main.py', 'def keep_awake'),
    ('V20.5.3', 'scanner/gain_cal.py', 'def pass_steps'),
    ('V20.6', 'scanner/gain_profile.py', 'def suggest'),
    ('V20.7', 'ui/pyside/qml/InfoTip.qml', 'V20.7'),
    ('V20.7.1', 'ui/pyside/scanner.py', "-> '10.11 18:18'"),
    ('V20.7.2', 'ui/pyside/qml/InfoTip.qml', 'V20.7.2'),
    ('V20.7.3', 'scanner/cleanup.py', 'CAL_FLOOR_MAX_DB'),
    ('V20.8', 'ui/pyside/scanner.py', 'def start_problem'),
]


class Results(object):
    def __init__(self):
        self.rows = []

    def add(self, tid, cat, status, title, detail=''):
        self.rows.append({'id': tid, 'cat': cat, 'status': status, 'title': title, 'detail': detail})
        line = '[%s] %-7s %-8s %s%s' % (tid, cat[:7], status, title, (' - ' + detail) if detail else '')
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode('ascii', 'replace').decode('ascii'), flush=True)

    def by_cat(self, cat):
        return [r for r in self.rows if r['cat'] == cat]

    def summary(self, cat=None):
        rows = self.rows if cat is None else self.by_cat(cat)
        out = {}
        for r in rows:
            out[r['status']] = out.get(r['status'], 0) + 1
        return out

    def worst(self, cat):
        st = [r['status'] for r in self.by_cat(cat)]
        for s in STATUS_ORDER:
            if s in st:
                return s
        return '-'


def wwb_root():
    import wwb_scanner
    return os.path.dirname(os.path.abspath(wwb_scanner.__file__))


def read(p):
    try:
        with io.open(p, encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except (IOError, OSError):
        return ''


def installed_versions(root):
    """[(version, True/False)]"""
    return [(v, m in read(os.path.join(root, f))) for v, f, m in MARKERS]


def latest_manifest(folder):
    """tools/manifest_<release>.json of the newest release (or None)."""
    import glob
    files = glob.glob(os.path.join(folder, 'manifest_V*.json'))
    def key(p):
        v = os.path.basename(p)[len('manifest_V'):-len('.json')]
        return [int(x) if x.isdigit() else 0 for x in v.split('.')]
    return sorted(files, key=key)[-1] if files else os.path.join(folder, 'manifest_none.json')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def make_manifest(root):
    out = {}
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in files:
            if fn.endswith(('.pyc', '.pyo')):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace(os.sep, '/')
            out[rel] = sha256(p)
    return out


def compare_manifest(root, manifest):
    """(missing, changed, extra) relative paths."""
    now = make_manifest(root)
    missing = sorted(k for k in manifest if k not in now)
    changed = sorted(k for k in manifest if k in now and now[k] != manifest[k])
    extra = sorted(k for k in now if k not in manifest)
    return missing, changed, extra


def write_report(outdir, res, title, header_lines, extra_sections=()):
    lines = ['# ' + title, ''] + list(header_lines) + ['']
    tot = res.summary()
    lines += ['Итог: ' + ', '.join('%s %d' % (k, tot[k]) for k in STATUS_ORDER if k in tot), '']
    lines += ['## Сводка по видам тестирования', '', '| Вид | Итог | Проверок | Результаты |', '|---|---|---|---|']
    for cat, name in CATEGORIES:
        s = res.summary(cat)
        if not s:
            lines.append('| %s | - | 0 | не выполнялось |' % name)
            continue
        lines.append('| %s | **%s** | %d | %s |' % (name, res.worst(cat), sum(s.values()),
                                                   ', '.join('%s %d' % (k, s[k]) for k in STATUS_ORDER if k in s)))
    for cat, name in CATEGORIES:
        rows = res.by_cat(cat)
        if not rows:
            continue
        lines += ['', '## ' + name, '', '| № | Итог | Проверка | Подробности |', '|---|---|---|---|']
        for r in rows:
            lines.append('| %s | %s | %s | %s |' % (r['id'], r['status'], r['title'],
                                                   str(r['detail']).replace('|', '/').replace('\n', ' ')))
    for sec in extra_sections:
        lines += [''] + list(sec)
    lines += ['', 'PASS - пройдено; FAIL - ошибка; FINDING - найдена уязвимость/недостаток (не ошибка теста); '
              'WARN - стоит посмотреть; PARTIAL - часть доказательств есть, остальное на ПК/вручную; '
              'MANUAL - проверяется вручную; INFO - для сведения; SKIP - пропущено.']
    with io.open(os.path.join(outdir, 'report.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    with io.open(os.path.join(outdir, 'results.json'), 'w', encoding='utf-8') as fh:
        json.dump({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'python': sys.version.split()[0],
                   'rows': res.rows}, fh, ensure_ascii=False, indent=1)
    return tot


def parse_junit(path):
    """[(file, name, outcome, message)] from a pytest junit xml."""
    import xml.etree.ElementTree as ET
    out = []
    if not os.path.exists(path):
        return out
    root = ET.parse(path).getroot()
    for tc in root.iter('testcase'):
        cls = tc.get('classname', '')
        fn = cls.split('.')[1] if cls.startswith('tests.') else cls.split('.')[0]
        outcome, msg = 'passed', ''
        for child in tc:
            if child.tag in ('failure', 'error'):
                outcome, msg = 'failed', (child.get('message') or '')[:200]
            elif child.tag == 'skipped':
                t = child.get('type', '')
                outcome = 'xfailed' if 'xfail' in t else 'skipped'
                msg = (child.get('message') or '')[:120]
        out.append((fn, tc.get('name', ''), outcome, msg))
    return out
