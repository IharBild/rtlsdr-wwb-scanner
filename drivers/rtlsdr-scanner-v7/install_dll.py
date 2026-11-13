# -*- coding: utf-8 -*-
"""Установка rtlsdr.dll scanner-v7 для pyrtlsdr 0.3 (Python 3.7) и 0.4-0.5
(Python 3.10+, новое окружение).

    python install_dll.py            установить (сканер должен быть закрыт)
    python install_dll.py where      только показать, какая DLL загружается
    python install_dll.py rollback   вернуть предыдущую DLL из резервной копии

Если pyrtlsdr уже загружает какую-то DLL, она заменяется, копия кладётся
рядом: <имя>.before-v7 (не перезаписывается).
Если не загружает ничего (новое окружение) или загружает старую, без
функций, которые нужны pyrtlsdr 0.4+, DLL ставится как
<окружение>\\Scripts\\rtlsdr.dll; программа (V21.3+) добавляет эту папку
в поиск DLL сама.
Python 3.3+, только 64-битный Windows Python (DLL из папки x64).
"""
from __future__ import print_function

import hashlib
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.join(HERE, 'x64', 'rtlsdr.dll')
TAG = 'scanner-v7'
SUFFIX = '.before-v7'

# the same search as wwb_scanner/dllpath.py (V21.3), then pyrtlsdr
PROBE = r'''
import ctypes, os, sys
if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
    for d in (os.path.join(sys.prefix, 'Scripts'), sys.prefix):
        if any(os.path.isfile(os.path.join(d, n)) for n in ('rtlsdr.dll', 'librtlsdr.dll')):
            _h = os.add_dll_directory(d)
            break
from rtlsdr.librtlsdr import librtlsdr as lib   # rtlsdr.librtlsdr in the package is the DLL itself
path = ''
if sys.platform == 'win32':
    buf = ctypes.create_unicode_buffer(32768)
    k = ctypes.windll.kernel32
    k.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    if k.GetModuleFileNameW(ctypes.c_void_p(lib._handle), buf, 32768):
        path = buf.value
else:
    path = getattr(lib, '_name', '')
info = 'stock (no build info)'
if hasattr(lib, 'rtlsdr_get_build_info'):
    lib.rtlsdr_get_build_info.restype = ctypes.c_char_p
    info = lib.rtlsdr_get_build_info().decode('ascii', 'replace')
print('PATH=' + path)
print('INFO=' + info)
'''


def probe():
    """Ask a fresh Python which DLL pyrtlsdr loads (this process must not
    load it: Windows does not let a loaded DLL be replaced).
    Returns (path, build info, output)."""
    r = subprocess.run([sys.executable, '-c', PROBE], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode('utf-8', 'replace')
    path = info = None
    for line in out.splitlines():
        if line.startswith('PATH='):
            path = line[5:].strip()
        elif line.startswith('INFO='):
            info = line[5:].strip()
    return path, info, out


def sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def default_target():
    """Where the DLL goes when pyrtlsdr loads none: Scripts of this Python."""
    if sys.platform != 'win32':
        return None
    return os.path.join(sys.prefix, 'Scripts', 'rtlsdr.dll')


def why(out):
    if "rtlsdr_set_dithering" in out or "rtlsdr_set_gpio" in out:
        return ('pyrtlsdr found a DLL without the functions pyrtlsdr 0.4+ needs '
                '(an older scanner build or the stock 2023 DLL)')
    if 'No module named' in out and 'rtlsdr' in out:
        return 'pyrtlsdr is not installed in this Python: python -m pip install pyrtlsdr'
    return 'pyrtlsdr could not load a DLL'


def copy(src, dst):
    try:
        shutil.copyfile(src, dst)
        return True
    except (IOError, OSError) as exc:
        print('could not write %s (%s) - close the scanner and every program that uses the dongle, '
              'then try again' % (dst, exc))
        return False


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'install'
    print('Python %s, %d-bit, %s' % (sys.version.split()[0], struct.calcsize('P') * 8, sys.prefix))
    path, info, out = probe()
    if path:
        print('pyrtlsdr loads: %s\nbuild: %s' % (path, info))
    else:
        print('%s:\n%s' % (why(out), out.strip().splitlines()[-1] if out.strip() else ''))
    if cmd == 'where':
        return 0 if path else 1
    target = path or default_target()
    if cmd == 'rollback':
        if not target or not os.path.exists(target + SUFFIX):
            print('no backup %s%s' % (target, SUFFIX))
            return 1
        if not copy(target + SUFFIX, target):
            return 1
        print('restored %s%s' % (target, SUFFIX))
        p2, i2, o2 = probe()
        print('now: build %s' % i2 if p2 else 'now: %s' % why(o2))
        return 0
    if struct.calcsize('P') != 8:
        print('32-bit Python: this kit has only the 64-bit DLL - not installed')
        return 1
    if path and TAG in (info or ''):
        print('%s is already installed (%s)' % (TAG, sha(path)))
        return 0
    if not target:
        print('no place to install to: pyrtlsdr loads no DLL and this is not Windows')
        return 1
    if not path:
        print('installing as %s' % target)
        d = os.path.dirname(target)
        if not os.path.isdir(d):
            os.makedirs(d)
    if os.path.exists(target) and not os.path.exists(target + SUFFIX):
        shutil.copyfile(target, target + SUFFIX)
        print('backup: %s%s' % (target, SUFFIX))
    if not copy(NEW, target):
        return 1
    path2, info2, out2 = probe()
    ok = TAG in (info2 or '')
    if path2:
        print('now: %s\nbuild: %s' % (path2, info2))
    else:
        print('%s:\n%s' % (why(out2), out2.strip()))
    if ok:
        print('OK - %s installed' % TAG)
    elif path2 and os.path.normcase(path2) != os.path.normcase(target):
        print('NOT OK - pyrtlsdr loads another DLL first (%s). Rename or remove it, or run '
              '"python install_dll.py" again after that.' % path2)
    else:
        print('NOT OK - run "python install_dll.py rollback"')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
