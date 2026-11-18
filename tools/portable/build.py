"""V22.5: build the portable program (plan of 18.11.2026, stage 4).

    python tools/portable/build.py [--out DIST] [--no-zip] [--dll PATH]

Needs: the scanner installed in this Python (pip install .), pyinstaller.
Makes DIST/WWB-Scanner (wwb_scanner.exe, _internal, rtl-tools, README) and
DIST/WWB-Scanner-<version>-<platform>.zip, then runs the exe with --selftest
(window loads, dongle library loads). Exit code 0 when the build passed.

On Windows the dongle library is drivers/rtlsdr-scanner-v7/x64/rtlsdr.dll
(the scanner build). Elsewhere (a test of the build) give --dll.
"""
import argparse
import datetime
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WIN = sys.platform == 'win32'
DRIVER = os.path.join(ROOT, 'drivers', 'rtlsdr-scanner-v7', 'x64')
RTL_TOOLS = ('rtl_test.exe', 'rtl_eeprom.exe')


def version():
    from importlib import metadata
    return metadata.version('rtlsdr-wwb-scanner')


def git_rev():
    try:
        return subprocess.check_output(['git', '-C', ROOT, 'rev-parse', '--short', 'HEAD'],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return os.environ.get('GITHUB_SHA', '')[:7] or 'unknown'


def run(cmd, **kw):
    print('>', ' '.join(cmd), flush=True)
    return subprocess.run(cmd, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist'))
    ap.add_argument('--no-zip', action='store_true')
    ap.add_argument('--dll', help='dongle library to put into the build (default on Windows: driver v7)')
    args = ap.parse_args(argv)

    out = os.path.abspath(args.out)
    work = os.path.join(out, 'build')
    app = os.path.join(out, 'WWB-Scanner')
    if os.path.isdir(app):
        shutil.rmtree(app)
    r = run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
             '--distpath', out, '--workpath', work, os.path.join(HERE, 'wwb_scanner.spec')])
    if r.returncode:
        return r.returncode
    internal = os.path.join(app, '_internal')

    # the dongle library: next to Python inside _internal (wwb_scanner/dllpath.py)
    dll = args.dll or (os.path.join(DRIVER, 'rtlsdr.dll') if WIN else None)
    if dll:
        shutil.copy2(dll, os.path.join(internal, os.path.basename(dll)))
    if WIN:
        tools = os.path.join(app, 'rtl-tools')
        os.makedirs(tools, exist_ok=True)
        for n in RTL_TOOLS + ('rtlsdr.dll',):
            shutil.copy2(os.path.join(DRIVER, n), os.path.join(tools, n))
    shutil.copy2(os.path.join(HERE, 'README_PORTABLE.txt'), os.path.join(app, 'README_PORTABLE.txt'))
    ver = version()
    import PySide6
    with open(os.path.join(app, 'VERSION.txt'), 'w', encoding='utf-8') as fh:
        fh.write('rtlsdr-wwb-scanner %s (%s)\nbuilt %s on %s, Python %s, PySide6 %s\n'
                 'dongle library: %s\n' % (
                     ver, git_rev(), datetime.datetime.now().isoformat(timespec='seconds'),
                     platform.platform(), platform.python_version(), PySide6.__version__,
                     os.path.basename(dll) if dll else 'none (test build)'))

    # selftest of the build itself, with a temporary data folder
    exe = os.path.join(app, 'wwb_scanner.exe' if WIN else 'wwb_scanner')
    tmp = tempfile.mkdtemp(prefix='wwb_selftest_')
    report = os.path.join(out, 'selftest_build.txt')
    env = dict(os.environ, WWB_SCANNER_DATA=tmp)
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    r = run([exe, '--selftest', report], env=env, timeout=300)
    text = open(report, encoding='utf-8').read() if os.path.exists(report) else '(no report)'
    print(text)
    shutil.rmtree(tmp, ignore_errors=True)
    selftest_ok = r.returncode == 0

    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(app) for f in fs)
    print('build: %s, %.0f MB, selftest %s' % (app, size / 1e6, 'passed' if selftest_ok else 'FAILED'))
    if not args.no_zip:
        plat = 'win64' if WIN else '%s-%s' % (sys.platform, platform.machine())
        zpath = os.path.join(out, 'WWB-Scanner-%s-%s-portable.zip' % (ver, plat))
        with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for d, _, fs in os.walk(app):
                for f in fs:
                    p = os.path.join(d, f)
                    z.write(p, os.path.relpath(p, out))
        print('zip: %s, %.0f MB' % (zpath, os.path.getsize(zpath) / 1e6))
    return 0 if selftest_ok else 1


if __name__ == '__main__':
    sys.exit(main())
