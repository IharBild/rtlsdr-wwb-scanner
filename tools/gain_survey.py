# -*- coding: utf-8 -*-
"""Замер всех ступеней усиления по всему диапазону (V20.5.4).

Каждая плитка (2,4 МГц, через каждые --step МГц) читается на ВСЕХ ступенях
усиления тюнера подряд: вверх 0 -> 49,6 и обратно вниз. Так все 29 замеров
одной частоты сделаны за ~3 с - эфир не успевает измениться, а сигналы,
которые всё же изменились (LTE, GSM), видны по разнице проходов.

Запуск (программа сканера закрыта):
    python gain_survey.py --antenna       антенна на обычном месте, СВОИ передатчики
                                          ВЫКЛЮЧЕНЫ (~25 мин при шаге 4 МГц)
    python gain_survey.py --no-antenna    антенна откручена (или заглушка 50 Ом), ~25 мин
    python gain_survey.py --simulate      без донгла, на имитаторе (проверка скрипта)
Ключи: --step 4 (МГц), --lo 30 --hi 1766, --passes 2.

После обоих замеров (V20.6):
    python gain_survey.py --analyze       берёт последние gain_survey_ant_* и
                                          gain_survey_noant_* рядом со скриптом
    python gain_survey.py --analyze ANT.zip NOANT.zip
строит профиль усиления (потери чувствительности и клиппинг по полосам) и
сохраняет его в wwb_scanner_data/calibration/gain_profile.json: кнопка
"Suggest gain" в программе берёт его вместо встроенного (Варшава, 10.11.2026).

Результат: папка gain_survey_<режим>_<дата-время> и такой же .zip рядом со
скриптом. Пришлите оба архива (с антенной и без).
Внутри: levels.csv (на каждую плитку, ступень и проход: уровни, клиппинг),
psd.npz (спектры), meta.json, console.txt (всё, что писали программа и DLL).
Данные сохраняются по ходу работы: прерванный замер (Ctrl+C, сбой) тоже годится.
Пока идёт замер, Windows не уходит в сон.

Python 3.7+. Код сканера берётся из установленного пакета.
"""
from __future__ import print_function

import argparse
import datetime
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = HERE if os.path.isdir(os.path.join(HERE, 'tests')) else os.path.dirname(HERE)
TESTS = os.path.join(KIT, 'tests')

FS = 2.4e6
DISCARD = 16384
READ = 65536              # 27 ms at 2.4 MS/s
NPERSEG = 256             # 9.4 kHz bins
KEEP_HZ = (50e3, 0.9e6)   # away from DC, inside the flat part of the tile
MAX_ERRORS = 10
SAVE_EVERY = 10           # tiles
DRIVER_MSG = re.compile(r'(i2c wr failed|i2c rd failed|PLL not locked|No valid PLL|LIBUSB_ERROR)', re.I)


def keep_awake():
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        print('Windows: sleep is held off while the survey runs', flush=True)
    except Exception:
        pass


# --------------------------------------------------------------- dongle

class Dongle(object):
    def __init__(self, simulate=False, antenna=True):
        self.simulate = simulate
        self.antenna = antenna
        self.sdr = None
        if simulate:
            sys.path.insert(0, TESTS)
            import conftest  # noqa: F401  (rtlsdr stub if needed)
            import sim
            self.sim = sim
            air = sim.Air(noise=0.003 if antenna else 0.0003)
            for k in range(2, 62):
                air.tone(28.8e6 * k, 0.004)            # own spurs
            if antenna:
                air.band(88e6, 108e6, 0.3).band(202e6, 206e6, 0.15).band(600e6, 607.6e6, 0.1)
                air.band(950e6, 955e6, 0.08).tone(526.0e6, 0.9).tone(486.825e6, 0.2)
            self.air = air

    def open(self):
        if self.simulate:
            self.sdr = self.sim.FakeRtlSdr(air=self.air, gain_affects=True,
                                           gain_error=self.sim.vhf_gain_error)
            # V21.3: never hand the simulated dongle to a real rtlsdr library.
            # pyrtlsdr 0.4+ finds librtlsdr where 0.3 did not, and its I2C
            # counter read the fake device pointer: a crash at the end of a
            # simulated survey (Linux; Windows turns it into an exception).
            from wwb_scanner import scan_dll
            scan_dll._cache['lib'] = None
        else:
            from rtlsdr import RtlSdr
            self.sdr = RtlSdr()
        self.sdr.sample_rate = FS
        return self.sdr

    def close(self):
        try:
            if self.sdr is not None:
                self.sdr.close()
        except Exception:
            pass
        self.sdr = None

    def reopen(self, wait_s=10.):
        self.close()
        t_end = time.time() + wait_s
        while time.time() < t_end:
            time.sleep(1.)
            try:
                return self.open()
            except Exception as exc:
                print('  dongle not back yet: %s' % exc, flush=True)
        return None


