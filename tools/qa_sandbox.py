# -*- coding: utf-8 -*-
"""All kinds of testing that need no dongle (the dongle simulator replaces it).

    python tools/qa_sandbox.py                 everything, ~6-10 min
    python tools/qa_sandbox.py --quick         smoke, sanity, unit (~2 min)
    options: --no-ui (without the Qt window), --no-coverage, --update-golden,
             --manifest FILE (file list of a release, default tools/manifest_V17.json),
             --out DIR, --baseline results.json (earlier run: new failures = regression)

Works on the developer machine and on the user's PC (Windows, Python 3.7).
Never touches the user's data: HOME / USERPROFILE point to a temporary
folder for the whole run. Report: <out>/report.md (+ results.json, logs).
Categories: smoke, sanity, unit, integration, functional, system,
regression, performance/load, security, acceptance.
"""
from __future__ import print_function

import argparse
import datetime
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = os.path.join(ROOT, 'tests')
TMP_HOME = tempfile.mkdtemp(prefix='qa_sandbox_home_')
os.environ['HOME'] = TMP_HOME            # before wwb_scanner is imported (data folder)
os.environ['USERPROFILE'] = TMP_HOME
sys.path[:0] = [HERE, TESTS]

import qa_common as Q  # noqa: E402

R = Q.Results()
PY = sys.executable

# pytest file -> category; (file, name part) overrides first
UNIT_FILES = {'test_tile_processing', 'test_merge_crop', 'test_csv_format', 'test_scan_objects',
              'test_sample_gen', 'test_dbstore', 'test_scan_timing', 'test_graph_model', 'test_scan_dll'}
OVERRIDES = [
    ('test_v17', ('ripple', 'spurs_removed', 'profile', 'calibration_refused', 'merge_weight'), 'UNIT'),
    ('test_v16', ('clip_ratio_values', 'compare_tool'), 'UNIT'),
    ('test_v18', ('test_B', 'test_G1'), 'UNIT'),
    ('test_qa_stability', ('S8_S10', 'S11'), 'PERFORMANCE'),
]
FILE_CAT = {'test_sim_smoke': 'INTEGRATION', 'test_qa_correctness': 'INTEGRATION', 'test_v16': 'INTEGRATION',
            'test_v17': 'INTEGRATION', 'test_integration': 'INTEGRATION', 'test_qa_formats': 'FUNCTIONAL',
            'test_io': 'FUNCTIONAL', 'test_qa_stability': 'SYSTEM', 'test_golden': 'REGRESSION',
            'test_security': 'SECURITY', 'test_v18': 'FUNCTIONAL', 'test_v181': 'UNIT', 'test_v19_wsm': 'FUNCTIONAL'}


def category_of(fn, name):
    for f, parts, cat in OVERRIDES:
        if fn == f and any(p in name for p in parts):
            return cat
    if fn in UNIT_FILES:
        return 'UNIT'
    return FILE_CAT.get(fn, 'INTEGRATION')


PREFIX = {'UNIT': 'UN', 'INTEGRATION': 'IN', 'FUNCTIONAL': 'FU', 'SYSTEM': 'SY', 'REGRESSION': 'RG',
          'PERFORMANCE': 'PF', 'SECURITY': 'SE'}


def tid_of(cat, fn):
    return 'T.%s.%s' % (PREFIX.get(cat, cat[:2]), fn.replace('test_', ''))


def guard(tid, cat, title):
    def deco(fn):
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                R.add(tid, cat, 'FAIL', title, 'ошибка проверки: %r' % (exc,))
                traceback.print_exc()
                return None
        return wrapper
    return deco


class TmpDir(object):
    def __init__(self):
        self.path = tempfile.mkdtemp(prefix='qa_sb_')
    def join(self, name):
        return os.path.join(self.path, name)
    def __str__(self):
        return self.path


def stub_rtlsdr():
    """The simulator replaces the dongle in-process; a missing librtlsdr must
    not stop the import of the scanner here (the real one is checked apart)."""
    try:
        import rtlsdr  # noqa: F401
    except Exception:
        m = types.ModuleType('rtlsdr')
        m.RtlSdr = object
        sys.modules['rtlsdr'] = m


def sim_scan(start, end, tmp=None, **kw):
    from _pytest.monkeypatch import MonkeyPatch
    import sim
    mp = MonkeyPatch()
    try:
        return sim.run_scan(mp, tmp or TmpDir(), start, end, **kw)
    finally:
        mp.undo()
        os.environ['HOME'] = TMP_HOME
        os.environ['USERPROFILE'] = TMP_HOME


def sub_env():
    env = dict(os.environ)
    env['HOME'] = TMP_HOME
    env['USERPROFILE'] = TMP_HOME
    env['PYTHONPATH'] = os.pathsep.join([p for p in [os.environ.get('PYTHONPATH', '')] if p])
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


# ======================================================================= SMOKE

