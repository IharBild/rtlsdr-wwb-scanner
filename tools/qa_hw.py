# -*- coding: utf-8 -*-
"""Проверка rtlsdr-wwb-scanner на живом RTL-SDR V4 и отчёт.

Запуск (сканер закрыт, донгл с антенной на обычном месте):
    python qa_hw.py              всё: окружение, автотесты, сканы на донгле (~25 мин)
    python qa_hw.py --quick      без полного скана 31-1766 и без серии из 10 сканов (~6 мин)
    python qa_hw.py --no-pytest  только донгл
    python qa_hw.py --unplug     в конце тест "выдернуть донгл посреди скана" (с подсказкой)
    python qa_hw.py --no-antenna --no-pytest
                                 АНТЕННА ОТКРУЧЕНА: собственный шум и спуры донгла
                                 против эталона V10 + scanner-v4 (~5 мин)
    python qa_hw.py --simulate   без донгла, на имитаторе (проверка самого скрипта)
Порядок: окружение (E), автотесты на имитаторе (T), окно программы на имитаторе (U),
прогрев донгла (H0), затем реальные сканы на донгле (H).
Сообщения самой DLL (stderr драйвера, напр. "i2c wr failed") ловятся отдельно (D1):
скрипт запускает себя дочерним процессом и пишет весь вывод в console.txt.

Результат: папка qa_hw_report_<дата-время> рядом со скриптом:
    report.md (итог), console.txt, pytest.txt, CSV и sdb2 сканов, отчёты времени.
Заархивируйте папку и пришлите.

Python 3.11+. Код сканера берётся из установленного пакета (site-packages),
тесты - из папки tests рядом со скриптом.
"""
from __future__ import print_function

import argparse
import datetime
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = HERE if os.path.isdir(os.path.join(HERE, 'tests')) else os.path.dirname(HERE)
TESTS = os.path.join(KIT, 'tests')


def dll_has(name):
    """True when the rtlsdr library pyrtlsdr loads exports ``name``."""
    try:
        from rtlsdr.librtlsdr import librtlsdr
        getattr(librtlsdr, name)
        return True
    except Exception:
        return False


def prepare_dll_search():
    """V21.3: Python 3.8+ on Windows does not search PATH or the Scripts folder
    of a virtual environment for DLLs, so pyrtlsdr 0.4+ would not find the
    rtlsdr.dll that install_dll.py put there. The scanner package (V21.3+) adds
    that folder when it is imported; do it before any 'import rtlsdr' here.
    Older installed versions have no dllpath: nothing changes for them."""
    try:
        from wwb_scanner import dllpath
        return dllpath.prepare()
    except Exception:
        return None


prepare_dll_search()

RESULTS = []      # (id, status, title, detail)   status: PASS / FAIL / WARN / INFO / SKIP

# Reference numbers of the user's dongle, V10 + rtlsdr.dll scanner-v4 (04.11.2026)
REF_TILE_S = 0.078            # s per tile, 31-1766 MHz scan (173.7 s, 2239 tiles)
REF_FULL_S = 173.7
# antenna disconnected, gain 33.8: median level per band, dB (v10_test_no_antenna.csv)
REF_NOANT = [(31, 88, -100.7), (88, 108, -100.6), (108, 174, -100.3), (174, 240, -99.7),
             (240, 300, -100.8), (300, 470, -100.7), (470, 700, -100.2), (700, 800, -100.4),
             (800, 1000, -100.8), (1000, 1250, -101.6), (1250, 1500, -102.6), (1500, 1766, -103.3)]
# own spurs of the dongle (antenna disconnected): k x 28.8 MHz, dB above the local floor
REF_SPURS = {86.4: 14.0, 115.2: 8.8, 144.0: 4.7, 172.8: 9.4, 201.6: 6.2, 230.4: 5.6, 259.2: 11.9,
             288.0: 3.6, 316.8: 4.8, 345.6: 7.4, 374.4: 9.6, 403.2: 8.9, 432.0: 5.7, 460.8: 5.9,
             489.6: 4.8, 518.4: 3.2}
DRIVER_MSG = re.compile(r'(i2c wr failed|i2c rd failed|PLL not locked|No valid PLL|LIBUSB_ERROR|usb_claim_interface)', re.I)
LOG = None


class Tee(object):
    def __init__(self, *streams):
        self.streams = streams
    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass
    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def result(tid, status, title, detail=''):
    RESULTS.append((tid, status, title, detail))
    print('[%s] %-4s %s%s' % (tid, status, title, (' - ' + detail) if detail else ''), flush=True)


def guard(tid, title):
    """Decorator: a crashing check becomes FAIL instead of stopping the run."""
    def deco(fn):
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                result(tid, 'FAIL', title, 'ошибка скрипта/программы: %r' % (exc,))
                traceback.print_exc()
                return None
        return wrapper
    return deco


# ---------------------------------------------------------------- environment

def check_environment(args):
    import numpy
    info = {
        'python': sys.version.split()[0] + (' 64-bit' if sys.maxsize > 2 ** 32 else ' 32-bit'),
        'os': platform.platform(),
        'numpy': numpy.__version__,
    }
    try:                        # V22.6: the scanner no longer needs scipy
        import scipy
        info['scipy'] = scipy.__version__
    except ImportError:
        info['scipy'] = 'нет (не нужна с V22.6)'

    for mod in ('rtlsdr', 'PySide6', 'pytest'):
        try:
            m = __import__(mod)
            info[mod] = getattr(m, '__version__', 'есть')
        except Exception as exc:
            info[mod] = 'нет (%s)' % type(exc).__name__
    try:
        from importlib.metadata import version as _v
        info['pyrtlsdr'] = _v('pyrtlsdr')
    except Exception:
        info['pyrtlsdr'] = '?'
    import wwb_scanner
    info['wwb_scanner'] = os.path.dirname(wwb_scanner.__file__)
    result('E1', 'INFO', 'окружение', ', '.join('%s %s' % kv for kv in info.items()))

    # pyrtlsdr >= 0.4 needs rtlsdr_set_dithering (not in RTL-SDR Blog / scanner-v4 DLL)
    v = info.get('pyrtlsdr', '?')
    try:
        major_minor = tuple(int(x) for x in v.split('.')[:2])
        if major_minor >= (0, 4):
            # V21.3.1: fine with DLL scanner-v7+ (it exports rtlsdr_set_dithering)
            if dll_has('rtlsdr_set_dithering'):
                result('E2', 'PASS', 'версия pyrtlsdr', '%s, DLL с rtlsdr_set_dithering (scanner-v7+)' % v)
            else:
                result('E2', 'WARN', 'версия pyrtlsdr', '%s: нужна DLL с rtlsdr_set_dithering - '
                       'drivers\\rtlsdr-scanner-v8\\install_dll.py' % v)
        else:
            result('E2', 'PASS', 'версия pyrtlsdr', v)
    except ValueError:
        result('E2', 'INFO', 'версия pyrtlsdr', v)

    # which V-version of the scanner files is installed (markers of V9/V10 fixes)
    base = info['wwb_scanner']
    marks = {
        'V9 scan_dll': os.path.exists(os.path.join(base, 'scan_dll', '__init__.py')),
        'V10 окна/ошибки плиток': 'make_window' in read(os.path.join(base, 'scanner', 'sample_processing.py')),
        'V10 30-2000 МГц': 'WWB_MIN_KHZ' in read(os.path.join(base, 'file_handlers', 'exporters.py')),
        'V10 график': '_replace_series' in read(os.path.join(base, 'ui', 'pyside', 'graph.py')),
        'V10 повтор плиток': 'retry_failed_tiles' in read(os.path.join(base, 'scanner', 'sample_processing.py')),
    }
    missing = [k for k, v in marks.items() if not v]
    result('E3', 'FAIL' if missing else 'PASS', 'установлены файлы V9/V10',
           ('нет: ' + ', '.join(missing)) if missing else 'все отметки найдены')
    qml = os.path.join(base, 'ui', 'pyside', 'qml')
    # V22.0: the scan history no longer uses tinydb (utils/dbstore.py JsonDB)
    m22 = 'class JsonDB' in read(os.path.join(base, 'utils', 'dbstore.py'))
    result('E3d', 'PASS' if m22 else 'FAIL', 'база сканов без tinydb (V22.0)',
           'utils/dbstore.py: JsonDB' if m22 else 'utils/dbstore.py старый (нужен V22.0)')
    m11 = {
        'график без анимации': 'NoAnimation' in read(os.path.join(qml, 'Graph.qml'))
                               and 'SeriesAnimations' not in read(os.path.join(qml, 'Graph.qml')),
        'экспорт sdb2 (V11.2)': 'withExtension' in read(os.path.join(qml, 'ExportDialog.qml')),
        'graph.py V11': 'save_to_file_as' in read(os.path.join(base, 'ui', 'pyside', 'graph.py')),
    }
    pq = os.path.join(base, 'ui', 'pyside')
    m16 = {'сообщения в окне': os.path.exists(os.path.join(pq, 'status.py')),
           'детектор перегрузки': 'report_overload' in read(os.path.join(base, 'scanner', 'sample_processing.py')),
           'метки спуров': 'spurFreqs' in read(os.path.join(pq, 'qml', 'Graph.qml')),
           'пресеты': 'presetCombo' in read(os.path.join(pq, 'qml', 'ScanControls.qml'))}
    miss16 = [k for k, v in m16.items() if not v]
    result('E3e', 'WARN' if miss16 else 'PASS', 'установлены файлы V16 (сообщения, перегрузка, спуры, пресеты)',
           ('нет: ' + ', '.join(miss16) + ' - поставьте fix-V16-pip-files.zip') if miss16 else 'все отметки найдены')
    m171 = {'безопасная база сканов': 'class _SafeUnpickler' in read(os.path.join(base, 'utils', 'numpyjson.py')),
            'sdb2 без DTD': 'DOCTYPE|ENTITY' in read(os.path.join(base, 'file_handlers', 'importers.py'))}
    miss171 = [k for k, v in m171.items() if not v]
    result('E3g', 'WARN' if miss171 else 'PASS', 'установлен V17.1 (безопасность)',
           ('нет: ' + ', '.join(miss171) + ' - поставьте fix-V17.1-pip-files.zip') if miss171 else 'все отметки найдены')
    m18 = {'bands.py': 'def scan_segments' in read(os.path.join(base, 'bands.py')),
           'окно Bands': os.path.exists(os.path.join(pq, 'qml', 'BandsDialog.qml')),
           'несколько диапазонов': '_build_multi_range' in read(os.path.join(base, 'scanner', 'main.py')),
           'экспорт без промежутков': 'def gap_mask' in read(os.path.join(base, 'file_handlers', 'exporters.py'))}
    miss18 = [k for k, v in m18.items() if not v]
    result('E3h', 'WARN' if miss18 else 'PASS', 'установлен V18 (диапазоны систем и стран)',
           ('нет: ' + ', '.join(miss18) + ' - поставьте fix-V18-pip-files.zip') if miss18 else 'все отметки найдены')
    m181 = {'ошибки в окне': 'def install_error_hooks' in read(os.path.join(pq, 'main.py')),
            'донгл не в потоке окна': 'def learn_gains' in read(os.path.join(base, 'scanner', 'main.py')),
            'зависший USB': 'def stuck_usb_calls' in read(os.path.join(base, 'scanner', 'sample_processing.py'))}
    miss181 = [k for k, v in m181.items() if not v]
    result('E3i', 'WARN' if miss181 else 'PASS', 'установлен V18.1 (надёжность по аудиту)',
           ('нет: ' + ', '.join(miss181) + ' - поставьте fix-V18.1-pip-files.zip') if miss181 else 'все отметки найдены')
    m17 = {'cleanup.py': os.path.exists(os.path.join(base, 'scanner', 'cleanup.py')),
           'автоусиление': 'remeasure_lower_gain' in read(os.path.join(base, 'scanner', 'sample_processing.py')),
           'калибровка в окне': 'calibrateButton' in read(os.path.join(pq, 'qml', 'ScanControlsDialog.qml')),
           'сырые данные в истории': 'raw_dbFS' in read(os.path.join(base, 'utils', 'dbstore.py'))}
    miss17 = [k for k, v in m17.items() if not v]
    result('E3f', 'WARN' if miss17 else 'PASS', 'установлены файлы V17 (чистый скан, автоусиление, калибровка)',
           ('нет: ' + ', '.join(miss17) + ' - поставьте fix-V17-pip-files.zip') if miss17 else 'все отметки найдены')
    m12 = {'экспорт sdb3 (V12)': 'class WWB3Exporter' in read(os.path.join(base, 'file_handlers', 'exporters.py')),
           'импорт sdb3 (V12)': 'class WWB3Importer' in read(os.path.join(base, 'file_handlers', 'importers.py'))}
    miss12 = [k for k, v in m12.items() if not v]
    result('E3c', 'WARN' if miss12 else 'PASS', 'установлены файлы V12 (sdb3, заголовок sdb2 как у WWB 7.9)',
           ('нет: ' + ', '.join(miss12) + ' - поставьте fix-V12-pip-files.zip') if miss12 else 'все отметки найдены')
    miss11 = [k for k, v in m11.items() if not v]
    result('E3b', 'WARN' if miss11 else 'PASS', 'установлены файлы V11.2 (график, экспорт)',
           ('нет: ' + ', '.join(miss11) + ' - поставьте fix-V11.2-pip-files.zip') if miss11 else 'все отметки найдены')
    result('E4', 'INFO', 'какую DLL загружает pyrtlsdr', loaded_dll())
    return info