def psd(x):
    import numpy as np
    from scipy.signal import welch
    f, p = welch(np.asarray(x), fs=FS, window='hann', nperseg=NPERSEG,
                 return_onesided=False, detrend=False)
    keep = (np.abs(f) >= KEEP_HZ[0]) & (np.abs(f) <= KEEP_HZ[1])
    o = np.argsort(f[keep])
    return f[keep][o], p[keep][o]


def read_tile(sdr, fc, gains, passes, call):
    """[(pass, gain_set, psd, clip_ratio, peak_abs)] for one tile."""
    import numpy as np
    from wwb_scanner.scanner.sample_processing import clip_ratio
    call(sdr.set_center_freq, fc)
    rows = []
    for n in range(passes):
        order = list(gains) if n % 2 == 0 else list(gains)[::-1]
        for g in order:
            call(setattr, sdr, 'gain', g)
            try:
                gs = float(sdr.gain)
            except Exception:
                gs = float(g)
            call(sdr.read_samples, DISCARD)
            x = np.asarray(call(sdr.read_samples, READ))
            a = np.maximum(np.abs(x.real), np.abs(x.imag))
            clip = float(clip_ratio(x))              # the scanner's own test (8-bit full scale)
            _, p = psd(x)
            rows.append((n, gs, p, clip, float(a.max())))
    return rows


# --------------------------------------------------------------- run

def survey(args, outdir):
    import numpy as np
    keep_awake()
    dongle = Dongle(simulate=args.simulate, antenna=not args.no_antenna)   # (simulator: stub first)
    from wwb_scanner.scanner.sample_processing import run_with_timeout, READ_TIMEOUT
    sdr = dongle.open()
    gains = sorted(g / 10. for g in sdr.get_gains())
    centers = []
    f = args.lo
    while f <= args.hi + 1e-6:
        centers.append(round(f, 3))
        f += args.step
    meta = {'mode': 'no-antenna' if args.no_antenna else 'antenna', 'started': str(datetime.datetime.now()),
            'step_mhz': args.step, 'lo': args.lo, 'hi': args.hi, 'passes': args.passes, 'gains': gains,
            'sample_rate': FS, 'read': READ, 'nperseg': NPERSEG, 'keep_hz': KEEP_HZ,
            'simulate': bool(args.simulate), 'python': sys.version.split()[0], 'platform': sys.platform}
    try:
        import wwb_scanner
        meta['wwb_scanner'] = os.path.dirname(wwb_scanner.__file__)
    except Exception:
        pass
    freqs, _ = psd(np.zeros(NPERSEG * 4, dtype=np.complex64))
    meta['bin_offsets_hz'] = [float(v) for v in freqs]
    print('gain survey: %d tiles %.0f-%.0f MHz every %.1f MHz, %d gains x %d passes, %s'
          % (len(centers), args.lo, args.hi, args.step, len(gains), args.passes, meta['mode']), flush=True)

    def call(fn, *a):
        return run_with_timeout(fn, READ_TIMEOUT, *a)

    csv_path = os.path.join(outdir, 'levels.csv')
    with io.open(csv_path, 'w', encoding='utf-8') as fh:
        fh.write('fc_mhz,pass,gain,p10_db,median_db,p90_db,max_db,clip_ratio,peak_abs,seconds\n')
    spectra = {}            # fc -> array [pass, gain, bins] (dB, float16)
    errors, skipped = 0, []
    t0 = time.time()
    done = 0
    try:
        for i, fc in enumerate(centers):
            t1 = time.time()
            try:
                rows = read_tile(sdr, fc * 1e6, gains, args.passes, call)
            except Exception as exc:
                errors += 1
                skipped.append(fc)
                print('tile %.3f MHz failed: %s' % (fc, exc), flush=True)
                if errors >= MAX_ERRORS:
                    print('too many dongle errors, stopping', flush=True)
                    break
                if not getattr(sdr, 'device_opened', True):
                    sdr = dongle.reopen()
                    if sdr is None:
                        print('the dongle did not come back, stopping', flush=True)
                        break
                    print('dongle opened again', flush=True)
                continue
            dt = time.time() - t1
            arr = np.full((args.passes, len(gains), len(freqs)), np.nan, dtype=np.float32)
            with io.open(csv_path, 'a', encoding='utf-8') as fh:
                for n, gs, p, clip, peak in rows:
                    d = 10. * np.log10(np.maximum(p, 1e-30))
                    gi = int(np.argmin(np.abs(np.array(gains) - gs)))
                    arr[n, gi] = d
                    fh.write('%.3f,%d,%.1f,%.2f,%.2f,%.2f,%.2f,%.5f,%.4f,%.2f\n' % (
                        fc, n, gs, np.percentile(d, 10), np.median(d), np.percentile(d, 90), d.max(),
                        clip, peak, dt))
            spectra[fc] = arr.astype(np.float16)
            done += 1
            if done % SAVE_EVERY == 0 or i == len(centers) - 1:
                _save(outdir, spectra, meta, gains, freqs, skipped, done, len(centers))
            if done % 10 == 0 or i == len(centers) - 1:
                el = time.time() - t0
                eta = el / done * (len(centers) - i - 1)
                print('  %d/%d tiles (%.1f MHz), %.1f s/tile, %.0f min left'
                      % (i + 1, len(centers), fc, el / done, eta / 60.), flush=True)
    except KeyboardInterrupt:
        print('stopped by Ctrl+C - saving what was measured', flush=True)
    finally:
        meta['finished'] = str(datetime.datetime.now())
        meta['tiles_done'] = done
        meta['errors'] = errors
        try:
            from wwb_scanner import scan_dll
            meta['dll'] = scan_dll.build_info()
            i2c = scan_dll.i2c_stats(sdr) if sdr is not None else None
            if i2c is not None:
                # DLL scanner-v5: tuner I2C transfers it tried again by itself
                meta['i2c_retried'], meta['i2c_failed'] = i2c
                print('tuner I2C: %d transfer(s) retried, %d failed after retries' % i2c, flush=True)
        except Exception:
            pass
        _save(outdir, spectra, meta, gains, freqs, skipped, done, len(centers))
        dongle.close()
    print('measured %d of %d tiles in %.1f min, %d error(s)' % (done, len(centers), (time.time() - t0) / 60., errors),
          flush=True)
    _summary(outdir, gains)


