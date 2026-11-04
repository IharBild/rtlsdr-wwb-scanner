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

Python 3.7+. Код сканера берётся из установленного пакета (site-packages),
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
    import numpy, scipy
    info = {
        'python': sys.version.split()[0] + (' 64-bit' if sys.maxsize > 2 ** 32 else ' 32-bit'),
        'os': platform.platform(),
        'numpy': numpy.__version__,
        'scipy': scipy.__version__,
    }
    for mod in ('rtlsdr', 'PySide2', 'tinydb', 'pytest'):
        try:
            m = __import__(mod)
            info[mod] = getattr(m, '__version__', 'есть')
        except Exception as exc:
            info[mod] = 'нет (%s)' % type(exc).__name__
    try:
        import pkg_resources
        info['pyrtlsdr'] = pkg_resources.get_distribution('pyrtlsdr').version
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
            result('E2', 'WARN', 'версия pyrtlsdr', '%s: нужна DLL с rtlsdr_set_dithering; держите pyrtlsdr<0.4' % v)
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
    m13 = 'TINYDB4' in read(os.path.join(base, 'utils', 'dbstore.py'))
    try:
        import tinydb
        tdb = getattr(tinydb, '__version__', '?')
    except Exception:
        tdb = '?'
    result('E3d', 'PASS' if m13 or not tdb.startswith('4') else 'FAIL', 'база сканов работает с вашей tinydb (V13)',
           'tinydb %s; %s' % (tdb, 'dbstore.py V13 установлен' if m13 else
                              'dbstore.py старый: с tinydb 4 не работают повторное сохранение и чтение сканов из базы - поставьте fix-V13'))
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
        p = subprocess.run([sys.executable, ui, '0', '1766'], cwd=outdir, env=env,
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
        spmod.Spectrum.save_to_dbstore = lambda self: None
        self.home_report = os.path.join(os.path.expanduser('~'), 'wwb_scan_timing.txt')

    def config(self, start, end, window_type='hann', overlap=0.65, sweeps=40, auto_gain=False, clean=False):
        c = {
            'scan_range': [float(start), float(end)],
            'device': {'gain': 33.8},
            'sampling': {'sample_rate': 2.4e6, 'sweep_overlap_ratio': overlap,
                         'sweeps_per_scan': sweeps, 'samples_per_sweep': 2048,
                         'window_size': 96, 'window_type': window_type},
        }
        if auto_gain:
            c['sampling']['auto_gain'] = True          # V17
        if clean:
            c['processing'] = {'clean_ripple': True, 'clean_spurs': True, 'clean_floor': True}
        return c

    def scan(self, name, start, end, timeout=1200, **kw):
        from wwb_scanner.scanner import main
        import numpy as np
        print('\n--- скан %s: %.1f-%.1f МГц %s' % (name, start, end, kw or ''), flush=True)
        sc = main.Scanner(config=self.config(start, end, **kw))
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


def quiet_floor(f, y, lo, hi):
    """std of the floor in a band, after removing signals (points > median+3 dB)."""
    import numpy as np
    m = (f >= lo) & (f <= hi)
    v = y[m]
    if v.size < 50:
        return None
    med = np.median(v)
    q = v[v < med + 3]
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
    return s


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
    best = None
    for lo in [x for x in np.arange(470, 751, 10) if x + 50 <= 703 or x >= 748]:   # not LTE 700 uplink
        q = quiet_floor(f, y, lo, lo + 50)
        if q and (best is None or q['share_signal'] < best[1]['share_signal']):
            best = (lo, q)
    if best:
        lo, q = best
        rip = tile_ripple(f, y, lo, lo + 50)
        # with an antenna there is often no truly empty 50 MHz in 470-800
        # (DVB-T, LTE 700, wireless systems), so a rougher floor is INFO
        st = 'PASS' if q['std'] <= 0.5 and (rip or 0) <= 0.8 else ('INFO' if q['std'] <= 1.0 else 'WARN')
        result('H2b', st, 'ровность шума (самая тихая полоса %.0f-%.0f МГц)' % (lo, lo + 50),
               'std %.2f дБ (у V7 0,16-0,21), рябь на периоде плитки %s дБ (у V7 0,4-0,7), макс. скачок %.2f дБ'
               % (q['std'], '%.2f' % rip if rip is not None else '?', q['step_max']))
    # repeatability between scan 1 and 2 on common grid, quiet points only;
    # tiles where the ADC clipped (V16 overload detector) are left out: a
    # clipped tile is not repeatable by nature
    a, b = scans[0], scans[1]
    ov = (a.get('overload') or []) + (b.get('overload') or [])
    if a['f'].size and a['f'].size == b['f'].size and np.allclose(a['f'], b['f']):
        d = a['y'] - b['y']
        quiet = a['y'] < np.median(a['y']) + 3
        clean = np.ones(a['f'].size, bool)
        for lo, hi, _ in ov:
            clean &= ~((a['f'] >= lo - 1) & (a['f'] <= hi + 1))
        m = quiet & clean
        sd = float(np.std(d[m])) if m.any() else float('nan')
        result('H2c', 'PASS' if sd <= 0.5 else 'WARN', 'повторяемость скан 1 / скан 2',
               'тихие точки вне перегрузки: среднее %+.2f дБ, std %.2f дБ (у NEW6-2 0,12); с перегрузкой %.2f; '
               'исключено %.0f %% точек' % (np.mean(d[m]), sd, np.std(d[quiet]), 100. * (1 - clean.mean())))
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
    for w in ('hann', 'blackmanharris', 'general_hamming', 'general_cosine', 'flattop'):
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
    z = _zone_mask(f, ov)
    rep_off = float(np.std((h2[0]['y'] - h2[1]['y'])[z]))
    rep_on = float(np.std((a['y'] - b['y'])[z]))
    # strongest points of the zone: level with and without auto gain
    top = np.argsort(np.where(z, h2[0]['y'], -999))[-20:]
    dl = float(np.median(a['y'][top] - h2[0]['y'][top]))
    ok = rep_on <= max(1.0, 0.7 * rep_off) and a['still_clipped'] == 0
    result('H9', 'PASS' if ok else 'WARN', 'автоусиление при перегрузке (470-800)',
           'зоны %s МГц; перемеряно плиток %d, осталось с перегрузкой %d; повторяемость в зонах %.2f -> %.2f дБ; '
           'уровень сильнейших точек зон %+.1f дБ (при перегрузке он искажён: сжатие и ложные продукты); время %.1f -> %.1f с'
           % (', '.join('%.1f-%.1f' % (lo, hi) for lo, hi, _ in ov[:6]), a['remeasured'], a['still_clipped'],
              rep_off, rep_on, dl, t_off, t_on))
    return scans


@guard('H10', 'точность шагов усиления (V17)')
def h10_gain_steps(lab, s):
    """Auto gain scales a re-measured tile by the nominal gain difference.
    Check on a strong (not clipped) signal how exact the nominal steps are."""
    import numpy as np
    from scipy.signal import welch
    from wwb_scanner.scanner import cleanup as C
    from wwb_scanner.scanner.sample_processing import clip_ratio
    f, y = s['f'], s['y']
    z = _zone_mask(f, s.get('overload') or [], pad=3.0)
    floor = C.running_median(y, 161)
    e = np.where(z, -99, y - floor)
    i = int(np.argmax(e))
    if e[i] < 20:
        result('H10', 'INFO', 'точность шагов усиления', 'нет сильного сигнала (> 20 дБ над полом) вне перегрузки')
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
            fr, p = welch(x, fs=2.4e6, window='hann', nperseg=96, return_onesided=False, detrend=False)
            o = np.argsort(fr)
            fr, p = fr[o], p[o]
            k = int(np.argmin(np.abs(fr + off)))
            lvl = 10 * np.log10(p[k - 2:k + 3].mean()) + (g0 - g)
            if ref is None:
                ref = lvl
            rows.append((g, lvl - ref, clip_ratio(x)))
    finally:
        try:
            sdr.close()
        except Exception:
            pass
    bad = [r for r in rows[1:] if abs(r[1]) > 1.5 and r[2] < 1e-3]
    result('H10', 'WARN' if bad else 'PASS', 'точность шагов усиления (сигнал %.3f МГц, +%.0f дБ)' % (fpk, e[i]),
           'отклонение уровня после пересчёта, дБ: %s (норма +-1,5)%s'
           % (', '.join('%.1f дБ: %+.1f%s' % (g, d, ' перегр.' if c >= 1e-3 else '') for g, d, c in rows),
              '; автоусиление даёт ошибку уровня на этих шагах' if bad else ''))


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
