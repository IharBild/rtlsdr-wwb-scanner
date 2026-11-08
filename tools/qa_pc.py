# -*- coding: utf-8 -*-
"""Полная проверка программы и донгла на вашем компьютере (все виды тестирования).

Запуск из папки набора (программа сканера закрыта):
    python qa_pc.py                  стандарт, ~45 мин: дымовой, санити, все автотесты
                                     на вашем Python, донгл с антенной, окно с донглом
    python qa_pc.py --smoke          только дымовой, ~4 мин (после каждой установки)
    python qa_pc.py --no-antenna     антенна откручена, ~12 мин: шум, спуры, калибровка
    Добавки: --quick (без полного скана и серии), --soak 60 (нагрузка 60 мин),
             --uat (приёмочные вопросы: открыть файлы в WWB и т.п.),
             --unplug (выдернуть донгл посреди скана), --gain 33.8
Результат: папка qa_pc_report_<дата> (report.md + подпапки). Заархивируйте и пришлите.
"""
from __future__ import print_function

import argparse
import datetime
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = HERE if os.path.isdir(os.path.join(HERE, 'tests')) else os.path.dirname(HERE)
TOOLS = os.path.join(KIT, 'tools')
QA_HW_PATH = os.path.join(KIT, 'qa_hw.py') if os.path.exists(os.path.join(KIT, 'qa_hw.py')) else os.path.join(TOOLS, 'qa_hw.py')
sys.path[:0] = [KIT, TOOLS]
import qa_common as Q  # noqa: E402

R = Q.Results()
PY = sys.executable

# rtlsdr builds we gave out (SHA-256): anything else is reported
KNOWN_DLL = {
    '2ade1fa9a41e7dbc737ff865b9389e205c85abaa8e94ca96ba48698dcabeba89': 'scanner-v4 x64 (cda6b29)',
    '31bdf1a8fcd969c1aa14854f6fdecc56ad9ea693aace8aca5d14f4738f23429f': 'scanner-v4 x86 (cda6b29)',
}

# qa_hw check -> category of the methodology
HW_CAT = [
    (r'^E', 'SANITY'), (r'^H0$|^H1', 'SMOKE'), (r'^H2a$|^H3a$|^H3b$|^H6', 'PERFORMANCE'),
    (r'^H2[bcde]$|^H4|^H5|^H8b$|^H8d$|^H8e$|^H9$|^H11$', 'FUNCTIONAL'), (r'^H10$', 'INTEGRATION'),
    (r'^H3|^H7|^D1', 'SYSTEM'), (r'^H8[ac]$', 'REGRESSION'), (r'^T', 'REGRESSION'), (r'^U', 'FUNCTIONAL'),
]


def hw_cat(tid):
    for pat, cat in HW_CAT:
        if re.match(pat, tid):
            return cat
    return 'SYSTEM'


def say(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)


def env_utf8(**extra):
    e = dict(os.environ)
    e['PYTHONIOENCODING'] = 'utf-8'
    e['PYTHONUNBUFFERED'] = '1'
    e.update(extra)
    return e