def _save(outdir, spectra, meta, gains, freqs, skipped, done, total):
    import numpy as np
    keys = sorted(spectra)
    meta = dict(meta, skipped_mhz=skipped, tiles_done=done, tiles_total=total)
    with io.open(os.path.join(outdir, 'meta.json'), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, indent=1)
    if keys:
        tmp = os.path.join(outdir, 'psd_tmp.npz')
        np.savez_compressed(tmp, fc_mhz=np.array(keys), gains=np.array(gains), bin_offsets_hz=freqs,
                            db=np.stack([spectra[k] for k in keys]))
        os.replace(tmp, os.path.join(outdir, 'psd.npz'))


def _summary(outdir, gains):
    """A short look at the data (the real analysis is done later on both runs)."""
    import numpy as np
    path = os.path.join(outdir, 'levels.csv')
    try:
        a = np.genfromtxt(path, delimiter=',', names=True)
    except Exception:
        return
    if a.size == 0:
        return
    a = np.atleast_1d(a)
    bands = ((30, 88), (88, 108), (108, 174), (174, 240), (240, 470), (470, 700), (700, 960), (960, 1766))
    lines = ['median level (dBFS) of the 10th percentile per tile; share of tiles with clipping >= 0.1 %', '',
             'gain  ' + ''.join('%13s' % ('%d-%d' % b) for b in bands)]
    for g in gains:
        cells = []
        for lo, hi in bands:
            m = (a['gain'] == g) & (a['fc_mhz'] >= lo) & (a['fc_mhz'] < hi) & (a['pass'] == 0)
            if not m.any():
                cells.append('%13s' % '-')
                continue
            cells.append('%8.1f/%3d%%' % (np.median(a['p10_db'][m]), round(100. * np.mean(a['clip_ratio'][m] >= 1e-3))))
        lines.append('%4.1f  %s' % (g, ''.join(cells)))
    text = '\n'.join(lines) + '\n'
    with io.open(os.path.join(outdir, 'summary.txt'), 'w', encoding='utf-8') as fh:
        fh.write(text)
    print('\n' + text, flush=True)


# --------------------------------------------------------------- parent

def parent(args, outdir):
    """Run in a child process and keep everything it prints, including what
    the rtlsdr DLL writes to stderr from C."""
    cmd = [sys.executable, '-u', os.path.abspath(__file__)] + sys.argv[1:] + ['--child', '--out', outdir]
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    out = sys.__stdout__
    n_driver = 0
    with io.open(os.path.join(outdir, 'console.txt'), 'w', encoding='utf-8') as log:
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for raw in iter(p.stdout.readline, b''):
                line = raw.decode('utf-8', 'replace')
                log.write(line)
                log.flush()
                if DRIVER_MSG.search(line):
                    n_driver += 1
                try:
                    out.write(line)
                    out.flush()
                except Exception:
                    pass
            p.wait()
        except KeyboardInterrupt:
            p.wait()
    zpath = outdir.rstrip('\\/') + '.zip'
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(outdir)):
            z.write(os.path.join(outdir, name), os.path.join(os.path.basename(outdir), name))
    print('\ndriver messages: %d' % n_driver)
    print('Result: %s\nSend this file.' % zpath)
    return p.returncode