def smoke(out):
    import compileall
    import importlib
    root = Q.wwb_root()
    t0 = time.time()
    ok = compileall.compile_dir(root, quiet=1, force=True)
    R.add('SM1', 'SMOKE', 'PASS' if ok else 'FAIL', 'все файлы программы компилируются этим Python',
          '%s, %.1f с' % (sys.version.split()[0], time.time() - t0))
    bad = []
    n = 0
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in files:
            if not fn.endswith('.py'):
                continue
            mod = os.path.relpath(os.path.join(dp, fn), os.path.dirname(root))[:-3].replace(os.sep, '.')
            mod = mod[:-9] if mod.endswith('.__init__') else mod
            if mod.endswith('ui.plots') or '.ui.pyside' in mod and not have_pyside():
                continue
            n += 1
            try:
                importlib.import_module(mod)
            except Exception as exc:
                bad.append('%s: %r' % (mod, exc))
    R.add('SM2', 'SMOKE', 'FAIL' if bad else 'PASS', 'все модули импортируются', '%d модулей; %s' % (n, '; '.join(bad[:4]) or 'ошибок нет'))
    sc, sdr, dt = sim_scan(470, 480)
    from wwb_scanner.file_handlers.importers import BaseImporter
    sp = sc.spectrum
    npts = sp.sample_data.size
    R.add('SM3', 'SMOKE', 'PASS' if npts == 401 and sc.error is None else 'FAIL',
          'короткий скан 470-480 МГц (имитатор донгла)', '%d точек за %.1f с, ошибка %r' % (npts, dt, sc.error))
    fmt = []
    for ext in ('csv', 'sdb2', 'sdb3', 'npz'):
        p = os.path.join(out, 'smoke.' + ext)
        try:
            kw = {'frequency_format': 'khz'} if ext == 'csv' else {}
            if ext == 'npz':
                import numpy as np
                np.savez(p, sample_data=sp.sample_data.data)
            else:
                sp.export_to_file(filename=p, **kw)
            back = BaseImporter.import_file(p)
            fmt.append('%s %d' % (ext, back.sample_data.size))
        except Exception as exc:
            fmt.append('%s ОШИБКА %r' % (ext, exc))
    R.add('SM4', 'SMOKE', 'FAIL' if any('ОШИБКА' in s for s in fmt) else 'PASS',
          'экспорт и обратный импорт csv / sdb2 / sdb3 / npz', ', '.join(fmt))


def have_pyside():
    try:
        import PySide2  # noqa: F401
        return True
    except Exception:
        return False