def run_live(cmd, log_path, timeout=None, env=None):
    """Run, show the output live, keep it in a file (also what the DLL prints in C)."""
    with io.open(log_path, 'w', encoding='utf-8') as log:
        p = subprocess.Popen(cmd, cwd=KIT, env=env or env_utf8(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        t0 = time.time()
        for raw in iter(p.stdout.readline, b''):
            line = raw.decode('utf-8', 'replace')
            log.write(line)
            log.flush()
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except Exception:
                pass
            if timeout and time.time() - t0 > timeout:
                p.kill()
                log.write('\nTIMEOUT\n')
                break
        p.wait()
    return io.open(log_path, encoding='utf-8', errors='replace').read()


def run_interactive(cmd):
    """The child talks to the user (Enter prompts): no capture."""
    return subprocess.call(cmd, cwd=KIT, env=env_utf8())


def import_rows(results_json, prefix='', cat_fn=None):
    if not os.path.exists(results_json):
        return 0
    with io.open(results_json, encoding='utf-8') as fh:
        rows = json.load(fh)['rows']
    for r in rows:
        R.add(prefix + r['id'], cat_fn(r['id']) if cat_fn else r['cat'], r['status'], r['title'], r['detail'])
    return len(rows)


def import_md_rows(report_md, prefix, cat_fn):
    n = 0
    for line in Q.read(report_md).splitlines():
        m = re.match(r'^\| (\S+) \| (PASS|FAIL|WARN|INFO|SKIP) \| (.*?) \| (.*) \|$', line)
        if m and m.group(1) != '№':
            R.add(prefix + m.group(1), cat_fn(m.group(1)), m.group(2), m.group(3), m.group(4))
            n += 1
    return n


# ================================================================ PC SMOKE + SANITY

def pc_environment():
    import platform
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    R.add('PC1', 'SMOKE', 'PASS' if sys.version_info[:2] >= (3, 7) else 'FAIL', 'Python и система',
          'Python %s %d-bit, %s' % (sys.version.split()[0], bits, platform.platform()))
    try:
        import wwb_scanner
        root = Q.wwb_root()
    except Exception as exc:
        R.add('PC2', 'SMOKE', 'FAIL', 'программа установлена', repr(exc))
        return False
    vers = Q.installed_versions(root)
    miss = [v for v, ok in vers if not ok]
    R.add('PC2', 'SANITY', 'FAIL' if miss else 'PASS', 'установлены все версии V9 ... V17',
          '%s; %s' % (root, ('НЕТ: ' + ' '.join(miss)) if miss else 'все'))
    man = Q.latest_manifest(TOOLS)
    if os.path.exists(man):
        with io.open(man, encoding='utf-8') as fh:
            m = json.load(fh)
        missing, changed, extra = Q.compare_manifest(root, m['files'])
        R.add('PC3', 'SANITY', 'WARN' if (missing or changed) else ('INFO' if extra else 'PASS'),
              'файлы программы совпадают с выпуском %s (SHA-256)' % m.get('release'),
              'нет %d %s; отличаются %d %s; лишние %d %s%s' % (
                  len(missing), missing[:3], len(changed), changed[:5], len(extra), extra[:6],
                  (' - установка смешана или изменена, переустановите архивы по порядку' if (missing or changed) else '')
                  + (' - лишние файлы не мешают; резервные копии лучше хранить вне папки wwb_scanner' if extra else '')))
    return True


def pc_dll():
    """Which rtlsdr DLL pyrtlsdr loads, is it ours, are there other copies
    that could be loaded instead (search order of Windows)."""
    code = ('import json,sys\nsys.path[:0] = [%r, %r]\nimport qa_hw\n'
            'print("DLLINFO " + json.dumps(qa_hw.loaded_dll()))\n' % (KIT, TOOLS))
    p = subprocess.run([PY, '-c', code], cwd=KIT, env=env_utf8(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=60)
    txt = p.stdout.decode('utf-8', 'replace')
    m = re.search(r'DLLINFO (".*")', txt)
    info = json.loads(m.group(1)) if m else txt.strip()[-200:]
    path = info.split(';')[0].strip() if m else ''
    ok_import = bool(m) and 'не удалось' not in info
    R.add('PC4', 'SMOKE', 'PASS' if ok_import else 'FAIL', 'pyrtlsdr загружает DLL донгла', info[:220])
    if path and os.path.exists(path):
        h = Q.sha256(path)
        known = KNOWN_DLL.get(h)
        R.add('PC5', 'SECURITY', 'PASS' if known else 'WARN', 'загруженная DLL - наша сборка (SHA-256)',
              '%s: %s' % (known or 'НЕИЗВЕСТНАЯ сборка', h[:16]))
    else:
        R.add('PC5', 'SECURITY', 'SKIP', 'загруженная DLL - наша сборка', 'путь DLL не определён')
    # other copies on the search path (cwd, python folder, PATH)
    dirs = [os.getcwd(), os.path.dirname(PY)] + os.environ.get('PATH', '').split(os.pathsep)
    found = {}
    for d in dirs:
        for name in ('rtlsdr.dll', 'librtlsdr.dll'):
            p2 = os.path.join(d, name)
            if d and os.path.isfile(p2):
                try:
                    found[os.path.normcase(os.path.abspath(p2))] = Q.sha256(p2)[:12]
                except OSError:
                    pass
    kinds = set(found.values())
    R.add('PC6', 'SECURITY', 'WARN' if len(kinds) > 1 else 'PASS', 'нет других копий DLL донгла на пути поиска',
          '%d файл(ов), %d разных: %s' % (len(found), len(kinds),
                                         '; '.join('%s %s' % kv for kv in list(found.items())[:5]) or 'не найдено'))


def pc_smoke_scan(out):
    """Real dongle: 10 MHz scan, all export formats, import back."""
    log = os.path.join(out, 'smoke_scan.txt')
    t0 = time.time()
    txt = run_live([PY, '-u', os.path.abspath(__file__), '--child', 'smoke-scan', '--out', out], log, timeout=300)
    m = re.search(r'SMOKE (\{.*\})', txt)
    if not m:
        R.add('PC7', 'SMOKE', 'FAIL', 'донгл: скан 470-480 МГц, экспорт и импорт', 'скан не выполнен, см. smoke_scan.txt')
        return
    d = json.loads(m.group(1))
    ok = d['points'] == 401 and not d['error'] and all(v == 401 for v in d['formats'].values())
    R.add('PC7', 'SMOKE', 'PASS' if ok else 'FAIL', 'донгл: скан 470-480 МГц, экспорт и импорт',
          '%d точек за %.1f с; %s; ошибка %s; сообщения DLL: %s' % (
              d['points'], d['time'], ', '.join('%s %s' % kv for kv in d['formats'].items()), d['error'],
              '; '.join(sorted(set(l.strip() for l in txt.splitlines() if re.search(r'failed|PLL|error', l, re.I))))[:150] or 'нет'))


def child_smoke_scan(out):
    import qa_hw
    lab = qa_hw.Lab(out)
    s = lab.scan('pc_smoke', 470, 480, timeout=120)
    from wwb_scanner.file_handlers.importers import BaseImporter
    import numpy as np
    sp = s['scanner'].spectrum
    fm = {}
    for ext in ('csv', 'sdb2', 'sdb3', 'npz'):
        p = os.path.join(out, 'smoke_scan.' + ext)
        try:
            if ext == 'npz':
                np.savez(p, sample_data=sp.sample_data.data)
            else:
                sp.export_to_file(filename=p, **({'frequency_format': 'khz'} if ext == 'csv' else {}))
            fm[ext] = BaseImporter.import_file(p).sample_data.size
        except Exception as exc:
            fm[ext] = 'ошибка %r' % (exc,)
    print('SMOKE ' + json.dumps({'points': int(s['f'].size), 'time': s['time'], 'error': repr(s['error']) if s['error'] else '',
                                 'formats': fm}), flush=True)


def pc_window(out, real, gain, only_u1=False, start=470, end=600):
    tag = 'окно с донглом' if real else 'окно с имитатором'
    env = env_utf8(QT_QPA_PLATFORM='offscreen', QA_UI_GAIN=str(gain))
    if real:
        env['QA_UI_REAL'] = '1'
    if only_u1:
        env['QA_UI_ONLY_U1'] = '1'
    log = os.path.join(out, 'ui_real.txt' if real else 'ui_smoke.txt')
    t0 = time.time()
    txt = run_live([PY, '-u', os.path.join(TOOLS, 'qa_ui.py'), str(start), str(end)], log, timeout=900, env=env)
    checks = re.findall(r'^(U\d+b?) +(OK|FAIL) +(.*)$', txt, re.M)
    if not checks:
        R.add('PW', 'SMOKE' if only_u1 else 'SYSTEM', 'FAIL', tag + ': запуск', 'окно не запустилось, см. %s' % os.path.basename(log))
        return
    if only_u1:
        u1 = [c for c in checks if c[0] == 'U1']
        R.add('PC8', 'SMOKE', 'PASS' if u1 and u1[0][1] == 'OK' else 'FAIL', 'окно программы загружается',
              (u1[0][2][:150] if u1 else '') + '; %.0f с' % (time.time() - t0))
        return
    for uid, ok, det in checks:
        R.add('PW.' + uid, 'SYSTEM', 'PASS' if ok == 'OK' else 'FAIL', tag + ': ' + UI_T.get(uid, uid), det[:200])


UI_T = {'U1': 'загрузка', 'U1b': 'элементы', 'U2': 'скан из окна', 'U3': 'нет замирания', 'U4': 'большой CSV',
        'U5': 'повторный скан', 'U6': 'экспорт', 'U7': 'точки на графике', 'U8': 'без анимации',
        'U9': 'File > Export', 'U10': 'сообщения и ошибки', 'U11': 'метки спуров', 'U12': 'пресеты',
        'U13': 'время до конца', 'U14': 'настройки Clean scan', 'U15': 'скан очищен', 'U16': 'калибровка',
        'U17': 'окно Bands', 'U18': 'выбор диапазонов', 'U19': 'скан нескольких диапазонов', 'U20': 'снова один диапазон',
        'U21': 'повреждённый файл', 'U22': 'короткий скан со сглаживанием'}


# ======================================================================= SOAK

def pc_soak(out, minutes, simulate=False):
    log = os.path.join(out, 'soak.txt')
    txt = run_live([PY, '-u', os.path.abspath(__file__), '--child', 'soak', '--minutes', str(minutes), '--out', out]
                   + (['--simulate'] if simulate else []), log, timeout=minutes * 60 + 600)
    m = re.search(r'SOAK (\{.*\})', txt)
    if not m:
        R.add('PL1', 'PERFORMANCE', 'FAIL', 'длительная нагрузка %d мин' % minutes, 'не завершилась, см. soak.txt')
        return
    d = json.loads(m.group(1))
    drift = d['t_last'] / max(1e-6, d['t_first'])
    R.add('PL1', 'PERFORMANCE', 'PASS' if drift < 1.15 and not d['errors'] else 'WARN',
          'длительная нагрузка: %d сканов 470-800 за %.0f мин' % (d['n'], d['minutes']),
          'время скана %.1f -> %.1f с (x%.2f); ошибок %d; PLL не захвачен %s' % (
              d['t_first'], d['t_last'], drift, d['errors'], d['pll_unlocked']))
    R.add('PL2', 'PERFORMANCE', 'PASS' if d['rss_growth_mb'] < 50 and d['threads_growth'] <= 2 else 'WARN',
          'память и потоки за время нагрузки',
          'память процесса %.0f -> %.0f МБ (+%.0f); потоков %+d' % (d['rss_first'], d['rss_last'], d['rss_growth_mb'],
                                                                 d['threads_growth']))


def rss_mb():
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                            ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            h = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb)
            return c.WorkingSetSize / 1e6
        with open('/proc/self/status') as fh:
            for line in fh:
                if line.startswith('VmRSS'):
                    return int(line.split()[1]) / 1e3
    except Exception:
        pass
    return 0.0


def child_soak(out, minutes, simulate):
    if simulate:
        try:
            import rtlsdr  # noqa: F401
        except Exception:                      # no librtlsdr: the simulator does not need it
            import types
            m = types.ModuleType('rtlsdr')
            m.RtlSdr = object
            sys.modules['rtlsdr'] = m
    import qa_hw
    lab = qa_hw.Lab(out, simulate=simulate)
    t_end = time.time() + minutes * 60
    ts, errs, unl = [], 0, 0
    rss0 = th0 = None
    n = 0
    while time.time() < t_end:
        s = lab.scan('soak_%03d' % n, 470, 800, timeout=600)
        n += 1
        ts.append(s['time'])
        errs += 1 if (s['error'] or s['hung'] or s['aborted']) else 0
        unl += s['pll_unlocked'] or 0
        s.clear()
        if n == 2:
            rss0, th0 = rss_mb(), threading.active_count()
        print('soak %d: %.1f s, memory %.0f MB, threads %d' % (n, ts[-1], rss_mb(), threading.active_count()), flush=True)
    rss1, th1 = rss_mb(), threading.active_count()
    k = max(1, min(3, len(ts) // 3))
    print('SOAK ' + json.dumps({'n': n, 'minutes': minutes, 't_first': sum(ts[:k]) / k, 't_last': sum(ts[-k:]) / k,
                                'errors': errs, 'pll_unlocked': unl, 'rss_first': rss0 or rss1, 'rss_last': rss1,
                                'rss_growth_mb': rss1 - (rss0 or rss1), 'threads_growth': th1 - (th0 or th1)}), flush=True)


# ================================================================== ACCEPTANCE

def status_of(tid):
    for r in R.rows:
        if r['id'] == tid:
            return r['status'], r['detail']
    return None, ''


def pc_acceptance(args, out):
    def ev(ids):
        got = [(i, status_of(i)[0]) for i in ids]
        have = [s for _, s in got if s and s not in ('INFO', 'SKIP', 'MANUAL')]
        if not have:
            return 'MANUAL', got
        if 'FAIL' in have:
            return 'FAIL', got
        if 'WARN' in have:
            return 'WARN', got
        return ('PASS' if len(have) == len(got) else 'PARTIAL'), got   # INFO = not proven
    crit = [
        ('PAC1', 'Скорость на донгле: 470-800 <= 40 с, полный 31-1766 <= 180 с', ['HW.H2a', 'HW.H3a']),
        ('PAC3', 'Повторяемость тихих точек +-0,5 дБ', ['HW.H2c']),
        ('PAC4', 'Отказ донгла не вешает программу', ['HW.H7', 'SB.T.SY.qa_stability']),
        ('PAC5', 'Чистый скан: свои спуры убраны, сигналы целы', ['HW.H11', 'HWN.H8e', 'SB.SY1']),
        ('PAC6', 'Перегрузка исправляется автоусилением, шаги усиления точны', ['HW.H9', 'HW.H10']),
        ('PAC7', 'Окно с донглом: скан, экспорт, сообщения, время до конца', ['PW.U2', 'PW.U9', 'PW.U13']),
        ('PAC8', 'Длительная работа без роста времени и памяти', ['PL1', 'PL2']),
        ('PAC13', 'Скан только диапазонов выбранных систем (V18) с донглом', ['PW.U17', 'PW.U19', 'SB.AC10']),
        ('PAC16', 'Надёжность (V18.1): ошибки видны в окне, окно не застревает, выдернутый донгл', ['PW.U21', 'SB.AC11', 'HW.H7']),
    ]
    for aid, title, ids in crit:
        st, got = ev(ids)
        R.add(aid, 'ACCEPTANCE', st, title, ', '.join('%s %s' % (i, s or 'нет') for i, s in got))
    if not args.uat:
        R.add('PAC2', 'ACCEPTANCE', 'MANUAL', 'Файлы открываются в WWB 7.9 и совпадают с окном',
              'запустите с --uat (вопросы по шагам)')
        return
    files = sorted(glob.glob(os.path.join(out, 'hw', 'scan_470-800*.sdb3')) + glob.glob(os.path.join(out, 'smoke_scan.sdb3')))
    qs = [
        ('PAC2', 'Откройте в Shure WWB 7.9 файл %s (и .sdb2/.csv рядом). Открылся, частоты и уровни как в программе?'
         % (files[0] if files else 'smoke_scan.sdb3')),
        ('PAC9', 'В программе: Scan settings > Calibrate dongle... (антенна откручена) - внизу "Calibration saved"?'),
        ('PAC10', 'Тёмное окно читается, все надписи видны, ничего не мешает работе?'),
        ('PAC11', 'Скан с антенной 470-800 в окне: спуры 489,6 / 518,4 / 547,2 МГц больше не видны как пики, '
                  'ваши передатчики видны правильно?'),
        ('PAC14', 'Bands... (V18): выберите Польшу и свои системы. Диапазоны систем и пометки (Licence-free / '
                  'Not allowed) верны? Полоса правил внизу графика понятна?'),
        ('PAC15', 'Скан нескольких диапазонов: экспорт .sdb3/.csv открылся в WWB, в промежутках нет выдуманного сигнала?'),
        ('PAC12', 'В целом: можно ли использовать программу для подготовки частотного плана в WWB?'),
    ]
    say('\n=== ПРИЁМОЧНЫЕ ВОПРОСЫ: y - да, n - нет, s - пропустить; после ответа можно написать комментарий')
    for aid, q in qs:
        say('\n' + q)
        try:
            a = input('  [y/n/s] > ').strip().lower()
            note = input('  комментарий (Enter - без) > ').strip() if a in ('y', 'n') else ''
        except EOFError:
            a, note = 's', ''
        st = {'y': 'PASS', 'n': 'FAIL'}.get(a[:1], 'SKIP')
        R.add(aid, 'ACCEPTANCE', st, q[:120], note or '-')


# ========================================================================== MAIN

def main():
    ap = argparse.ArgumentParser(description='Полная проверка программы и донгла на ПК')
    ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--no-antenna', action='store_true')
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--soak', type=int, default=0, metavar='MIN')
    ap.add_argument('--uat', action='store_true')
    ap.add_argument('--unplug', action='store_true')
    ap.add_argument('--gain', type=float, default=33.8)
    ap.add_argument('--simulate', action='store_true', help='без донгла (проверка самого скрипта)')
    ap.add_argument('--out', default=None)
    ap.add_argument('--child', default=None, help=argparse.SUPPRESS)
    ap.add_argument('--minutes', type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child == 'smoke-scan':
        return child_smoke_scan(args.out)
    if args.child == 'soak':
        return child_soak(args.out, args.minutes, args.simulate)
    started = datetime.datetime.now()
    mode = 'smoke' if args.smoke else ('no_antenna' if args.no_antenna else 'standard')
    out = args.out or os.path.join(KIT, 'qa_pc_report_%s_%s' % (started.strftime('%Y%m%d-%H%M%S'), mode))
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    say('Отчёт: %s\nРежим: %s\n' % (out, mode))
    try:
        say('=== 1. ДЫМОВОЙ И САНИТИ: программа, DLL, донгл, окно')
        if not pc_environment():
            raise SystemExit('Программа не установлена - дальше проверять нечего')
        if not args.simulate:
            pc_dll()
            pc_smoke_scan(out)
        pc_window(out, real=False, gain=args.gain, only_u1=True)
        smoke_bad = [r for r in R.by_cat('SMOKE') if r['status'] == 'FAIL']
        if smoke_bad:
            say('\nДымовой тест не прошёл (%s) - остальное пропущено.' % ', '.join(r['id'] for r in smoke_bad))
        elif args.no_antenna:
            say('\n=== 2. ДОНГЛ БЕЗ АНТЕННЫ (qa_hw --no-antenna): шум, спуры, калибровка, чистый скан')
            hw = os.path.join(out, 'hw_noant')
            run_interactive([PY, '-u', QA_HW_PATH, '--no-antenna', '--no-pytest', '--out', hw]
                            + (['--simulate'] if args.simulate else []))
            import_md_rows(os.path.join(hw, 'report.md'), 'HWN.', hw_cat)
        else:
            say('\n=== 2. ВСЕ АВТОТЕСТЫ НА ВАШЕМ PYTHON (qa_sandbox, имитатор донгла)')
            sb = os.path.join(out, 'sandbox')
            sb_cmd = [PY, '-u', os.path.join(TOOLS, 'qa_sandbox.py'), '--out', sb]
            if args.smoke:
                sb_cmd.append('--quick')
            run_live(sb_cmd, os.path.join(out, 'sandbox_console.txt'), timeout=5400)
            import_rows(os.path.join(sb, 'results.json'), 'SB.')
            if not args.smoke:
                say('\n=== 3. ДОНГЛ С АНТЕННОЙ (qa_hw): скорость, качество, автоусиление, чистый скан')
                hw = os.path.join(out, 'hw')
                cmd = [PY, '-u', QA_HW_PATH, '--no-pytest', '--out', hw]
                cmd += ['--quick'] if args.quick else []
                cmd += ['--unplug'] if args.unplug else []
                cmd += ['--simulate'] if args.simulate else []
                run_interactive(cmd)
                import_md_rows(os.path.join(hw, 'report.md'), 'HW.', hw_cat)
                if not args.simulate:
                    say('\n=== 4. ОКНО ПРОГРАММЫ С НАСТОЯЩИМ ДОНГЛОМ (470-600 МГц)')
                    pc_window(out, real=True, gain=args.gain)
        if args.soak:
            say('\n=== 5. ДЛИТЕЛЬНАЯ НАГРУЗКА %d мин' % args.soak)
            pc_soak(out, args.soak, args.simulate)
        if not args.smoke:
            pc_acceptance(args, out)
    except KeyboardInterrupt:
        R.add('X', 'SYSTEM', 'WARN', 'прервано (Ctrl+C)')
    finally:
        head = ['Дата: %s; длительность %.0f мин; режим %s; Python %s' % (
            started.strftime('%Y-%m-%d %H:%M'), (time.time() - t0) / 60., mode, sys.version.split()[0]),
            'Подпапки: sandbox (все автотесты на вашем Python), hw / hw_noant (донгл), ui_*.txt (окно), soak.txt']
        tot = Q.write_report(out, R, 'Проверка на компьютере (qa_pc.py)', head)
        say('\nИтог: %s\nОтчёт: %s\nЗаархивируйте папку %s и пришлите.' % (
            ', '.join('%s %d' % kv for kv in sorted(tot.items())), os.path.join(out, 'report.md'), out))
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