def _latest(kind):
    found = []
    for n in os.listdir(HERE):
        if n.startswith('gain_survey_%s_' % kind) and '_SIM' not in n:
            full = os.path.join(HERE, n)
            if os.path.isdir(full) or n.endswith('.zip'):
                found.append(n[:-4] if n.endswith('.zip') else n)
    if not found:
        return None
    n = sorted(found)[-1]
    full = os.path.join(HERE, n)
    return full if os.path.isdir(full) else full + '.zip'


def analyze(paths, out=None):
    """Gain profile from an antenna and a no-antenna survey -> gain_profile.json."""
    if paths:
        if len(paths) != 2:
            print('--analyze takes two surveys: ANTENNA NO-ANTENNA')
            return 2
        ant, noant = paths
    else:
        ant, noant = _latest('ant'), _latest('noant')
        if not ant or not noant:
            print('No gain_survey_ant_* and gain_survey_noant_* next to this script')
            return 2
    print('antenna:    %s\nno antenna: %s' % (ant, noant))
    try:
        import rtlsdr  # noqa: F401
    except Exception:           # the analysis needs no dongle and no DLL
        import types
        stub = types.ModuleType('rtlsdr')
        stub.RtlSdr = object
        sys.modules['rtlsdr'] = stub
    from wwb_scanner.scanner import gain_profile
    prof = gain_profile.build_profile(ant, noant)
    if not prof['bands']:
        print('Nothing to analyze: no band has two tiles in both surveys')
        return 1
    gains = prof['gains']
    print('\n%-10s %5s  %-6s %s' % ('band, MHz', 'tiles', 'best', 'loss vs best, dB / tiles clipping, % at 22.9 29.7 36.4 43.4 49.6'))
    for b in prof['bands']:
        best = gains[min(range(len(gains)), key=lambda k: b['loss'][k])]
        cols = ['%4.1f/%3d' % (b['loss'][gains.index(g)], round(100 * b['clip'][gains.index(g)]))
                for g in (22.9, 29.7, 36.4, 43.4, 49.6) if g in gains]
        print('%4d-%-5d %5d  %-6.1f %s' % (b['lo'], b['hi'], b['tiles'], best, '  '.join(cols)))
    path = gain_profile.save_profile(prof, out)
    print('\nSaved: %s\n"Suggest gain" in the scanner now uses this profile.' % path)
    return 0


def main():
    ap = argparse.ArgumentParser(description='Gain steps x frequency survey of the RTL-SDR V4')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--antenna', action='store_true', help='antenna connected, own transmitters OFF')
    mode.add_argument('--no-antenna', action='store_true', help='antenna disconnected')
    mode.add_argument('--analyze', nargs='*', metavar='SURVEY',
                      help='gain profile from ANTENNA and NO-ANTENNA surveys (default: the latest ones here)')
    ap.add_argument('--simulate', action='store_true')
    ap.add_argument('--step', type=float, default=4.0, help='MHz between tiles (default 4)')
    ap.add_argument('--lo', type=float, default=30.0)
    ap.add_argument('--hi', type=float, default=1766.0)
    ap.add_argument('--passes', type=int, default=2, choices=(1, 2))
    ap.add_argument('--out', default=None)
    ap.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.analyze is not None:
        return analyze(args.analyze, args.out)
    if not (args.antenna or args.no_antenna):
        ap.error('say --antenna or --no-antenna')
    if args.child:
        survey(args, args.out)
        return 0
    name = 'gain_survey_%s_%s%s' % ('noant' if args.no_antenna else 'ant',
                                     datetime.datetime.now().strftime('%Y%m%d-%H%M%S'),
                                     '_SIM' if args.simulate else '')
    outdir = os.path.join(HERE, name)
    os.makedirs(outdir)
    if args.no_antenna and not args.simulate:
        print('ANTENNA OFF (or a 50 ohm load) - the survey starts in 5 s', flush=True)
        time.sleep(5)
    elif not args.simulate:
        print('Antenna ON, your own transmitters OFF - the survey starts in 5 s', flush=True)
        time.sleep(5)
    return parent(args, outdir)


if __name__ == '__main__':
    sys.exit(main())