def loaded_dll():
    """Path of the rtlsdr library pyrtlsdr loaded (Windows), and its build."""
    try:
        import ctypes
        from rtlsdr.librtlsdr import librtlsdr as L
        path = '?'
        if os.name == 'nt':
            buf = ctypes.create_unicode_buffer(1024)
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
            if k32.GetModuleFileNameW(ctypes.c_void_p(L._handle), buf, 1024):
                path = buf.value
        else:
            path = getattr(L, '_name', '?')
        build = 'штатная (без rtlsdr_get_build_info)'
        if hasattr(L, 'rtlsdr_get_build_info'):
            L.rtlsdr_get_build_info.restype = ctypes.c_char_p
            build = L.rtlsdr_get_build_info().decode('ascii', 'replace')
        return '%s; %s' % (path, build)
    except Exception as exc:
        return 'не удалось определить: %r' % (exc,)


def read(p):
    try:
        with io.open(p, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''


# ---------------------------------------------------------------- pytest

def run_pytest(outdir):
    if not os.path.isdir(TESTS):
        result('T1', 'SKIP', 'автотесты', 'нет папки tests рядом со скриптом')
        return
    try:
        import pytest  # noqa: F401
    except ImportError:
        result('T1', 'SKIP', 'автотесты', 'pytest не установлен: python -m pip install pytest')
        return
    env = dict(os.environ)
    env.setdefault('QA_SCANS', '20')
    env['PYTHONIOENCODING'] = 'utf-8'
    t0 = time.time()
    cmd = [sys.executable, '-m', 'pytest', TESTS, '-q', '-rfEs', '-p', 'no:cacheprovider',
           '--junitxml', os.path.join(outdir, 'pytest.xml')]
    p = subprocess.run(cmd, cwd=outdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode('utf-8', 'replace')
    with io.open(os.path.join(outdir, 'pytest.txt'), 'w', encoding='utf-8') as f:
        f.write(out)
    tail = [l for l in out.splitlines() if re.search(r'\d+ (passed|failed)', l)]
    summary = tail[-1].strip('= ') if tail else 'нет итоговой строки'
    failed = [l for l in out.splitlines() if l.startswith('FAILED') or l.startswith('ERROR')]
    result('T1', 'PASS' if p.returncode == 0 else 'FAIL', 'автотесты (имитатор)',
           '%s за %.0f с%s' % (summary, time.time() - t0,
                               ('; первые: ' + '; '.join(failed[:3])) if failed else ''))


# ---------------------------------------------------------------- window (Qt) with the simulator

def run_ui_check(outdir):
    ui = os.path.join(KIT, 'tools', 'qa_ui.py')
    if not os.path.exists(ui):
        result('U', 'SKIP', 'окно программы (Qt)', 'нет tools/qa_ui.py')
        return
    env = dict(os.environ)
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env['PYTHONIOENCODING'] = 'utf-8'
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, ui, '0.5', '1766'], cwd=outdir, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1200)
        out = p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired as exc:
        out = (exc.output or b'').decode('utf-8', 'replace') + '\nTIMEOUT'
    with io.open(os.path.join(outdir, 'ui.txt'), 'w', encoding='utf-8') as f:
        f.write(out)
    checks = [l for l in out.splitlines() if re.match(r'^U\d', l)]
    bad = [l for l in checks if ' FAIL ' in l]
    if not checks:
        result('U', 'FAIL', 'окно программы (Qt, имитатор, 0-1766)', 'окно не запустилось, см. ui.txt')
        return
    result('U', 'FAIL' if bad else 'PASS', 'окно программы (Qt, имитатор, 0-1766), %.0f с' % (time.time() - t0),
           '; '.join(l.split(None, 2)[0] + ' ' + l.split(None, 2)[2][:70] for l in (bad or checks)))


# ---------------------------------------------------------------- dongle scans

class Lab(object):
    """Runs real scans through the installed scanner (Scanner.run_scan)."""
    def __init__(self, outdir, simulate=False, no_antenna=False):
        self.outdir = outdir
        self.simulate = simulate
        if simulate:
            sys.path.insert(0, TESTS)
            import conftest  # noqa: F401  (rtlsdr stub if needed)
            import sim
            from wwb_scanner.scanner import sdrwrapper
            air = sim.Air(noise=0.03)
            for k in range(2, 30):                 # the dongle's own spurs
                air.tone(28.8e6 * k, 0.004)
            if not no_antenna:
                for st in (88.0, 91.3, 95.1, 100.6, 104.9):
                    air.tone(st * 1e6, 0.05)
                for lo in (474.19, 490.19, 554.19, 698.19):
                    air.band(lo * 1e6, (lo + 7.62) * 1e6, 0.05)
                air.tone(526.0e6, 0.9)             # own IEM next to the antenna: clips
                air.tone(600.3e6, 0.15)            # strong, not clipped (H10)
            self.sim_air = air
            self.made = []
            def factory(serial_number=None):
                s = sim.FakeRtlSdr(air=air, tune_delay=0.002, gain_affects=True)
                self.made.append(s)
                return s
            sdrwrapper.RtlSdr = factory
        from wwb_scanner.scan_objects import spectrum as spmod
        # do not fill the user's scan database with test scans
        spmod.Spectrum.save_to_dbstore = lambda self, *a, **k: None
        # V22.0: the time report is in the data folder (paths.py); before: home
        try:
            from wwb_scanner import paths as _paths
            self.home_report = _paths.timing_report_file()
        except ImportError:
            self.home_report = os.path.join(os.path.expanduser('~'), 'wwb_scan_timing.txt')

    def config(self, start, end, window_type='hann', overlap=0.65, sweeps=40, auto_gain=False, clean=False,
               ranges=None, band_gain=False, sample_rate=2.4e6):
        c = {
            'scan_range': [float(start), float(end)],
            'device': {'gain': 33.8},
            'sampling': {'sample_rate': float(sample_rate), 'sweep_overlap_ratio': overlap,
                         'sweeps_per_scan': sweeps, 'samples_per_sweep': 2048,
                         'window_size': 96, 'window_type': window_type,
                         # V22.0: the core defaults are the window's (all on);
                         # the H checks measure with exactly these switches
                         'auto_gain': bool(auto_gain), 'verify_peaks': False,
                         'band_gain': bool(band_gain)},          # V22.7 (an older scanner ignores it)
            'processing': {'clean_ripple': bool(clean), 'clean_spurs': bool(clean),
                           'clean_floor': bool(clean), 'clean_images': False},
        }
        if ranges:
            c['scan_ranges'] = [list(r) for r in ranges]   # V18
        return c

    def scan(self, name, start, end, timeout=1200, **kw):
        from wwb_scanner.scanner import main
        import numpy as np
        print('\n--- скан %s: %.1f-%.1f МГц %s' % (name, start, end, kw or ''), flush=True)
        sc = main.Scanner(config=self.config(start, end, **kw), history=None)
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = Tee(old, buf)
        t0 = time.time()
        th = threading.Thread(target=sc.run_scan)
        th.daemon = True
        th.start()
        th.join(timeout)
        dt = time.time() - t0
        sys.stdout = old
        hung = th.is_alive()
        sd = sc.spectrum.sample_data
        f = np.asarray(sd.frequency, float)
        y = np.asarray(sd['dbFS'], float)
        coll = sc.sample_collection
        out = buf.getvalue()
        m = re.search(r'tuner PLL: locked late (\d+) times, not locked (\d+) times', out)
        info = {
            'name': name, 'time': dt, 'hung': hung, 'f': f, 'y': y,
            'tiles': len(coll.sample_sets),
            'processed': (len(coll.processed_keys) if hasattr(coll, 'processed_keys')
                          else sum(1 for x in coll.sample_sets.values()
                                   if getattr(x, 'powers', None) is not None)),   # before V10
            'missing': list(getattr(coll, 'missing_tiles', []) or []),
            'overload': list(getattr(coll, 'overload', []) or []),
            'aborted': bool(getattr(coll, 'aborted', False)),
            'error': getattr(sc, 'error', None),
            'pll_late': int(m.group(1)) if m else None, 'pll_unlocked': int(m.group(2)) if m else None,
            'lib': (re.findall(r'rtlsdr library: (.*)', out) or ['?'])[-1],
            'scanner': sc, 'console': out,
            # V17
            'remeasured': len(getattr(coll, 'remeasured', []) or []),
            'still_clipped': sum(1 for x in coll.sample_sets.values()
                                 if getattr(x, 'clip_ratio', 0.) >= 1e-3),
            'cleanup': getattr(sc.spectrum, 'cleanup_info', None),
            'raw': getattr(sc.spectrum, 'raw_dbFS', None),
        }
        if os.path.exists(self.home_report):
            shutil.copy(self.home_report, os.path.join(self.outdir, 'timing_%s.txt' % name))
            info['timing'] = read(self.home_report)
        return info

    def save(self, s, name):
        p_csv = os.path.join(self.outdir, name + '.csv')
        p_sdb = os.path.join(self.outdir, name + '.sdb2')
        s['scanner'].spectrum.export_to_file(filename=p_csv, frequency_format='khz')
        try:
            s['scanner'].spectrum.export_to_file(filename=p_sdb)
        except Exception as exc:
            print('sdb2 export failed: %r' % (exc,))
            p_sdb = None
        return p_csv, p_sdb


