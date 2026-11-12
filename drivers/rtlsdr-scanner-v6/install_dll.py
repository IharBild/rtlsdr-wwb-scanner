# -*- coding: utf-8 -*-
"""Установка rtlsdr.dll scanner-v6 вместо той, что сейчас загружает pyrtlsdr.

    python install_dll.py            установить (сканер должен быть закрыт)
    python install_dll.py where      только показать, какая DLL загружается
    python install_dll.py rollback   вернуть предыдущую DLL из резервной копии

Резервная копия кладётся рядом: <имя>.before-v6 (не перезаписывается).
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
TAG = 'scanner-v6'

PROBE = r'''
import ctypes, sys
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
    load it: Windows does not let a loaded DLL be replaced)."""
    r = subprocess.run([sys.executable, '-c', PROBE], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode('utf-8', 'replace')
    path = info = None
    for line in out.splitlines():
        if line.startswith('PATH='):
            path = line[5:].strip()
        elif line.startswith('INFO='):
            info = line[5:].strip()
    if path is None:
        print('pyrtlsdr could not load a DLL:\n' + out)
    return path, info


def sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'install'
    print('Python %s, %d-bit' % (sys.version.split()[0], struct.calcsize('P') * 8))
    path, info = probe()
    if not path:
        return 1
    print('pyrtlsdr loads: %s\nbuild: %s' % (path, info))
    backup = path + '.before-v6'
    if cmd == 'where':
        return 0
    if cmd == 'rollback':
        if not os.path.exists(backup):
            print('no backup %s' % backup)
            return 1
        shutil.copyfile(backup, path)
        print('restored %s' % backup)
        print('now: build %s' % probe()[1])
        return 0
    if struct.calcsize('P') != 8:
        print('32-bit Python: this kit has only the 64-bit DLL - not installed')
        return 1
    if TAG in (info or ''):
        print('scanner-v6 is already installed (%s)' % sha(path))
        return 0
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
        print('backup: %s' % backup)
    try:
        shutil.copyfile(NEW, path)
    except (IOError, OSError) as exc:
        print('could not replace the DLL (%s) - close the scanner and every program that uses the dongle, '
              'then try again' % exc)
        return 1
    path2, info2 = probe()
    ok = TAG in (info2 or '')
    print('now: %s\nbuild: %s' % (path2, info2))
    print('OK - scanner-v6 installed' if ok else 'NOT OK - run "python install_dll.py rollback"')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