def smoke_window(out):
    if not have_pyside():
        R.add('SM5', 'SMOKE', 'SKIP', 'окно программы загружается', 'PySide2 нет')
        return
    env = sub_env()
    env['QA_UI_ONLY_U1'] = '1'
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    t0 = time.time()
    p = subprocess.run([PY, os.path.join(HERE, 'qa_ui.py'), '470', '480'], env=env, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    txt = p.stdout.decode('utf-8', 'replace')
    m = re.search(r'^U1 +(OK|FAIL) +(.*)$', txt, re.M)
    R.add('SM5', 'SMOKE', 'PASS' if m and m.group(1) == 'OK' else 'FAIL', 'окно программы загружается (Qt, без экрана)',
          (m.group(2)[:150] if m else txt[-200:]) + '; %.0f с' % (time.time() - t0))


# ====================================================================== SANITY

def sanity(args):
    root = Q.wwb_root()
    vers = Q.installed_versions(root)
    miss = [v for v, ok in vers if not ok]
    have = [v for v, ok in vers if ok]
    R.add('SA1', 'SANITY', 'FAIL' if miss else 'PASS', 'установлены все версии V9 ... V17',
          'есть: %s%s' % (' '.join(have), ('; НЕТ: ' + ' '.join(miss)) if miss else ''))
    man = args.manifest or Q.latest_manifest(HERE)
    if os.path.exists(man):
        with io.open(man, encoding='utf-8') as fh:
            m = json.load(fh)
        missing, changed, extra = Q.compare_manifest(root, m['files'])
        st = 'PASS' if not (missing or changed) else 'WARN'
        if extra and st == 'PASS':
            st = 'INFO'
        R.add('SA2', 'SANITY', st, 'файлы совпадают с выпуском %s (SHA-256)' % m.get('release', '?'),
              'нет %d %s; изменены %d %s; лишние %d %s' % (len(missing), missing[:4], len(changed), changed[:6],
                                                          len(extra), extra[:4]))
    else:
        R.add('SA2', 'SANITY', 'SKIP', 'файлы совпадают с выпуском', 'нет %s' % man)
    # Python 3.7 syntax (the user's Python)
    try:
        p = subprocess.run([PY, '-c', 'import sys; from vermin import main; sys.exit(main())',
                            '-t=3.7-', '--no-tips', '--violations', root],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        txt = p.stdout.decode('utf-8', 'replace')
        if 'No module named' in txt and 'vermin' in txt:
            raise ImportError
        viol = [l for l in txt.splitlines() if l.strip() and not l.startswith(
            ('Detecting', 'Analyzing', 'Minimum required', 'Incompatible', 'Tips'))]
        R.add('SA3', 'SANITY', 'PASS' if p.returncode == 0 else 'FAIL', 'код совместим с Python 3.7 (vermin)',
              '; '.join(viol[:4]) or 'нарушений нет')
    except (ImportError, OSError, subprocess.SubprocessError):
        R.add('SA3', 'SANITY', 'INFO', 'код совместим с Python 3.7', 'vermin не установлен; проверено компиляцией (SM1) на %s'
              % sys.version.split()[0])
    try:
        p = subprocess.run([PY, '-m', 'pyflakes', root], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        txt = p.stdout.decode('utf-8', 'replace')
        if 'No module named pyflakes' in txt:
            raise ImportError
        und = [l for l in txt.splitlines() if 'undefined name' in l]
        R.add('SA4', 'SANITY', 'FAIL' if und else 'PASS', 'нет неопределённых имён (pyflakes)',
              '; '.join(os.path.relpath(u, root) if os.path.isabs(u.split(':')[0]) else u for u in und[:4]) or
              '%d прочих замечаний (неиспользуемые импорты и т.п.)' % len(txt.splitlines()))
    except (ImportError, OSError, subprocess.SubprocessError):
        R.add('SA4', 'SANITY', 'SKIP', 'нет неопределённых имён', 'pyflakes не установлен')
    from wwb_scanner.scanner import config
    pc, sc = config.ProcessingConfig(), config.SamplingConfig()
    lib_off = not any(pc.get(k) for k in ('clean_ripple', 'clean_spurs', 'clean_floor')) and not sc.get('auto_gain')
    qml = Q.read(os.path.join(root, 'ui', 'pyside', 'qml', 'ScanConfig.qml'))
    ui_on = all(re.search(r'\b%s: true' % k, qml) for k in ('cleanRipple', 'cleanSpurs', 'cleanFloor', 'autoGain'))
    R.add('SA5', 'SANITY', 'PASS' if lib_off and ui_on else 'WARN', 'настройки по умолчанию V17',
          'в библиотеке очистка/автоусиление выключены: %s; в окне включены: %s' % (lib_off, ui_on))
    deps = []
    for mod in ('numpy', 'scipy', 'tinydb', 'PySide2', 'jsonfactory'):
        try:
            m = __import__(mod)
            deps.append('%s %s' % (mod, getattr(m, '__version__', '?')))
        except Exception:
            deps.append('%s НЕТ' % mod)
    import pyexpat
    deps.append(pyexpat.EXPAT_VERSION)
    R.add('SA6', 'SANITY', 'INFO', 'версии библиотек', ', '.join(deps))


# ============================================================ PYTEST (unit .. security)

def cov_cmd(out, cov_src):
    rc = os.path.join(out, 'coveragerc')
    if not os.path.exists(rc):
        with io.open(rc, 'w', encoding='utf-8') as fh:
            fh.write('[run]\nsource = %s\nparallel = True\ndata_file = %s\n' % (
                cov_src, os.path.join(out, '.coverage')))
    return ['-m', 'coverage', 'run', '--rcfile=' + rc]


def run_pytest(out, args, cov_src):
    junit = os.path.join(out, 'junit.xml')
    cmd = [PY]
    if cov_src:
        cmd += cov_cmd(out, cov_src)
    cmd += ['-m', 'pytest', '-q', '-p', 'no:warnings', '-p', 'no:cacheprovider', '--junitxml=' + junit,
            '-o', 'junit_family=xunit1', TESTS]
    if args.quick:
        cmd += ['-k', 'not golden and not qa_stability and not qa_correctness']
    print('\n=== pytest: %s' % ' '.join(cmd[1:]), flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ROOT, env=sub_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600)
    txt = p.stdout.decode('utf-8', 'replace')
    with io.open(os.path.join(out, 'pytest.txt'), 'w', encoding='utf-8') as fh:
        fh.write(txt)
    cases = Q.parse_junit(junit)
    dt = time.time() - t0
    by = {}
    for fn, name, outcome, msg in cases:
        cat = category_of(fn, name)
        d = by.setdefault((cat, fn), {'passed': 0, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'fails': []})
        d[outcome] = d.get(outcome, 0) + 1
        if outcome == 'failed':
            d['fails'].append('%s: %s' % (name, msg))
    n = 0
    for (cat, fn), d in sorted(by.items()):
        n += 1
        st = 'FAIL' if d['failed'] else ('SKIP' if not d['passed'] else 'PASS')
        R.add(tid_of(cat, fn), cat, st, 'автотесты %s' % fn,
              'прошли %d, упали %d, пропущены %d%s%s' % (d['passed'], d['failed'], d['skipped'],
                                                     (', ожидаемо падают %d' % d['xfailed']) if d['xfailed'] else '',
                                                     ('; ' + ' | '.join(d['fails'][:3])) if d['fails'] else ''))
    tot = {}
    for _, _, o, _ in cases:
        tot[o] = tot.get(o, 0) + 1
    return cases, tot, dt


# ================================================================== FUNCTIONAL UI

def run_ui(out, cov_src):
    if not have_pyside():
        R.add('U', 'FUNCTIONAL', 'SKIP', 'окно программы (U1-U23)', 'PySide2 нет')
        return None
    env = sub_env()
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    env['QA_USB_DELAY'] = '1'
    cmd = [PY]
    if cov_src:
        cmd += cov_cmd(out, cov_src)
    cmd += [os.path.join(HERE, 'qa_ui.py'), '470', '560']
    t0 = time.time()
    try:
        p = subprocess.run(cmd, env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
        txt = p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired as exc:
        txt = (exc.output or b'').decode('utf-8', 'replace') + '\nTIMEOUT'
    with io.open(os.path.join(out, 'ui.txt'), 'w', encoding='utf-8') as fh:
        fh.write(txt)
    checks = re.findall(r'^(U\d+b?) +(OK|FAIL) +(.*)$', txt, re.M)
    if not checks:
        R.add('U', 'FUNCTIONAL', 'FAIL', 'окно программы (U1-U23)', 'окно не запустилось, см. ui.txt')
        return None
    perf = {'U3'}
    for uid, ok, det in checks:
        cat = 'PERFORMANCE' if uid in perf else 'FUNCTIONAL'
        R.add(uid, cat, 'PASS' if ok == 'OK' else 'FAIL', 'окно: ' + UI_TITLES.get(uid, uid), det[:220])
    R.add('U', 'FUNCTIONAL', 'INFO', 'окно программы, всего', '%d проверок за %.0f с' % (len(checks), time.time() - t0))
    return dict((u, (ok, d)) for u, ok, d in checks)


UI_TITLES = {
    'U1': 'загрузка без ошибок QML', 'U1b': 'элементы окна на месте', 'U2': 'скан из окна',
    'U3': 'окно не замирает во время скана', 'U4': 'загрузка большого CSV', 'U5': 'повторный скан',
    'U6': 'экспорт через график', 'U7': 'все точки на графике', 'U8': 'без анимации графика',
    'U9': 'File > Export, все форматы', 'U10': 'строка сообщений и окно ошибки', 'U11': 'метки спуров',
    'U12': 'пресеты диапазонов', 'U13': 'время до конца скана', 'U14': 'блок Clean scan в настройках',
    'U15': 'скан очищен, сырые данные сохранены', 'U16': 'калибровка из окна, затем выровненный скан',
    'U17': 'окно Bands: системы и страна', 'U18': 'выбор диапазонов передан в скан, полоса правил на графике',
    'U19': 'скан нескольких диапазонов из окна, промежутки пустые', 'U20': 'возврат к одному диапазону',
    'U21': 'повреждённый файл: сообщение в окне, окно работает', 'U22': 'сглаживание короткого скана не вешает окно',
    'U23': 'View: полосы потери и метки образов выключаются и сохраняются'}


# ======================================================================= SYSTEM

def realistic_air(antenna=True):
    import sim
    a = sim.Air(noise=0.01, seed=11)
    for k in range(2, 62):
        a.tone(28.8e6 * k, 0.003 if k < 30 else 0.0015)
    if antenna:
        for f in (88.4, 91.3, 95.1, 96.7, 100.6, 104.9):
            a.tone(f * 1e6, 0.05)
        for lo in (474.19, 490.19, 554.19, 698.19):
            a.band(lo * 1e6, (lo + 7.62) * 1e6, 0.02)
        a.tone(526.0e6, 1.6).tone(534.4e6, 0.05).tone(541.6e6, 0.05)
        a.band(758e6, 768e6, 0.03)                       # LTE 800
    return a


@guard('SY1', 'SYSTEM', 'полный рабочий цикл')
def system(out, perf):
    import numpy as np
    import tracemalloc
    from wwb_scanner.scanner import cleanup as C
    from wwb_scanner.file_handlers.importers import BaseImporter
    clean = {'clean_ripple': True, 'clean_spurs': True, 'clean_floor': True}
    log = io.StringIO()
    h = logging.StreamHandler(log)
    h.setLevel(logging.ERROR)
    logging.getLogger('wwb_scanner').addHandler(h)
    steps = []
    # 1. calibration scan without antenna, full range
    home = TmpDir()          # calibration and scans share one data folder
    tracemalloc.start()
    t0 = time.time()
    cal, _, dt_cal = sim_scan(31, 1766, tmp=home, gain=29.7, sdr_kwargs={'air': realistic_air(False), 'gain_affects': True})
    peak_cal = tracemalloc.get_traced_memory()[1] / 1e6
    tracemalloc.stop()
    f, y = cal.spectrum.sample_data.frequency, cal.spectrum.sample_data['dbFS']
    tiles = len(cal.sample_collection.sample_sets)
    perf['full_scan_s'], perf['tiles'], perf['peak_mb'] = dt_cal, tiles, peak_cal
    os.environ['HOME'] = os.environ['USERPROFILE'] = home.path
    t1 = time.time()
    ok, text = C.calibrate(np.asarray(f, float), np.asarray(y, float), 31.0, cal.step_size,
                           dict(gain=float(cal.gain), sample_rate=float(cal.sample_rate), step_mhz=float(cal.step_size)))
    perf['calibrate_s'] = time.time() - t1
    steps.append('калибровка: %s' % ('сохранена' if ok else 'НЕТ: ' + text))
    # 2. working scan with antenna: auto gain + clean scan
    s, sdr, dt = sim_scan(31, 1766, tmp=home, gain=29.7, auto_gain=True, clean=clean,
                          sdr_kwargs={'air': realistic_air(True), 'gain_affects': True})
    info = s.spectrum.cleanup_info or {}
    coll = s.sample_collection
    steps.append('скан %.0f с: спуров убрано %d, пол выровнен %s, перемерено плиток %d, осталось перегруженных %d'
                 % (dt, len(info.get('spurs_removed') or []), info.get('flatten'), len(getattr(coll, 'remeasured', [])),
                    sum(1 for x in coll.sample_sets.values() if x.clip_ratio >= 1e-3)))
    # 3. files for WWB and back
    sp = s.spectrum
    fmt = []
    for ext in ('csv', 'sdb2', 'sdb3'):
        p = os.path.join(out, 'system_scan.' + ext)
        t1 = time.time()
        sp.export_to_file(filename=p, **({'frequency_format': 'khz'} if ext == 'csv' else {}))
        te = time.time() - t1
        t1 = time.time()
        back = BaseImporter.import_file(p)
        ti = time.time() - t1
        perf['export_%s_s' % ext], perf['import_%s_s' % ext] = te, ti
        fmt.append('%s %d т.' % (ext, back.sample_data.size))
    steps.append('файлы: ' + ', '.join(fmt))
    # 4. compare tool against itself (WWB comparison path)
    import compare_wwb
    txt, _ = compare_wwb.compare(os.path.join(out, 'system_scan.csv'), os.path.join(out, 'system_scan.sdb3'))
    same = 'shifted by +0 kHz' in txt
    steps.append('сравнение csv/sdb3: сдвиг 0 %s' % same)
    # 5. history
    steps.append('сохранён в историю: %s' % (s.spectrum.eid is not None))
    logging.getLogger('wwb_scanner').removeHandler(h)
    errs = [l for l in log.getvalue().splitlines() if l.strip()]
    good = (ok and info.get('flatten') and len(info.get('spurs_removed') or []) >= 25 and same
            and all('т.' in x for x in fmt) and not errs and s.error is None)
    R.add('SY1', 'SYSTEM', 'PASS' if good else 'FAIL',
          'полный цикл: калибровка -> скан 31-1766 (автоусиление + очистка) -> файлы WWB -> импорт -> сравнение -> история',
          '; '.join(steps) + ('; ошибки в логе: %s' % errs[:2] if errs else ''))
    return s


# ================================================================= PERFORMANCE

def performance(out, perf, quick):
    import numpy as np
    from wwb_scanner.scanner import cleanup as C
    # P1 one tile
    import sim
    from wwb_scanner.scanner.sample_processing import SampleSet
    sc, _, _ = sim_scan(470, 471)
    ss = list(sc.sample_collection.sample_sets.values())[0]
    rng = np.random.RandomState(0)
    times = []
    for _ in range(30):
        ss.raw = (rng.randn(40, 2048) + 1j * rng.randn(40, 2048)) * 0.01
        t0 = time.perf_counter()
        ss.process_samples()
        times.append((time.perf_counter() - t0) * 1000)
    tile_ms = float(np.median(times))
    R.add('PF1', 'PERFORMANCE', 'PASS' if tile_ms < 30 else 'WARN', 'расчёт одной плитки (40 x 2048, окно 96)',
          '%.1f мс (норма < 30; на донгле плитка ~78 мс уходит на USB)' % tile_ms)
    if 'full_scan_s' in perf:
        per = perf['full_scan_s'] / perf['tiles'] * 1000
        R.add('PF2', 'PERFORMANCE', 'PASS' if per < 40 else 'WARN', 'полный скан 31-1766 без времени USB',
              '%.0f с, %.1f мс на плитку, %d плиток (это часть Python; на донгле +USB)' % (perf['full_scan_s'], per, perf['tiles']))
        R.add('PF3', 'PERFORMANCE', 'PASS' if perf['peak_mb'] < 300 else 'WARN', 'память полного скана (Python)',
              'пик %.0f МБ (норма < 300)' % perf['peak_mb'])
        ex = ', '.join('%s %.2f/%.2f с' % (e, perf['export_%s_s' % e], perf['import_%s_s' % e]) for e in ('csv', 'sdb2', 'sdb3'))
        slow = max(perf['export_%s_s' % e] + perf['import_%s_s' % e] for e in ('csv', 'sdb2', 'sdb3'))
        R.add('PF4', 'PERFORMANCE', 'PASS' if slow < 10 else 'WARN', 'экспорт / импорт 69 400 точек', ex)
    # P5 clean scan time on 69 400 points
    f = np.round(np.arange(31.0, 1766.0001, 0.025), 4)
    y = -101 + rng.randn(f.size) * 0.2
    t0 = time.time()
    prof = C.build_profile(f, y, 31.0, 31)
    t_prof = time.time() - t0
    t0 = time.time()
    C.clean(f, y, 31.0, 31, profile=prof)
    t_clean = time.time() - t0
    R.add('PF5', 'PERFORMANCE', 'PASS' if t_clean < 2 and t_prof < 5 else 'WARN', 'чистый скан на 69 400 точках',
          'очистка %.2f с, построение калибровки %.2f с' % (t_clean, t_prof))
    if quick:
        return
    # P6 history with many scans
    from _pytest.monkeypatch import MonkeyPatch
    from wwb_scanner.utils import dbstore
    from wwb_scanner.scan_objects import Spectrum
    mp = MonkeyPatch()
    d = TmpDir()
    try:
        mp.setattr(dbstore.DBStore, 'DB_PATH', d.join('db.json'))
        mp.setattr(dbstore.DBStore, 'SCAN_DB_PATH', d.join('scan_index.json'))
        mp.setattr(dbstore.db_store, '_db', None)
        mp.setattr(dbstore.db_store, '_scan_db', None)
        fr = np.round(np.arange(470.0, 800.0001, 0.025), 4)
        save_t = []
        for i in range(100):
            sp = Spectrum()
            sp.add_sample_set(frequency=fr, magnitude=10 ** ((-100 + rng.randn(fr.size)) / 10.), force_lower_freq=True)
            t0 = time.time()
            sp.save_to_dbstore()
            save_t.append(time.time() - t0)
        t0 = time.time()
        allscans = dbstore.db_store.get_all_scans()
        t_list = time.time() - t0
        t0 = time.time()
        eid = sorted(allscans)[len(allscans) // 2]
        Spectrum.from_dbstore(eid=eid)
        t_load = time.time() - t0
        R.add('PF6', 'PERFORMANCE', 'PASS' if np.mean(save_t[-10:]) < 1.0 and t_list < 2 else 'WARN',
              'история: 100 сканов 470-800', 'сохранение 1-го %.2f с, последних 10 в среднем %.2f с; список %.2f с; открыть один %.2f с'
              % (save_t[0], np.mean(save_t[-10:]), t_list, t_load))
    finally:
        mp.undo()
    # P7 load: 30 scans one after another
    import tracemalloc
    tracemalloc.start()
    th0 = threading.active_count()
    ts, mem = [], []
    for i in range(30):
        _, _, dt = sim_scan(470, 500)
        ts.append(dt)
        mem.append(tracemalloc.get_traced_memory()[0] / 1e6)
    tracemalloc.stop()
    grow = mem[-1] - mem[4]
    slow = np.mean(ts[-5:]) / max(1e-6, np.mean(ts[:5]))
    th = threading.active_count() - th0
    R.add('PF7', 'PERFORMANCE', 'PASS' if grow < 30 and slow < 1.5 and th <= 2 else 'WARN',
          'нагрузка: 30 сканов подряд', 'время скана %.2f -> %.2f с (x%.2f), память Python +%.1f МБ, потоков +%d'
          % (np.mean(ts[:5]), np.mean(ts[-5:]), slow, grow, th))


# ===================================================================== SECURITY

def security(out):
    import base64
    import pickle
    import pyexpat
    from wwb_scanner.file_handlers.importers import BaseImporter
    d = TmpDir()
    # SE1: pickled data in the scan database JSON runs code when the database is read
    from wwb_scanner.utils import numpyjson
    marker = d.join('PICKLE_RAN_CODE')

    class P(object):
        def __reduce__(self):
            return (os.makedirs, (marker,))
    payload = base64.b64encode(pickle.dumps(P())).decode('ascii')
    p = d.join('evil_scan_index.json')
    with open(p, 'w') as fh:
        json.dump({'scans_performed': {'1': {'name': 'x', 'sample_data': {'__ndarray__': payload}}}}, fh)
    try:
        with open(p) as fh:
            numpyjson.load(fh)
    except Exception:
        pass
    ran = os.path.isdir(marker)
    R.add('SE1', 'SECURITY', 'FINDING' if ran else 'PASS', 'база сканов (JSON) не выполняет код при чтении',
          ('НАЙДЕНО (средняя): numpyjson.decode делает pickle.loads данных из db.json / scan_index.json / '
           'scan_db.json; подложенный файл базы (или migrate_scan_db --src чужой файл) выполняет любой код. '
           'Исправление: безопасная распаковка только массивов numpy' if ran else 'код не выполнен'))
    # SE2: sdb2 with a DTD and entities (XML bomb) is accepted?
    lvl = 5
    bomb = '<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY a0 "0123456789012345678">' + ''.join(
        '<!ENTITY a%d "%s">' % (i, ('&a%d;' % (i - 1)) * 10) for i in range(1, lvl + 1)) + ']>\n<r>&a%d;</r>' % lvl
    p = d.join('bomb.sdb2')
    with open(p, 'w') as fh:
        fh.write(bomb)
    t0 = time.time()
    try:
        BaseImporter.import_file(p)
        res = 'прочитан'
    except Exception as exc:
        res = type(exc).__name__ + ': ' + str(exc)[:80]
    dt = time.time() - t0
    expanded = 'DTD' not in res and 'amplification' not in res
    ver = tuple(int(x) for x in re.findall(r'\d+', pyexpat.EXPAT_VERSION)[:3])
    old = ver < (2, 4, 1)
    st = 'FINDING' if expanded else 'PASS'
    R.add('SE2', 'SECURITY', st, 'файл .sdb2 с DTD / сущностями (XML-бомба) не принимается',
          '%s; бомба 1,9 МБ: %s за %.2f с; %s' % (
              pyexpat.EXPAT_VERSION, res, dt,
              ('НАЙДЕНО (%s): импорт не отклоняет DTD; %s' % ('средняя' if old else 'низкая',
               'у этого Python expat < 2.4.1 без защиты - файл побольше повесит программу и съест память'
               if old else 'expat >= 2.4.1 ограничивает раздувание')) if expanded else 'файл с DTD отклонён'))
    # SE3: numpy refuses pickles in .npy/.npz (CVE-2019-6446: numpy < 1.16.3)
    import numpy as np
    nv = tuple(int(x) for x in re.findall(r'\d+', np.__version__)[:3])
    R.add('SE3', 'SECURITY', 'PASS' if nv >= (1, 16, 3) else 'FINDING', 'numpy не распаковывает pickle из .npz по умолчанию',
          'numpy %s (нужно >= 1.16.3, CVE-2019-6446)' % np.__version__)
    # SE4: bandit static analysis
    try:
        p = subprocess.run([PY, '-m', 'bandit', '-q', '-r', Q.wwb_root(), '-f', 'json', '-ll'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        txt = p.stdout.decode('utf-8', 'replace')
        if not txt.strip().startswith('{'):
            raise ImportError
        data = json.loads(txt)
        issues = data.get('results', [])
        kinds = {}
        for i in issues:
            k = '%s %s' % (i['test_id'], i['issue_severity'])
            kinds[k] = kinds.get(k, 0) + 1
        hi = sum(1 for i in issues if i['issue_severity'] == 'HIGH')
        R.add('SE4', 'SECURITY', 'WARN' if hi else 'INFO', 'статический анализ bandit (средние и высокие)',
              '%d замечаний: %s' % (len(issues), ', '.join('%s x%d' % kv for kv in sorted(kinds.items())) or 'нет'))
        with io.open(os.path.join(out, 'bandit.json'), 'w', encoding='utf-8') as fh:
            fh.write(txt)
    except (ImportError, OSError, subprocess.SubprocessError, ValueError):
        R.add('SE4', 'SECURITY', 'SKIP', 'статический анализ bandit', 'bandit не установлен (python -m pip install bandit)')
    # SE5: which libraries could be swapped: pyrtlsdr version (0.4+ needs a DLL function we do not have)
    try:
        from importlib.machinery import PathFinder
        spec = PathFinder.find_spec('rtlsdr')
        loc = list(spec.submodule_search_locations)[0] if spec else None
        ver = '?'
        try:
            try:
                from importlib.metadata import version as _v  # novm
            except ImportError:                       # Python 3.7
                from pkg_resources import get_distribution
                _v = lambda n: get_distribution(n).version
            ver = _v('pyrtlsdr')
        except Exception:
            pass
        vt = tuple(int(x) for x in re.findall(r'\d+', ver)[:2]) if ver != '?' else ()
        st = 'INFO' if not loc else ('WARN' if vt and vt >= (0, 4) else 'PASS')
        R.add('SE5', 'SECURITY', st, 'зависимости: pyrtlsdr < 0.4 из ожидаемого места',
              'pyrtlsdr %s, %s%s' % (ver, loc, '; 0.4+ требует функцию DLL, которой нет в scanner-v4' if st == 'WARN' else ''))
    except Exception as exc:
        R.add('SE5', 'SECURITY', 'INFO', 'зависимости: pyrtlsdr', repr(exc))


# =================================================================== ACCEPTANCE

ACCEPTANCE = [
    ('AC1', 'Скорость: 470-800 МГц <= 40 с, полный 31-1766 <= 180 с (на донгле)', [], 'H2a, H3a на ПК'),
    ('AC2', 'Файлы csv / sdb2 / sdb3 открываются в WWB 7.9 и совпадают с отображаемым', ['T.FU.qa_formats', 'T.UN.csv_format', 'SM4'],
     'открыть в WWB вручную (ПК)'),
    ('AC3', 'Повторяемость: тихие точки двух сканов +-0,5 дБ', ['T.IN.qa_correctness'], 'H2c на ПК'),
    ('AC4', 'Нет зависаний; отказ / выдёргивание донгла не вешает окно', ['T.SY.qa_stability', 'U10'], 'H7 на ПК'),
    ('AC5', 'Чистый скан убирает свои спуры и рябь, не трогая сигналы', ['T.IN.v17', 'T.UN.v17', 'T.IN.integration', 'SY1'], 'H8e, H11 на ПК'),
    ('AC6', 'Перегрузка обнаруживается, уровень исправляется автоусилением; рядом с сильной несущей нет плато и ложных пиков, потеря чувствительности названа (V20)', ['T.IN.v16', 'T.IN.integration', 'T.IN.v20', 'T.IN.v201', 'T.IN.v202'], 'H9, H10 на ПК; скан с микрофоном у антенны'),
    ('AC7', 'Окно отзывчиво: замирание < 0,5 с, есть время до конца скана', ['U3', 'U13'], ''),
    ('AC8', 'История сохраняет и открывает сканы (очищенные и сырые)', ['T.IN.integration', 'PF6'], ''),
    ('AC9', 'Калибровка донгла из окна, затем выровненный скан', ['U16', 'T.IN.integration'], 'H8d на ПК (без антенны)'),
    ('AC10', 'Диапазоны: системы Shure/Sennheiser и правила страны, скан только их диапазонов, в промежутках нет '
             'выдуманных данных (окно, график, экспорт)', ['U17', 'U18', 'U19', 'U20', 'T.FU.v18', 'T.UN.v18'],
     'PW.U19 на ПК; экспорт открыть в WWB'),
    ('AC11', 'Надёжность (аудит): ошибки видны в окне, окно не застревает в "идёт скан", донгл не '
             'открывается в потоке окна, зависший USB не открывается повторно',
     ['U21', 'U22', 'T.UN.v181'], 'PW.U21 на ПК; --unplug'),
]


def acceptance():
    status = {r['id']: r['status'] for r in R.rows}
    for aid, title, ev, pc in ACCEPTANCE:
        got = [(e, status.get(e)) for e in ev]
        if any(s == 'FAIL' for _, s in got):
            st = 'FAIL'
        elif not ev:
            st = 'MANUAL'
        elif any(s is None for _, s in got):
            st = 'PARTIAL'
        else:
            st = 'PARTIAL' if pc else 'PASS'
        R.add(aid, 'ACCEPTANCE', st, title, 'песочница: %s%s' % (
            ', '.join('%s %s' % (e, s or 'нет') for e, s in got) or 'нет данных',
            ('; осталось: ' + pc) if pc else ''))


# ===================================================================== REGRESSION

def regression(out, tot, args):
    total = sum(tot.values())
    R.add('RG1', 'REGRESSION', 'FAIL' if tot.get('failed') else 'PASS', 'все автотесты (повтор после каждого изменения)',
          'всего %d: прошли %d, упали %d, пропущены %d, ожидаемо падают %d' % (
              total, tot.get('passed', 0), tot.get('failed', 0), tot.get('skipped', 0), tot.get('xfailed', 0)))
    base = args.baseline
    if not base:
        parent = os.path.dirname(out)
        old = sorted(d for d in os.listdir(parent) if d.startswith('qa_sandbox_report_') and
                     os.path.join(parent, d) != out and os.path.exists(os.path.join(parent, d, 'results.json')))
        base = os.path.join(parent, old[-1], 'results.json') if old else None
    if base and os.path.exists(base):
        with io.open(base, encoding='utf-8') as fh:
            prev = {r['id']: r['status'] for r in json.load(fh)['rows']}
        now = {r['id']: r['status'] for r in R.rows}
        newfail = [k for k, v in now.items() if v == 'FAIL' and prev.get(k) not in (None, 'FAIL')]
        fixed = [k for k, v in now.items() if v == 'PASS' and prev.get(k) == 'FAIL']
        R.add('RG2', 'REGRESSION', 'FAIL' if newfail else 'PASS', 'сравнение с прошлым прогоном',
              '%s; новые падения: %s; исправлено: %s' % (os.path.basename(os.path.dirname(base)),
                                                       newfail or 'нет', fixed or 'нет'))
    else:
        R.add('RG2', 'REGRESSION', 'INFO', 'сравнение с прошлым прогоном', 'прошлого отчёта нет (будет в следующий раз)')


def update_golden():
    import golden_scenarios as G
    from _pytest.monkeypatch import MonkeyPatch
    data = {'version': 'V17', 'time': time.strftime('%Y-%m-%d %H:%M'), 'scenarios': {}}
    for name in sorted(G.SCENARIOS):
        mp = MonkeyPatch()
        try:
            data['scenarios'][name] = G.metrics(G.run(name, mp, TmpDir()))
        finally:
            mp.undo()
        print('golden %s: %s' % (name, data['scenarios'][name]))
    G.save_golden(data)
    print('saved', G.GOLDEN_PATH)


# ======================================================================= COVERAGE

def coverage_report(out, cov_src):
    if not cov_src:
        return []
    env = sub_env()
    rc = '--rcfile=' + os.path.join(out, 'coveragerc')
    subprocess.run([PY, '-m', 'coverage', 'combine', '-q', rc], cwd=out, env=env)
    p = subprocess.run([PY, '-m', 'coverage', 'report', rc], cwd=out, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    txt = p.stdout.decode('utf-8', 'replace')
    with io.open(os.path.join(out, 'coverage.txt'), 'w', encoding='utf-8') as fh:
        fh.write(txt)
    rows = []
    for line in txt.splitlines():
        m = re.match(r'^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)%$', line.strip())
        if m:
            name = m.group(1).replace('\\', '/')
            name = name[name.find('wwb_scanner/'):] if 'wwb_scanner/' in name else name
            rows.append((name, int(m.group(2)), int(m.group(3)), int(m.group(4))))
    total = [r for r in rows if r[0] == 'TOTAL']
    sec = ['## Покрытие кода тестами (строки)', '', '| Модуль | Строк | Не выполнено | Покрытие |', '|---|---|---|---|']
    for name, st, miss, pc in rows:
        sec.append('| %s | %d | %d | %d %% |' % (name, st, miss, pc))
    if total:
        R.add('COV', 'REGRESSION', 'INFO', 'покрытие кода автотестами + окном', 'всего %d %% строк (%d из %d)' % (
            total[0][3], total[0][1] - total[0][2], total[0][1]))
    return sec


# ========================================================================== MAIN

def main():
    ap = argparse.ArgumentParser(description='All sandbox tests (no dongle)')
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--no-ui', action='store_true')
    ap.add_argument('--no-coverage', action='store_true')
    ap.add_argument('--update-golden', action='store_true')
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--baseline', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--make-manifest', default=None, help='write the file list of the installed wwb_scanner (release)')
    args = ap.parse_args()
    stub_rtlsdr()
    if args.make_manifest:
        with io.open(args.make_manifest, 'w', encoding='utf-8') as fh:
            json.dump({'release': os.path.splitext(os.path.basename(args.make_manifest))[0].replace('manifest_', ''),
                       'files': Q.make_manifest(Q.wwb_root())}, fh, indent=1, sort_keys=True)
        print('written', args.make_manifest)
        return 0
    if args.update_golden:
        update_golden()
        return 0
    started = datetime.datetime.now()
    out = args.out or os.path.join(os.getcwd(), 'qa_sandbox_report_' + started.strftime('%Y%m%d-%H%M%S'))
    if not os.path.isdir(out):
        os.makedirs(out)
    cov_src = None
    if not args.no_coverage and not args.quick:
        try:
            import coverage  # noqa: F401
            cov_src = Q.wwb_root()
        except ImportError:
            print('coverage is not installed: no coverage table (python -m pip install coverage)')
    t_all = time.time()
    print('Отчёт: %s\nwwb_scanner: %s\n' % (out, Q.wwb_root()), flush=True)
    print('=== SMOKE', flush=True)
    smoke(out)
    if not args.no_ui:
        smoke_window(out)
    if any(r['status'] == 'FAIL' for r in R.by_cat('SMOKE')):
        print('Дымовой тест не прошёл: дальше проверять бессмысленно.')
    else:
        print('\n=== SANITY', flush=True)
        sanity(args)
        cases, tot, dt = run_pytest(out, args, cov_src)
        ui = None
        if not args.no_ui and not args.quick:
            print('\n=== FUNCTIONAL: window', flush=True)
            ui = run_ui(out, cov_src)
        perf = {}
        if not args.quick:
            print('\n=== SYSTEM', flush=True)
            system(out, perf)
        print('\n=== PERFORMANCE', flush=True)
        performance(out, perf, args.quick)
        print('\n=== SECURITY', flush=True)
        security(out)
        print('\n=== REGRESSION', flush=True)
        regression(out, tot, args)
        print('\n=== ACCEPTANCE', flush=True)
        acceptance()
    sec = coverage_report(out, cov_src)
    import numpy
    head = ['Дата: %s; длительность %.1f мин; Python %s; numpy %s; %s' % (
        started.strftime('%Y-%m-%d %H:%M'), (time.time() - t_all) / 60., sys.version.split()[0], numpy.__version__,
        'быстрый режим' if args.quick else 'полный режим'), 'Программа: %s' % Q.wwb_root(),
        'Донгл не нужен: везде имитатор (sim.py). Данные пользователя не затрагиваются (временная папка).']
    tot = Q.write_report(out, R, 'Тестирование в песочнице (qa_sandbox.py)', head, [sec] if sec else [])
    print('\nИтог: %s\nОтчёт: %s' % (', '.join('%s %d' % kv for kv in sorted(tot.items())), os.path.join(out, 'report.md')))
    shutil.rmtree(TMP_HOME, ignore_errors=True)
    return 1 if tot.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