# ---------------------------------------------------------------- analysis helpers

def per_tile(s):
    return s['time'] / max(1, s['tiles'])


def floor_level(y):
    """Level of the empty floor of a whole scan: 20th percentile (kit-8.1).
    A median would sit on the TV multiplexes when they fill the band."""
    import numpy as np
    return float(np.percentile(y, 20))


def quiet_floor(f, y, lo, hi, floor=None):
    """std of the floor in a band, only points at the floor level (kit-8.1:
    within 3 dB of the 20th percentile of the whole scan, not 'median + 3')."""
    import numpy as np
    m = (f >= lo) & (f <= hi)
    v = y[m]
    if v.size < 50:
        return None
    med = np.median(v)
    lim = (floor if floor is not None else med) + 3
    q = v[v < lim]
    if q.size < 50:
        return None
    d = np.diff(q)
    return {'median': med, 'std': float(np.std(q)), 'step_max': float(np.max(np.abs(d))) if d.size else 0.,
            'share_signal': 1 - q.size / float(v.size)}


def tile_ripple(f, y, lo, hi, period=31):
    import numpy as np
    m = (f >= lo) & (f <= hi)
    v = y[m]
    if v.size < 5 * period:
        return None
    base = np.convolve(v, np.ones(201) / 201., 'same')
    r = (v - base)[100:-100]
    n = (r.size // period) * period
    ph = r[:n].reshape(-1, period)
    ph = np.median(ph, axis=0)
    return float(np.ptp(ph))


def fm_raster(f, y):
    import numpy as np
    m = (f >= 87.5) & (f <= 108.0)
    ff, yy = f[m], y[m]
    if ff.size < 100:
        return None
    med = np.median(yy)
    # strong stations only (20 dB over the floor): a weak FM station's maximum
    # in 25 kHz bins wanders +-25/50 kHz with the modulation
    peaks = [i for i in range(2, ff.size - 2)
             if yy[i] > med + 20 and yy[i] == yy[i - 2:i + 3].max()]
    # the 4 strongest decide (weaker peaks can be off-raster transmitters or
    # intermodulation of strong ones, e.g. 2 x 97.7 - 98.3 = 97.1 MHz)
    peaks = sorted(peaks, key=lambda i: -yy[i])[:4]
    on = sum(1 for i in peaks if abs(round(ff[i] * 10) / 10. - ff[i]) < 0.001)
    return len(peaks), on


def tv_edges(f, y, lo=470., hi=790.):
    import numpy as np
    m = (f >= lo) & (f <= hi)
    if np.count_nonzero(m) < 20:
        return None                      # V20.5.2: the scan stopped early (PC 10.11 H3)
    ff = f[m]
    sm = np.convolve(y[m], np.ones(5) / 5., 'same')
    rise, fall = [], []
    for k in range(int((hi - lo) // 8)):
        e = lo + 8 * k
        i0 = int(np.argmin(np.abs(ff - e)))
        a = max(i0 - 100, 0)
        seg, fs = sm[a:i0 + 100], ff[a:i0 + 100]
        if seg.size < 20 or seg.max() - seg.min() < 12:
            continue
        mid = (seg.max() + seg.min()) / 2
        for c in np.flatnonzero((seg[:-1] < mid) & (seg[1:] >= mid)):
            rise.append(fs[c] - e)
        for c in np.flatnonzero((seg[:-1] >= mid) & (seg[1:] < mid)):
            fall.append(fs[c] - e)
    if len(rise) < 3 or len(fall) < 3:
        return None
    return float(np.median(rise)), float(np.median(fall)), len(rise), len(fall)


def csv_format(path, lo_khz=None, hi_khz=None):
    import numpy as np
    b = open(path, 'rb').read()
    lines = b.split(b'\n')
    bad = [l for l in lines if not re.match(rb'^\d+,-?\d+\.\d$', l)]
    k = np.array([int(l.split(b',')[0]) for l in lines if l and not l in bad])
    steps = sorted(set(np.diff(k).tolist())) if k.size > 1 else []
    problems = []
    if b.count(b'\r'):
        problems.append('есть \\r')
    if bad:
        problems.append('%d плохих строк' % len(bad))
    if b.endswith(b'\n'):
        problems.append('перевод строки в конце')
    if steps != [25]:
        problems.append('шаг %s' % steps[:5])
    if k.size and (k.min() < 30000 or k.max() > 2000000):
        problems.append('вне 30-2000 МГц')
    return problems, (k.min() / 1000. if k.size else 0), (k.max() / 1000. if k.size else 0), k.size


# ---------------------------------------------------------------- hardware checks

@guard('H1', 'донгл найден, какая DLL')
def h1_dongle(lab):
    s = lab.scan('h1_smoke', 470, 480)
    ok = s['tiles'] > 0 and s['processed'] == s['tiles'] and not s['error']
    result('H1', 'PASS' if ok else 'FAIL', 'донгл найден, короткий скан 470-480',
           'библиотека: %s; плиток %d/%d; ошибка %r' % (s['lib'], s['processed'], s['tiles'], s['error']))
    if ok:
        v4 = 'scanner-v4' in s['lib']
        result('H1b', 'PASS' if v4 else 'WARN', 'DLL scanner-v4 загружена',
               'да (перестройка ~22 мс, уровни выше штатной DLL 2023 г. на ~5,4 дБ)' if v4 else
               'нет: %s. Время и уровни не сравнимы с эталоном V10 + scanner-v4' % s['lib'])
        h1_calibrations()
    return s


def h1_calibrations():
    """V22.4: which dongle was opened and whose calibrations are used."""
    try:
        from wwb_scanner import dongles, paths
    except ImportError:
        return
    try:
        serial = dongles.active()
        folder = paths.calibration_dir()
        files = sorted(n for n in os.listdir(folder) if dongles.CAL_FILE_RE.match(n)) \
            if os.path.isdir(folder) else []
        reg = dongles.load_registry()
        legacy = (reg.get('legacy') or {}).get('state')
        known = [s for s, _ in dongles.known()]
        result('H1c', 'INFO', 'калибровки донгла',
               'серийный номер %r; папка %s; файлов калибровки %d (%s); известные донглы: %s; '
               'калибровки до V22.4: %s' % (serial, folder, len(files), ', '.join(files[:6]),
                                            ', '.join(map(repr, known)) or '-', legacy or '-'))
    except Exception as exc:
        result('H1c', 'WARN', 'калибровки донгла', 'не прочитаны: %s' % exc)


def h0_warmup(lab, seconds=90):
    """Stream for a while so the dongle is warm before measured scans
    (dll-bisect: after 90 s the level drifts 0.01 dB)."""
    if lab.simulate:
        result('H0', 'SKIP', 'прогрев донгла', 'имитатор')
        return
    print('\n--- прогрев донгла %d с (ничего не меряется)' % seconds, flush=True)
    try:
        from rtlsdr import RtlSdr
        sdr = RtlSdr()
        try:
            sdr.sample_rate = 2.4e6
            sdr.center_freq = 600e6
            sdr.gain = 33.8
            t_end = time.time() + seconds
            n = 0
            while time.time() < t_end:
                sdr.read_samples(262144)
                n += 1
        finally:
            sdr.close()
        result('H0', 'PASS', 'прогрев донгла', '%d с, %d блоков' % (seconds, n))
    except Exception as exc:
        result('H0', 'WARN', 'прогрев донгла', 'не удалось: %r (сканы идут без прогрева)' % (exc,))


@guard('H2', 'три скана 470-800')
def h2_three_scans(lab):
    import numpy as np
    scans = [lab.scan('h2_470_800_%d' % i, 470, 800) for i in (1, 2, 3)]
    lab.save(scans[0], 'scan_470-800_1')
    times = [s['time'] for s in scans]
    pt = [per_tile(s) for s in scans]
    unl = [s['pll_unlocked'] for s in scans]
    miss = [len(s['missing']) for s in scans]
    hung = any(s['hung'] for s in scans)
    slow = max(pt) > REF_TILE_S * 1.3
    status = 'FAIL' if hung or any(s['error'] for s in scans) else ('WARN' if any(miss) or slow else 'PASS')
    result('H2a', status, 'скорость 470-800 (3 скана)',
           'время %s с, %s с/плитку (эталон V10+scanner-v4 %.3f, у V7 0,114); PLL не захвачен %s; плиток без данных %s'
           % (['%.1f' % t for t in times], ['%.3f' % p for p in pt], REF_TILE_S, unl, miss))
    # floor and stitching on the first scan, in the quietest 50 MHz window
    f, y = scans[0]['f'], scans[0]['y']
    fl = floor_level(y)
    best = None
    for lo in [x for x in np.arange(470, 751, 10) if x + 50 <= 703 or x >= 748]:   # not LTE 700 uplink
        q = quiet_floor(f, y, lo, lo + 50, fl)
        if q and (best is None or q['share_signal'] < best[1]['share_signal']):
            best = (lo, q)
    if best:
        lo, q = best
        rip = tile_ripple(f, y, lo, lo + 50)
        # with an antenna there is often no truly empty 50 MHz in 470-800
        # (DVB-T, LTE 700, wireless systems), so a rougher floor is INFO
        st = 'PASS' if q['std'] <= 0.5 and (rip or 0) <= 0.8 else ('INFO' if q['std'] <= 1.0 else 'WARN')
        result('H2b', st, 'ровность шума (самая тихая полоса %.0f-%.0f МГц)' % (lo, lo + 50),
               'точки у пола: std %.2f дБ (у V7 0,16-0,21), рябь на периоде плитки %s дБ (у V7 0,4-0,7), '
               'макс. скачок %.2f дБ; сигналы занимают %.0f %% полосы'
               % (q['std'], '%.2f' % rip if rip is not None else '?', q['step_max'], 100 * q['share_signal']))
    # repeatability between scan 1 and 2 on common grid, quiet points only;
    # tiles where the ADC clipped (V16 overload detector) are left out: a
    # clipped tile is not repeatable by nature
    a, b = scans[0], scans[1]
    ov = (a.get('overload') or []) + (b.get('overload') or [])
    if a['f'].size and a['f'].size == b['f'].size and np.allclose(a['f'], b['f']):
        d = a['y'] - b['y']
        # kit-8.1: "quiet" = at the floor level in BOTH scans (signals that
        # come and go - TV fading, 5G/LTE traffic, own transmitters - are
        # the air, not the scanner)
        quiet = (a['y'] < floor_level(a['y']) + 3) & (b['y'] < floor_level(b['y']) + 3)
        clean = np.ones(a['f'].size, bool)
        for lo, hi, _ in ov:
            clean &= ~((a['f'] >= lo - 1) & (a['f'] <= hi + 1))
        m = quiet & clean
        sd = float(np.std(d[m])) if m.sum() > 50 else float('nan')
        st = 'INFO' if m.mean() < 0.15 else ('PASS' if sd <= 0.5 else 'WARN')
        allsig = ~quiet & clean
        result('H2c', st, 'повторяемость скан 1 / скан 2',
               'точки у пола (%.0f %%): среднее %+.2f дБ, std %.2f дБ (у NEW6-2 0,12); точки сигналов: std %.2f дБ '
               '(эфир меняется); исключено из-за перегрузки %.0f %%'
               % (100. * m.mean(), np.mean(d[m]) if m.any() else float('nan'), sd,
                  np.std(d[allsig]) if allsig.any() else float('nan'), 100. * (1 - clean.mean())))
    else:
        result('H2c', 'FAIL', 'повторяемость', 'сетки частот сканов не совпали')
    rngs = sorted(set('%.1f-%.1f' % (lo, hi) for lo, hi, _ in ov))
    result('H2e', 'WARN' if rngs else 'PASS', 'перегрузка АЦП (470-800, 3 скана)',
           ('сигнал упирается в предел 8 бит на %s МГц, до %.0f %% отсчётов - выключите/отнесите свои '
            'передатчики или уменьшите усиление' % (', '.join(rngs[:8]), 100. * max(r for _, _, r in ov)))
           if rngs else 'нет')
    p_csv = os.path.join(lab.outdir, 'scan_470-800_1.csv')
    problems, fmin, fmax, n = csv_format(p_csv)
    result('H2d', 'FAIL' if problems else 'PASS', 'CSV для WWB (470-800)',
           '%s; %.3f-%.3f МГц, %d строк' % (', '.join(problems) or 'формат как у WWB', fmin, fmax, n))
    return scans


@guard('H3', 'полный скан 31-1766')
def h3_full(lab):
    import numpy as np
    s = lab.scan('h3_full', 31, 1766, timeout=1800)
    lab.save(s, 'scan_31-1766')
    f, y = s['f'], s['y']
    status = 'FAIL' if s['hung'] or s['error'] else (
        'WARN' if s['missing'] or s['time'] > REF_FULL_S * 1.25 else 'PASS')
    result('H3a', status, 'полный скан 31-1766',
           '%.0f с (эталон V10+scanner-v4 174 с, у V7 259 с), %d плиток, %.3f с/плитку; PLL поздно %s / не захвачен %s; без данных %d%s'
           % (s['time'], s['tiles'], per_tile(s), s['pll_late'], s['pll_unlocked'], len(s['missing']),
              (' (напр. %.3f МГц)' % (s['missing'][0] / 1e6)) if s['missing'] else ''))
    t = s.get('timing', '')
    m = re.search(r'first 20%: ([\d.]+), last 20%: ([\d.]+)', t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        result('H3b', 'PASS' if b <= a * 1.3 else 'WARN', 'нет замедления к концу скана',
               'первые 20 %%: %.3f с, последние 20 %%: %.3f с на плитку' % (a, b))
    fm = fm_raster(f, y)
    if fm:
        n, on = fm
        result('H3c', 'PASS' if n and on == n else ('WARN' if n else 'INFO'), 'частота: сильные FM на сетке 100 кГц',
               '%d из %d самых сильных станций точно на сетке' % (on, n))
    tv = tv_edges(f, y, 470., 694.)      # DVB-T in Poland; 703-788 is LTE 700
    if tv:
        r, fa, nr, nf = tv
        sym = abs(r + fa) / 2 <= 0.1      # rough: FM raster (H3c) is the exact check
        result('H3d', 'PASS' if sym else 'INFO', 'частота: края ТВ-каналов 470-694 (грубо, +-0,15 МГц)',
               'начала %+.3f МГц (%d), концы %+.3f МГц (%d); сдвиг частоты ~%+.3f МГц'
               % (r, nr, fa, nf, (r + fa) / 2))
    # the dongle's own floor falls ~1.7 dB from 1000 to 1700 MHz, so the
    # spread is taken per 100 MHz (whole band std 0.65-0.72 is that slope)
    parts = [quiet_floor(f, y, lo, lo + 100) for lo in range(1000, 1700, 100)]
    parts = [p for p in parts if p]
    if parts:
        sd = float(np.median([p['std'] for p in parts]))
        med = float(np.median([p['median'] for p in parts]))
        result('H3e', 'PASS' if sd <= 0.5 else 'WARN', 'шум 1000-1700 МГц (по 100 МГц)',
               'медиана %.1f дБ, std %.2f дБ (без антенны у V10 ~0,2-0,35)' % (med, sd))
    p_csv = os.path.join(lab.outdir, 'scan_31-1766.csv')
    problems, fmin, fmax, n = csv_format(p_csv)
    result('H3f', 'FAIL' if problems else 'PASS', 'CSV для WWB (полный)',
           '%s; %.3f-%.3f МГц, %d строк' % (', '.join(problems) or 'формат как у WWB', fmin, fmax, n))
    return s


@guard('H4', 'окна')
def h4_windows(lab):
    out = []
    bad = False
    try:                        # V22.6: three windows
        from wwb_scanner.scanner.sample_processing import WINDOW_TYPES as wlist
    except ImportError:
        wlist = ('hann', 'blackmanharris', 'general_hamming', 'general_cosine', 'flattop')
    for w in wlist:
        s = lab.scan('h4_%s' % w, 470, 500, window_type=w)
        ok = not s['hung'] and not s['error'] and s['f'].size > 1000
        bad = bad or not ok
        out.append('%s %s (%d точек, %.0f с)' % (w, 'ok' if ok else 'СБОЙ', s['f'].size, s['time']))
    result('H4', 'FAIL' if bad else 'PASS', 'все окна из меню работают (470-500)', '; '.join(out))


@guard('H5', 'Overlap Ratio')
def h5_overlap(lab):
    import numpy as np
    out = []
    bad = False
    for ov in (0.0, 0.8, 0.9):
        # Overlap 0 -> step of one bin: ~200 tiles even for 5 MHz
        s = lab.scan('h5_ov%.1f' % ov, 470, 475 if ov == 0 else 490, overlap=ov, timeout=300)
        d = np.diff(s['f'])
        holes = int((d > 0.026).sum())
        ok = not s['hung'] and holes == 0 and s['f'].size > 150
        bad = bad or not ok
        out.append('%.1f: %d плиток, %d дыр, %.0f с%s' % (ov, s['tiles'], holes, s['time'], ' ЗАВИС' if s['hung'] else ''))
    result('H5', 'FAIL' if bad else 'PASS', 'Overlap Ratio 0 / 0,8 / 0,9 без дыр и зависаний', '; '.join(out))


@guard('H6', 'серия сканов')
def h6_series(lab, n=10):
    times = []
    bad = 0
    for i in range(n):
        s = lab.scan('h6_series_%d' % (i + 1), 470, 520)
        times.append(s['time'])
        if s['hung'] or s['error'] or s['aborted']:
            bad += 1
    result('H6', 'PASS' if bad == 0 and times[-1] <= times[0] * 1.5 + 1 else 'FAIL',
           '%d сканов подряд 470-520' % n,
           'сбоев %d; время первого %.1f с, последнего %.1f с, max %.1f с' % (bad, times[0], times[-1], max(times)))


@guard('H7', 'выдернуть донгл')
def h7_unplug(lab):
    print('\n>>> Тест H7: через 10 секунд после начала скана ВЫДЕРНИТЕ донгл из USB.')
    print('>>> Нажмите Enter, чтобы начать...')
    try:
        input()
    except EOFError:
        pass
    s = lab.scan('h7_unplug', 470, 1000, timeout=180)
    ok = not s['hung']
    result('H7', 'PASS' if ok else 'FAIL', 'донгл выдернут посреди скана',
           'скан закончился за %.0f с, aborted %s, ошибка %r, точек сохранено %d'
           % (s['time'], s['aborted'], s['error'], s['f'].size))
    print('>>> Вставьте донгл обратно.')


@guard('H8', 'без антенны: шум и спуры донгла')
def h8_no_antenna(lab):
    """Antenna disconnected: own floor per band and own spurs against the
    reference of this dongle (V10 + scanner-v4, gain 33.8)."""
    import numpy as np
    s = lab.scan('h8_no_antenna', 31, 1766, timeout=1800)
    lab.save(s, 'scan_no_antenna_31-1766')
    f, y = s['f'], s['y']
    if f.size < 1000 or s['hung'] or s['error']:
        result('H8a', 'FAIL', 'скан без антенны', 'скан не получился: hung %s, ошибка %r' % (s['hung'], s['error']))
        return None
    rows, worst = [], 0.0
    for lo, hi, ref in REF_NOANT:
        m = (f >= lo) & (f < hi)
        if m.sum() < 50:
            continue
        d = float(np.median(y[m])) - ref
        worst = max(worst, abs(d))
        rows.append('%d-%d %+.1f' % (lo, hi, d))
    result('H8a', 'PASS' if worst <= 1.5 else 'WARN', 'собственный шум по полосам против эталона',
           'отличие, дБ: %s (норма +-1,5; больше - другое усиление/DLL, нагрев или антенна не откручена)' % ', '.join(rows))
    # peaks > 3 dB above the local floor
    k = 161
    pad = np.pad(y, k // 2, mode='edge')
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        base = np.median(sliding_window_view(pad, k), axis=1)
    except ImportError:            # numpy < 1.20
        base = np.array([np.median(pad[i:i + k]) for i in range(y.size)])
    e = y - base
    idx = np.where(e > 3)[0]
    groups = []
    for i in idx:
        if groups and i - groups[-1][-1] <= 4:
            groups[-1].append(i)
        else:
            groups.append([i])
    peaks = [(float(f[g[int(np.argmax(e[g]))]]), float(e[g].max())) for g in groups]
    own, other = [], []
    for fr, ex in peaks:
        kk = round(fr / 28.8)
        (own if abs(fr - kk * 28.8) <= 0.06 else other).append((fr, ex))
    lost = [r for r in sorted(REF_SPURS) if REF_SPURS[r] >= 5 and not any(abs(r - fr) < 0.06 for fr, _ in own)]
    # 1700-1766: top of the tuner range and LTE 1800 uplink (phones nearby), not counted
    edge = [p for p in other if p[0] > 1700]
    other = [p for p in other if p[0] <= 1700]
    result('H8b', 'WARN' if other else 'PASS', 'все пики без антенны - кратные 28,8 МГц',
           '%sкратных 28,8: %d (%s); других: %s%s' % (
               ('ЧУЖИЕ СИГНАЛЫ ПРОНИКАЮТ БЕЗ АНТЕННЫ - скорее всего включённые рядом передатчики '
                '(радиомикрофоны/IEM, телефон); для чистого замера выключите их или отнесите. ') if other else '',
               len(own), ', '.join('%.1f %+.1f' % p for p in own[:8]),
               ', '.join('%.3f %+.1f' % p for p in other[:10]) or 'нет',
               ('; 1700-1766 (край тюнера / LTE 1800 uplink, не считаются): ' + ', '.join('%.3f' % p[0] for p in edge)) if edge else ''))
    diffs = ['%.1f %+.1f' % (fr, ex - REF_SPURS[round(fr, 1)]) for fr, ex in own if round(fr, 1) in REF_SPURS]
    result('H8c', 'INFO' if not lost else 'WARN', 'спуры против эталона (дБ над полом, разница)',
           '%s%s' % (', '.join(diffs[:16]), ('; пропали: ' + ', '.join('%.1f' % r for r in lost)) if lost else ''))
    return s


# ---------------------------------------------------------------- V17 clean scan

def v17_installed():
    try:
        from wwb_scanner.scanner import cleanup  # noqa: F401
        return True
    except ImportError:
        result('H9', 'SKIP', 'чистый скан (V17)', 'V17 не установлен')
        return False


def _zone_mask(f, zones, pad=1.0):
    import numpy as np
    m = np.zeros(f.size, bool)
    for lo, hi, _ in zones:
        m |= (f >= lo - pad) & (f <= hi + pad)
    return m


@guard('H8d', 'калибровка и чистый скан без антенны (V17)')
def h8d_calibrate_clean(lab, s):
    import numpy as np
    from wwb_scanner.scanner import cleanup as C
    sc = s['scanner']
    meta = dict(gain=float(sc.gain), sample_rate=float(sc.sample_rate), step_mhz=float(sc.step_size),
                window_size=96, window_type='hann', source='qa_hw H8')
    ok, text = C.calibrate(s['f'], s['y'], 31.0, sc.step_size, meta)
    result('H8d', 'PASS' if ok else 'WARN', 'калибровка донгла по скану без антенны', text)
    if not ok:
        return
    s2 = lab.scan('h8e_no_antenna_clean', 31, 1766, timeout=1800, clean=True)
    lab.save(s2, 'scan_no_antenna_clean_31-1766')
    raw, y, f = s2['raw'], s2['y'], s2['f']
    info = s2['cleanup'] or {}
    if raw is None or not info:
        result('H8e', 'FAIL', 'чистый скан без антенны', 'очистка не выполнена: %r' % (info,))
        return
    def spread(v):
        meds = [float(np.median(v[(f >= lo) & (f < hi)])) for lo, hi, _ in REF_NOANT
                if ((f >= lo) & (f < hi)).sum() > 50]
        return max(meds) - min(meds)
    def spurs_left(v):
        n = []
        for k in range(2, 62):
            i = int(np.argmin(np.abs(f - 28.8 * k)))
            if abs(f[i] - 28.8 * k) > 0.02 or i < 10 or i > f.size - 11:
                continue
            side = np.r_[v[i - 10:i - 4], v[i + 5:i + 11]]
            if v[i - 1:i + 2].max() - np.median(side) > 1.0:
                n.append(round(28.8 * k, 1))
        return n
    r0 = raw - C.running_median(raw, 61)
    r1 = y - C.running_median(y, 61)
    sp0, sp1 = spurs_left(raw), spurs_left(y)
    ok = spread(y) <= 0.6 and len(sp1) <= 1
    result('H8e', 'PASS' if ok else 'WARN', 'чистый скан без антенны: пол ровный, спуров нет',
           'разброс пола по полосам %.1f -> %.1f дБ; шероховатость %.2f -> %.2f дБ; спуры > 1 дБ: %d -> %d %s; '
           'рябь плиток %s; %s' % (spread(raw), spread(y), np.std(r0[np.abs(r0) < 3]), np.std(r1[np.abs(r1) < 3]),
                                   len(sp0), len(sp1), sp1[:6], info.get('ripple_pp_db'),
                                   'пол выровнен' if info.get('flatten') else 'пол НЕ выровнен: %s' % info.get('floor_skipped')))


H9_MIN_POINTS = 10       # V22.11: quiet points needed for the repeatability (was 20, gave nan)


@guard('H9', 'автоусиление при перегрузке (V17)')
def h9_auto_gain(lab, h2):
    import numpy as np
    scans = [lab.scan('h9_470_800_auto_%d' % i, 470, 800, auto_gain=True, clean=True) for i in (1, 2)]
    lab.save(scans[0], 'scan_470-800_clean_auto')
    ov = (h2[0].get('overload') or []) + (h2[1].get('overload') or [])
    t_off = np.mean([s['time'] for s in h2[:2]])
    t_on = np.mean([s['time'] for s in scans])
    if not ov:
        result('H9', 'INFO', 'автоусиление при перегрузке (470-800)',
               'перегрузки нет - автоусиление не понадобилось; время %.1f -> %.1f с' % (t_off, t_on))
        return scans
    f = h2[0]['f']
    a, b = scans
    if not (a['f'].size == f.size == b['f'].size and np.allclose(a['f'], f)):
        result('H9', 'FAIL', 'автоусиление', 'сетки частот не совпали')
        return scans
    zones = ', '.join(sorted(set('%.1f-%.1f' % (lo, hi) for lo, hi, _ in ov))[:6])
    n_re = a['remeasured'] + b['remeasured']
    if n_re == 0:
        # kit-8.1: the transmitter went quiet between H2 and H9 - nothing was
        # re-measured, so auto gain was not tested (was PASS by mistake)
        result('H9', 'INFO', 'автоусиление при перегрузке (470-800)',
               'перегрузка была в H2 (%s МГц), а в сканах H9 её уже не было - перемерено 0 плиток, '
               'автоусиление не проверено (эфир изменился)' % zones)
        return scans
    z = _zone_mask(f, ov)
    # repeatability only on floor-level points of the zones (signals there
    # change by themselves: 5G/LTE traffic, own transmitters)
    def quiet(s1, s2):
        return z & (s1['y'] < floor_level(s1['y']) + 3) & (s2['y'] < floor_level(s2['y']) + 3)
    q_off, q_on = quiet(h2[0], h2[1]), quiet(a, b)
    # V22.11 (hod-rabot-34): after auto gain the quiet points next to narrow
    # zones may be few; "nan" in the summary looked like an error
    def rep(s1, s2, q):
        n = int(q.sum())
        if n < H9_MIN_POINTS:
            return 'мало точек (%d)' % n
        return '%.2f' % float(np.std((s1['y'] - s2['y'])[q]))
    rep_off, rep_on = rep(h2[0], h2[1], q_off), rep(a, b, q_on)
    top = np.argsort(np.where(z, h2[0]['y'], -999))[-20:]
    dl = float(np.median(a['y'][top] - h2[0]['y'][top]))
    still = a['still_clipped'] + b['still_clipped']
    result('H9', 'PASS' if still == 0 else 'WARN', 'автоусиление при перегрузке (470-800)',
           'зоны %s МГц; перемеряно плиток %d + %d, осталось с перегрузкой %d; пол рядом с зонами: повторяемость '
           '%s -> %s дБ; уровень сильнейших точек %+.1f дБ (для сведения: эфир меняется); время %.1f -> %.1f с'
           % (zones, a['remeasured'], b['remeasured'], still, rep_off, rep_on, dl, t_off, t_on))
    return scans


H10_MIN_SNR_DB = 15      # V20.4


def _welch96(x):
    """Welch, hann 96, two-sided (as scipy.signal.welch(..., return_onesided=False,
    detrend=False)). V22.6: the scanner's own Welch and windows, scipy if they
    are missing (an older installed scanner)."""
    import numpy as np
    try:
        from wwb_scanner.scanner import psd as _psd
        from wwb_scanner.scanner.windows import get_window
        return _psd.welch(np.asarray(x), 2.4e6, get_window('hann', 96), 96)
    except ImportError:
        from scipy.signal import welch
        return welch(x, fs=2.4e6, window='hann', nperseg=96, return_onesided=False, detrend=False)


@guard('H10', 'точность шагов усиления (V17)')
def h10_gain_steps(lab, s):
    """Auto gain scales a re-measured tile by the nominal gain difference.
    Check on a strong (not clipped) signal how exact the nominal steps are."""
    import numpy as np
    from wwb_scanner.scanner import cleanup as C
    from wwb_scanner.scanner.sample_processing import clip_ratio
    f, y = s['f'], s['y']
    z = _zone_mask(f, s.get('overload') or [], pad=3.0)
    floor = floor_level(y)          # kit-8.1: wide TV multiplexes fooled a running median
    e = np.where(z, -99, y - floor)
    # V20.5: a NARROW carrier - a wide one (LTE, DVB-T) fills the tile and
    # leaves no noise to compare with (kit 9.9: 798.6 MHz, nothing evaluated)
    i = None
    for j in np.argsort(e)[::-1][:400]:
        if e[j] < 20:
            break
        lo_s = (f < f[j] - 0.35) & (f > f[j] - 0.8)
        hi_s = (f > f[j] + 0.35) & (f < f[j] + 0.8)
        # V20.5.2: quiet on BOTH sides (574.275 MHz sat at the edge of a TV multiplex)
        if np.count_nonzero(lo_s) >= 4 and np.count_nonzero(hi_s) >= 4 and \
                max(np.median(y[lo_s]), np.median(y[hi_s])) <= y[j] - 20:
            i = int(j)
            break
    if i is None:
        result('H10', 'INFO', 'точность шагов усиления',
               'нет сильной узкой несущей (> 20 дБ над полом и над соседними 0,5 МГц) вне перегрузки')
        return
    fpk = float(f[i])
    if lab.simulate:
        import sim
        sdr = sim.FakeRtlSdr(air=lab.sim_air, gain_affects=True)
    else:
        from rtlsdr import RtlSdr
        sdr = RtlSdr()
    rows = []
    try:
        sdr.sample_rate = 2.4e6
        off = 300e3
        sdr.set_center_freq(fpk * 1e6 + off)
        gains = sorted(g / 10. for g in sdr.get_gains())
        g0 = min(gains, key=lambda g: abs(g - 33.8))
        ref = None
        for drop in (0, 6, 12, 20, 30):
            g = [x for x in gains if x <= g0 - drop + 0.5][-1]
            sdr.gain = g
            sdr.read_samples(16384)
            x = sdr.read_samples(262144)
            fr, p = _welch96(x)
            o = np.argsort(fr)
            fr, p = fr[o], p[o]
            k = int(np.argmin(np.abs(fr + off)))
            lvl = 10 * np.log10(p[k - 2:k + 3].mean()) + (g0 - g)
            # V20.4: signal over the floor of this reading - a signal sunk
            # in the noise at a low gain says nothing about the gain step
            snr = 10 * np.log10(p[k - 2:k + 3].mean() / np.percentile(p, 10))   # V20.5.2: TV next to it
            if ref is None:
                ref = lvl
            rows.append((g, lvl - ref, clip_ratio(x), snr))
    finally:
        try:
            sdr.close()
        except Exception:
            pass
    # V20.5: with the gain step table (Scan settings > Calibrate gain steps)
    # auto gain scales by the real step: the error left is d - nominal + real
    table_note = ''
    try:
        from wwb_scanner.scanner import gain_cal
        table = gain_cal.load_table(2.4e6)
    except Exception:
        table = None
    if table is not None:
        g0 = rows[0][0]
        corr = [(g, d - (g0 - g) + gain_cal.real_drop(g0, g, fpk * 1e6, table=table), c, q)
                for g, d, c, q in rows]
        table_note = '; по таблице шагов (%s): %s' % (
            table.get('date', '?'), ', '.join('%.1f дБ: %+.1f' % (g, d) for g, d, c, q in corr[1:]
                                              if q >= H10_MIN_SNR_DB and c < 1e-3))
        rows_eval = corr
    else:
        table_note = '; таблица шагов не откалибрована (Scan settings > Calibrate gain steps)'
        rows_eval = rows
    bad = [r for r in rows_eval[1:] if abs(r[1]) > 1.5 and r[2] < 1e-3 and r[3] >= H10_MIN_SNR_DB]
    weak = [r for r in rows[1:] if r[3] < H10_MIN_SNR_DB]

    def mark(c, snr):
        if c >= 1e-3:
            return ' перегр.'
        if snr < H10_MIN_SNR_DB:
            return ' (сигнал +%.0f дБ над шумом - не считается)' % snr
        return ''
    evaluated = [r for r in rows_eval[1:] if r[2] < 1e-3 and r[3] >= H10_MIN_SNR_DB]
    result('H10', 'WARN' if bad else ('PASS' if evaluated else 'INFO'),
           'точность шагов усиления (сигнал %.3f МГц, +%.0f дБ)' % (fpk, e[i]),
           'отклонение уровня после пересчёта, дБ: %s (норма +-1,5; шаги, где сигнал меньше %d дБ над шумом, '
           'не оцениваются)%s%s'
           % (', '.join('%.1f дБ: %+.1f%s' % (g, d, mark(c, q)) for g, d, c, q in rows), H10_MIN_SNR_DB,
              '; автоусиление даёт ошибку уровня на этих шагах' if bad else '',
              '; для слабых шагов нужен сигнал сильнее' if weak and not bad else '') + table_note)


@guard('H11', 'чистый скан с антенной (V17)')
def h11_clean(lab, s):
    import numpy as np
    from wwb_scanner.scanner import cleanup as C
    info = s['cleanup'] or {}
    raw, y, f = s['raw'], s['y'], s['f']
    if raw is None or not info:
        result('H11', 'FAIL', 'чистый скан с антенной', 'очистка не выполнена: %r' % (info,))
        return
    rip0 = tile_ripple(f, raw, 470, 800)
    rip1 = tile_ripple(f, y, 470, 800)
    big = np.abs(y - raw) > 3
    result('H11', 'PASS', 'чистый скан с антенной (470-800)',
           'спуров убрано %d: %s; рябь плиток %s -> %s дБ; %s; точек изменено > 3 дБ: %d'
           % (len(info.get('spurs_removed') or []), ', '.join('%g' % v for v in (info.get('spurs_removed') or [])[:10]),
              '%.2f' % rip0 if rip0 is not None else '?', '%.2f' % rip1 if rip1 is not None else '?',
              'пол выровнен по калибровке %s' % info.get('calibration') if info.get('flatten')
              else 'пол не выровнен: %s' % info.get('floor_skipped'), int(big.sum())))


def v2210_installed():
    try:
        from wwb_scanner.scanner.gain_plan import noise_choice  # noqa: F401
        return True
    except ImportError:
        return False


@guard('H13b', 'шумовая проверка своего усиления, 174-240 МГц (V22.10)')
def h13b_band_gain_vhf(lab):
    """V22.10 (A4): DAB / TV band III, where more gain made the floor
    referred to the input worse on the user's dongle (200 MHz, 20.11: -7 dB at
    49.6 without clipping). The same range at the gain of the scan and with
    "Own gain per band": the gains the noise check kept and the floor of the
    raised bands. PASS: no raised band whose floor got higher than the gain
    says (floor referred to the input worse)."""
    import numpy as np
    ref = lab.scan('vhf_ref', 174, 240, clean=True, auto_gain=True)
    s = lab.scan('vhf_band_gain', 174, 240, clean=True, auto_gain=True, band_gain=True)
    plan = getattr(s['scanner'], 'gain_plan', None)
    if s['hung'] or s['error'] or ref['error'] or plan is None:
        result('H13b', 'FAIL', 'своё усиление на полосу, 174-240',
               'завис %s, ошибка %r / %r, план %r' % (s['hung'], s['error'], ref['error'], plan))
        return
    bands = '; '.join('%g-%g %.1f дБ (%s)' % (b.lo, b.hi, b.gain, b.why) for b in plan.bands)
    if not plan.banded:
        result('H13b', 'INFO', 'своё усиление на полосу, 174-240: усиление нигде не поднято',
               'проба %.1f с; %s' % (plan.seconds, plan.note or bands))
        return
    f, y, rf, ry = s['f'], s['y'], ref['f'], ref['y']
    rows, worse = [], False
    for b in plan.bands:
        if b.gain <= plan.ref_gain + 0.05:
            continue
        m_new = (f >= b.lo) & (f <= b.hi)
        m_ref = (rf >= b.lo) & (rf <= b.hi)
        if m_new.sum() < 50 or m_ref.sum() < 50:
            continue
        # levels of the band are given at the gain of the scan: a lower floor
        # there = a lower floor referred to the input
        d = float(np.percentile(y[m_new], 20) - np.percentile(ry[m_ref], 20))
        worse = worse or d > 0.5
        rows.append('%g-%g: пол %+.1f дБ' % (b.lo, b.hi, d))
    result('H13b', 'WARN' if worse else 'PASS', 'своё усиление на полосу, 174-240 (шумовая проверка)',
           'проба %.1f с; полосы: %s; %s' % (plan.seconds, bands, '; '.join(rows) or 'нет полос для сравнения'))


@guard('H16', 'частота дискретизации 3,2 МС/с: фильтр ПЧ, точки 25 кГц, гребёнка (V22.10)')
def h16_rates(lab):
    """V22.10 on the real dongle: 470-480 MHz at 2.4 and 3.2 MS/s.
    a) the IF filter of the tuner is 2.4 MHz at 3.2 MS/s (23.11: the 6 MHz mode
       made the output 4-6 dB lower);
    b) the points are 25 kHz wide at both rates (window 96 -> 128);
    c) for the record: the floor at 3.2 against 2.4 MS/s (23.11 with the 6 MHz
       filter: -17 dB; expected now about -12 dB, the rest depends on the rate),
       the tile shape and the comb lines of the kit that were hidden."""
    import numpy as np
    from wwb_scanner.receivers import rtlsdr as rr
    rows, bad = [], []
    got = {}
    real_set = rr.set_if_bandwidth
    for fs in (2.4e6, 3.2e6):
        seen = []

        def spy(dev, sample_rate=None, _seen=seen):
            ok = real_set(dev, sample_rate)
            try:
                _seen.append(int(dev.get_bandwidth()) if ok else None)
            except Exception:
                _seen.append(None)
            return ok
        rr.set_if_bandwidth = spy
        try:
            s = lab.scan('rate_%.1f' % (fs / 1e6), 470, 480, clean=True, sample_rate=fs)
        finally:
            rr.set_if_bandwidth = real_set
        sc = s['scanner']
        if s['hung'] or s['error']:
            bad.append('%.1f МС/с: ошибка %r' % (fs / 1e6, s['error']))
            continue
        f, y = s['f'], s['y']
        step = float(np.median(np.diff(f))) if f.size > 2 else 0.
        bw = seen[-1] if seen else 'не задан'
        info = getattr(sc.spectrum, 'cleanup_info', None) or {}
        comb = info.get('comb') or {}
        got[fs] = float(np.median(y)) if y.size else None
        rows.append('%.1f МС/с: окно %s, точка %.1f кГц, пол %.1f дБ, линий гребёнки скрыто %d' % (
            fs / 1e6, sc.window_size, step * 1e3, got[fs] if got[fs] is not None else float('nan'),
            len(comb.get('hidden') or [])))
        if abs(step - 0.025) > 0.001:
            bad.append('%.1f МС/с: точка %.1f кГц, а не 25' % (fs / 1e6, step * 1e3))
        rows[-1] += ', фильтр ПЧ %s Гц' % bw
        if fs > 2.5e6 and bw != 2400000:
            bad.append('фильтр ПЧ %s, а не 2400000 Гц' % bw)
    if got.get(2.4e6) is not None and got.get(3.2e6) is not None:
        rows.append('3.2 против 2.4: %+.1f дБ (23.11 до V22.10: -16.7)' % (got[3.2e6] - got[2.4e6]))
    result('H16', 'FAIL' if bad else 'PASS', '3,2 МС/с: фильтр ПЧ 2,4 МГц и точки 25 кГц',
           '; '.join(bad + rows))


def v227_installed():
    try:
        from wwb_scanner.scanner import gain_plan  # noqa: F401
        return True
    except ImportError:
        return False


H13_STABLE_DB = 1.5       # V22.12: a strong point is "stable air" when the two references agree this well
H13_MIN_STABLE = 20       # ... and the level check needs at least this many such points


def h13_compare(f, y, plan_bands, ref_gain, before, after):
    """V22.12 (PC 23.11: two DVB-T-like bands vanished between H9 and H13 and
    H13 reported -16.5 dB). The scan with own gain per band is compared with
    the mean of two scans with one gain, one before (H9) and one right after;
    levels only on strong points where those two agree within
    H13_STABLE_DB (the air did not change there). Returns (status, text)."""
    import numpy as np
    bf, by = before['f'], before['y']
    ay = np.interp(bf, after['f'], after['y'])
    yi = np.interp(bf, f, y)
    ref = (by + ay) / 2.
    fl = floor_level(ref)
    strong = (by > floor_level(by) + 20) | (ay > floor_level(ay) + 20)
    stable = strong & (np.abs(by - ay) <= H13_STABLE_DB)
    n_strong, n_stable = int(strong.sum()), int(stable.sum())
    med = float(np.median((yi - ref)[stable])) if n_stable >= H13_MIN_STABLE else None
    rows, worse = [], False
    for lo, hi, gain in plan_bands:
        if gain <= ref_gain + 0.05:
            continue
        m_new = (f >= lo) & (f <= hi)
        m_ref = (bf >= lo) & (bf <= hi)
        if m_new.sum() < 50 or m_ref.sum() < 50:
            continue
        q_new = float(np.percentile(y[m_new], 20))
        q_b = float(np.percentile(by[m_ref], 20))
        q_a = float(np.percentile(ay[m_ref], 20))
        q_ref = (q_b + q_a) / 2.
        worse = worse or q_new > q_ref + 0.5
        rows.append('%g-%g: пол %.1f / %.1f (до / после) -> %.1f дБ (%+.1f)' % (lo, hi, q_b, q_a, q_new,
                                                                              q_new - q_ref))
    if med is None:
        levels = ('уровни: эфир менялся - стабильных сильных точек %d из %d (нужно %d), '
                  'уровни не сравнивались' % (n_stable, n_strong, H13_MIN_STABLE))
        status = 'WARN' if worse else 'INFO'
    else:
        levels = 'сильные точки, где эфир стабилен (%d из %d): разница %+.2f дБ' % (n_stable, n_strong, med)
        status = 'PASS' if abs(med) <= 1.5 and not worse else 'WARN'
    return status, '%s; %s' % (levels, '; '.join(rows) or 'нет поднятых полос')


@guard('H13', 'своё усиление на полосу (V22.7)')
def h13_band_gain(lab, ref):
    """The same scan as H9/H11 (470-800, clean, Overload) with "Own gain per
    band": which gains the probe chose, whether strong carriers keep their
    level (at the gain of the scan) and how much lower the floor of the
    raised bands is. V22.12: compared with one-gain scans before (H9) and
    right after, levels only where the air stayed the same."""
    s = lab.scan('band_gain', 470, 800, clean=True, auto_gain=True, band_gain=True)
    plan = getattr(s['scanner'], 'gain_plan', None)
    if s['hung'] or s['error'] or plan is None:
        result('H13', 'FAIL', 'скан со своим усилением на полосу',
               'завис %s, ошибка %r, план %r' % (s['hung'], s['error'], plan))
        return s
    bands = '; '.join('%g-%g %.1f дБ (%s)' % (b.lo, b.hi, b.gain, b.why) for b in plan.bands)
    if not plan.banded:
        result('H13', 'INFO', 'своё усиление на полосу: усиление нигде не поднято',
               'проба %.1f с; %s' % (plan.seconds, plan.note or bands))
        return s
    after = lab.scan('band_gain_ref_after', 470, 800, clean=True, auto_gain=True, band_gain=False)
    if after['hung'] or after['error']:
        result('H13', 'FAIL', 'скан с одним усилением после скана со своим на полосу',
               'завис %s, ошибка %r' % (after['hung'], after['error']))
        return s
    status, text = h13_compare(s['f'], s['y'], [(b.lo, b.hi, b.gain) for b in plan.bands],
                               plan.ref_gain, ref, after)
    result('H13', status, 'своё усиление на полосу (470-800)',
           'проба %.1f с; полосы: %s; %s; время %.0f с против %.0f с с одним усилением' % (
               plan.seconds, bands, text, s['time'], after['time']))
    try:
        lab.save(s, 'scan_band_gain')
    except Exception as exc:
        print('export failed: %r' % (exc,))
    return s


def v228_installed():
    try:
        from wwb_scanner.scanner import watchdog  # noqa: F401
        return True
    except ImportError:
        return False


@guard('H14', 'надёжность открытия донгла (V22.8)')
def h14_open(lab):
    """V22.8 (external audit A1, A6) on the real dongle:
    a) Stop 0.2 s after Start (the dongle is still being opened): the scan
       ends before its first tile, within a few seconds (V22.7: the whole
       range was scanned);
    b) a set-up the dongle refuses (8 MS/s: LibUSBError -22 in the audit's
       run): the scan ends with an error and the next scan opens the dongle
       (V22.7: the next open waited for ever)."""
    from wwb_scanner import service
    from wwb_scanner.scanner import main
    from wwb_scanner.scanner.config import ScanConfig
    conf = lab.config(470, 700)
    svc = service.ScanService(history=None)
    t0 = time.time()
    svc.start(ScanConfig(conf))
    time.sleep(0.2)
    asked = svc.stop()
    res = svc.wait(60)
    dt = time.time() - t0
    if res is None:
        result('H14', 'FAIL', 'Stop во время открытия донгла', 'скан не закончился за 60 с')
        return
    ok_a = asked and res.stopped_by_user and res.tiles_done == 0 and dt < 10
    # the simulator opens at once: by 0.2 s the scan may already be running
    st_a = 'PASS' if ok_a else ('INFO' if lab.simulate and res.stopped_by_user else 'FAIL')
    result('H14', st_a, 'Stop во время открытия донгла (470-700)',
           'stop()=%s, остановлен=%s, плиток %d из %d, %.1f с' % (
               asked, res.stopped_by_user, res.tiles_done, res.tiles_total, dt))
    bad = lab.config(470, 480)
    bad['sampling']['sample_rate'] = 8.0e6
    sc = main.Scanner(config=bad, history=None)
    th = threading.Thread(target=sc.run_scan, daemon=True)
    # V22.9 refuses 8 MS/s before the dongle is opened (ScanConfig.device_problem);
    # here the dongle itself must refuse it, so that check is left out
    saved_check = getattr(ScanConfig, 'device_problem', None)
    if saved_check is not None:
        ScanConfig.device_problem = lambda self: None
    t0 = time.time()
    try:
        th.start()
        th.join(60)
    finally:
        if saved_check is not None:
            ScanConfig.device_problem = saved_check
    dt_bad = time.time() - t0
    if th.is_alive():
        result('H14', 'FAIL', 'отказ настройки донгла (8 МС/с)', 'скан висит больше 60 с')
        return
    refused = sc.error is not None and 'could not be set up' in str(sc.error)
    s = lab.scan('after_refused_setup', 470, 480, timeout=120)
    ok_next = not s['hung'] and s['error'] is None and s['processed'] > 0
    if refused:
        status = 'PASS' if ok_next else 'FAIL'
        what = 'донгл отказал (%s) за %.1f с' % (sc.error, dt_bad)
    else:
        status = 'INFO' if ok_next else 'FAIL'
        what = 'донгл принял 8 МС/с, отказ не воспроизведён (ошибка %r)' % (sc.error,)
    result('H14', status, 'отказ настройки донгла и следующий скан',
           '%s; следующий скан 470-480: %s, %d плиток, %.1f с' % (
               what, 'завис' if s['hung'] else ('ошибка %r' % s['error'] if s['error'] else 'OK'),
               s['processed'], s['time']))


def v229_installed():
    try:
        from wwb_scanner.receivers import gate  # noqa: F401
        return True
    except ImportError:
        return False


_H15_CHILD = r'''
import json, sys, threading, time
from wwb_scanner.scanner import main
conf = json.loads(sys.argv[1])
sc = main.Scanner(config=conf, history=None)
th = threading.Thread(target=sc.run_scan, daemon=True)
th.start()
time.sleep(float(sys.argv[2]))
sc.sdr_wrapper.close_sdr()
th.join(60)
coll = getattr(sc, 'sample_collection', None)
print('H15 alive=%s error=%r points=%d' % (th.is_alive(), sc.error, sc.spectrum.sample_data.size), flush=True)
print('H15x %s' % json.dumps({'aborted': bool(getattr(coll, 'aborted', False)),
                              'reason': str(getattr(coll, 'abort_reason', '') or ''),
                              'tiles_done': len(getattr(coll, 'processed_keys', ()) or ()),
                              'tiles_total': len(getattr(coll, 'sample_sets', {}) or {})}), flush=True)
'''


def h15_note(points, full, extra):
    """V22.11 (hod-rabot-34): what a run of H15 that ended without an
    error measured - whole, not at all, or part of the range (the dongle was
    closed before the end; the points measured until then are kept)."""
    if not points:
        return 'закрыт до первой плитки, 0 точек'
    if full and points >= full:
        return 'скан до конца'
    why = (extra or {}).get('reason') or ('чтение прервано' if (extra or {}).get('aborted') else '')
    td, tt = (extra or {}).get('tiles_done'), (extra or {}).get('tiles_total')
    tiles = ', плиток %s из %s' % (td, tt) if td is not None and tt else ''
    return 'скан неполный: точек %d из %s%s%s' % (points, full or '?', tiles, ' (%s)' % why if why else '')


@guard('H15', 'закрытие донгла во время чтения (V22.9)')
def h15_close_during_read(lab):
    """V22.9 (external audit P3-01) on the real dongle: the dongle is closed
    from another thread while the scan reads (the audit: rc=3, "Assertion
    failed: refcnt >= 2" from libusb at 1.7 and 1.9 s). In a child process,
    so a crash ends only the child. Expected: exit code 0 every time."""
    import json
    import subprocess
    if lab.simulate:
        result('H15', 'SKIP', 'закрытие донгла во время чтения', 'только на донгле')
        return
    conf = json.dumps(lab.config(470, 478))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    rows, bad, runs = [], 0, []
    for delay in (1.4, 1.7, 1.9, 1.7, 1.9):
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, '-c', _H15_CHILD, conf, str(delay)], env=env,
                               capture_output=True, text=True, timeout=120)
            rc, out = p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired:
            rc, out = 'timeout', ''
        line = [l for l in out.splitlines() if l.startswith('H15 ')]
        ext = [l for l in out.splitlines() if l.startswith('H15x ')]
        try:
            extra = json.loads(ext[-1][5:]) if ext else {}
        except ValueError:
            extra = {}
        m = re.search(r'points=(\d+)', line[-1]) if line else None
        crash = 'Assertion failed' in out or rc != 0
        bad += bool(crash)
        runs.append((delay, rc, line[-1][4:] if line else '', int(m.group(1)) if m else None, extra,
                     time.time() - t0, crash))
        if crash:
            print(out[-2000:])
        time.sleep(1.0)
    full = max([r[3] for r in runs if r[3]] or [0]) or None
    for delay, rc, text, points, extra, dt, crash in runs:
        note = '' if crash or points is None else ', ' + h15_note(points, full, extra)
        rows.append('%.1f с: rc=%s%s%s (%.0f с)' % (delay, rc, ' ' + text if text else '', note, dt))
    result('H15', 'FAIL' if bad else 'PASS', 'закрытие донгла из другого потока во время чтения, 5 раз',
           '; '.join(rows))


BANDS_H12 = [(470.0, 534.0), (606.0, 630.0), (823.0, 832.0), (863.0, 865.0)]


def v18_installed():
    try:
        from wwb_scanner import bands  # noqa: F401
        return True
    except ImportError:
        return False


@guard('H12', 'скан нескольких диапазонов (V18)')
def h12_bands(lab, h2):
    """QLX-D G51 + BLX K3E + PSM300 S8 in one run: only these ranges are
    measured, nothing in the gaps, faster than one range over all of them."""
    import numpy as np
    s = lab.scan('bands', 470, 865, ranges=BANDS_H12, clean=True, auto_gain=True)
    f = s['f']
    gaps = [(534.0, 606.0), (630.0, 823.0), (832.0, 863.0)]
    in_gap = int(sum(((f > a + 0.01) & (f < b - 0.01)).sum() for a, b in gaps))
    have = ['%g-%g: %d' % (a, b, int(((f >= a) & (f <= b)).sum())) for a, b in BANDS_H12]
    empty = [a for a, b in BANDS_H12 if not ((f >= a) & (f <= b)).any()]
    files = []
    try:
        files = list(lab.save(s, 'scan_bands'))
        p3 = os.path.join(lab.outdir, 'scan_bands.sdb3')
        s['scanner'].spectrum.export_to_file(filename=p3)
        files.append(p3)
    except Exception as exc:
        files.append('export error %r' % (exc,))
    full = None
    if h2:
        full = per_tile(h2[0]) * ((865 - 470) / 0.775 + 1)
    ok = not s['hung'] and not s['error'] and in_gap == 0 and not empty
    fast = full is None or s['time'] < 0.6 * full
    result('H12', 'PASS' if ok and fast else ('WARN' if ok else 'FAIL'),
           'скан 4 диапазонов (470-534, 606-630, 823-832, 863-865)',
           '%.1f с, плиток %d (один диапазон 470-865 ~ %s с); точек: %s; в промежутках %d; файлы: %s'
           % (s['time'], s['tiles'], '%.0f' % full if full else '?', ', '.join(have), in_gap,
              ', '.join(os.path.basename(x) for x in files if x)))
    return s


# ---------------------------------------------------------------- report

def write_report(outdir, args, started):
    counts = {}
    for r in RESULTS:
        counts[r[1]] = counts.get(r[1], 0) + 1
    lines = ['# Проверка rtlsdr-wwb-scanner на железе', '',
             'Дата: %s; длительность %.0f мин; режим: %s' % (
                 started.strftime('%Y-%m-%d %H:%M'), (time.time() - started_ts) / 60.,
                 'ИМИТАТОР' if args.simulate else ('быстрый' if args.quick else 'полный')),
             '', 'Итог: ' + ', '.join('%s %d' % kv for kv in sorted(counts.items())), '',
             '| № | Итог | Проверка | Подробности |', '|---|---|---|---|']
    for tid, st, title, det in RESULTS:
        lines.append('| %s | %s | %s | %s |' % (tid, st, title, det.replace('|', '/')))
    lines += ['', 'Файлы: console.txt (весь вывод), pytest.txt (автотесты), scan_*.csv/.sdb2 (сканы), timing_*.txt (время по этапам).']
    with io.open(os.path.join(outdir, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return counts


started_ts = time.time()


def main():
    global LOG
    ap = argparse.ArgumentParser(description='Проверка сканера на RTL-SDR V4')
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--no-pytest', action='store_true')
    ap.add_argument('--unplug', action='store_true')
    ap.add_argument('--simulate', action='store_true')
    ap.add_argument('--no-antenna', action='store_true')
    ap.add_argument('--out', default=None)
    ap.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    started = datetime.datetime.now()
    outdir = args.out or os.path.join(KIT, 'qa_hw_report_' + started.strftime('%Y%m%d-%H%M%S')
                                      + ('_no_antenna' if args.no_antenna else ''))
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    if not args.child:
        return parent(args, outdir)
    LOG = io.open(os.path.join(outdir, 'console_python.txt'), 'w', encoding='utf-8')
    sys.stdout = Tee(sys.__stdout__, LOG)
    sys.stderr = Tee(sys.__stderr__, LOG)
    print('Отчёт: %s' % outdir)
    try:
        for st in (sys.__stdout__, sys.__stderr__):
            try:
                st.reconfigure(errors='replace')     # Windows console without UTF-8
            except Exception:
                pass
        check_environment(args)
        if not args.no_pytest:
            print('\n=== Автотесты (несколько минут) ===', flush=True)
            run_pytest(outdir)
            print('\n=== Окно программы с имитатором ===', flush=True)
            run_ui_check(outdir)
        print('\n=== Донгл ===', flush=True)
        lab = Lab(outdir, simulate=args.simulate, no_antenna=args.no_antenna)
        if args.no_antenna:
            print('\n>>> АНТЕННА ДОЛЖНА БЫТЬ ОТКРУЧЕНА (пустой разъём). Enter - начать...', flush=True)
            try:
                input()
            except EOFError:
                pass
        if h1_dongle(lab) is None or any(r[0] == 'H1' and r[1] == 'FAIL' for r in RESULTS):
            print('Донгл не работает - остальные проверки пропущены.')
        elif args.no_antenna:
            h0_warmup(lab)
            h8 = h8_no_antenna(lab)
            if v17_installed() and h8:
                h8d_calibrate_clean(lab, h8)
        else:
            h0_warmup(lab)
            h2 = h2_three_scans(lab)
            if v17_installed() and h2:
                h9 = h9_auto_gain(lab, h2)
                h10_gain_steps(lab, h2[0])
                if h9:
                    h11_clean(lab, h9[0])
                    if v227_installed():
                        h13_band_gain(lab, h9[0])
            if v228_installed():
                h14_open(lab)
            if v229_installed():
                h15_close_during_read(lab)
            if v2210_installed():
                h13b_band_gain_vhf(lab)
                h16_rates(lab)
            if v18_installed():
                h12_bands(lab, h2)
            v10 = any(r[0] == 'E3' and r[1] == 'PASS' for r in RESULTS)
            if v10:
                h4_windows(lab)
                h5_overlap(lab)
            else:
                # without V10 these settings hang the old version (by design of the test)
                result('H4', 'SKIP', 'окна', 'сначала установите V10 (E3)')
                result('H5', 'SKIP', 'Overlap Ratio', 'сначала установите V10 (E3)')
            if not args.quick:
                h3_full(lab)
                h6_series(lab)
            if args.unplug and not args.simulate:
                h7_unplug(lab)
    except KeyboardInterrupt:
        result('X', 'WARN', 'прервано пользователем (Ctrl+C)')
    finally:
        counts = write_report(outdir, args, started)
        print('\nГотово (без D1): %s' % ', '.join('%s %d' % kv for kv in sorted(counts.items())))
        sys.stdout = sys.__stdout__
        LOG.close()
    os._exit(0)


def parent(args, outdir):
    """Run the checks in a child process and keep everything it prints,
    including what the rtlsdr DLL writes to stderr from C (that does not go
    through Python's sys.stderr). Then add D1 to the report."""
    cmd = [sys.executable, '-u', os.path.abspath(__file__)] + sys.argv[1:] + ['--child', '--out', outdir]
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    out = sys.__stdout__
    try:
        out.reconfigure(errors='replace')
    except Exception:
        pass
    msgs = []
    with io.open(os.path.join(outdir, 'console.txt'), 'w', encoding='utf-8') as log:
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for raw in iter(p.stdout.readline, b''):
                line = raw.decode('utf-8', 'replace')
                log.write(line)
                log.flush()
                try:
                    out.write(line)
                    out.flush()
                except Exception:
                    pass
                if DRIVER_MSG.search(line):
                    msgs.append(line.strip())
            p.wait()
        except KeyboardInterrupt:
            p.wait()
    rep = os.path.join(outdir, 'report.md')
    if os.path.exists(rep):
        uniq = []
        for m in msgs:
            if m not in uniq:
                uniq.append(m)
        det = ('%d сообщ.: %s' % (len(msgs), '; '.join(uniq[:6]))) if msgs else 'нет'
        row = '| D1 | %s | сообщения драйвера (DLL) в консоли | %s |' % ('INFO' if msgs else 'PASS', det.replace('|', '/'))
        text = read(rep)
        marker = '\nФайлы:'
        text = text.replace(marker, row + '\n' + marker, 1) if marker in text else text + row + '\n'
        with io.open(rep, 'w', encoding='utf-8') as f:
            f.write(text)
    print('\nОтчёт: %s' % rep)
    print('Заархивируйте папку %s и пришлите.' % outdir)
    return 0


if __name__ == '__main__':
    main()
