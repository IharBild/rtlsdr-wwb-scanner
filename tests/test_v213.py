"""V21.3: new Python (3.10-3.14), modern pip, pyrtlsdr 0.4+ with DLL scanner-v7.

- wwb_scanner.dllpath: on Windows with Python 3.8+ the folder with rtlsdr.dll
  (Scripts of a virtual environment) is added to the DLL search, otherwise
  pyrtlsdr 0.4+ does not find it (checked in Wine with Windows Python 3.13).
- Packaging: pyproject.toml (PEP 517), dependencies by Python version.
- End to end (where the driver sources and gcc are there): the real pyrtlsdr
  0.4+ loads our driver built for Linux against the USB model, and the real
  Scanner.run_scan() scans with it.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from wwb_scanner import dllpath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ----------------------------------------------------------------- dllpath

class _FakeOs(object):
    def __init__(self):
        self.added = []

    def add_dll_directory(self, d):
        self.added.append(d)
        return object()


def _win(monkeypatch, fake=None):
    monkeypatch.setattr(dllpath.sys, 'platform', 'win32')
    monkeypatch.setattr(dllpath, '_added', [])
    fake = fake or _FakeOs()
    monkeypatch.setattr(dllpath.os, 'add_dll_directory', fake.add_dll_directory, raising=False)
    return fake


def test_dll_in_scripts_of_the_environment_is_added(monkeypatch, tmpdir):
    scripts = tmpdir.mkdir('Scripts')
    scripts.join('rtlsdr.dll').write_binary(b'x')
    monkeypatch.setattr(dllpath.sys, 'prefix', str(tmpdir))
    fake = _win(monkeypatch)
    assert dllpath.prepare() == str(scripts)
    assert fake.added == [str(scripts)]
    assert dllpath.prepare() == str(scripts) and fake.added == [str(scripts)]   # once


def test_librtlsdr_name_and_prefix_folder(monkeypatch, tmpdir):
    tmpdir.join('librtlsdr.dll').write_binary(b'x')
    monkeypatch.setattr(dllpath.sys, 'prefix', str(tmpdir))
    fake = _win(monkeypatch)
    assert dllpath.prepare() == str(tmpdir) and fake.added == [str(tmpdir)]


def test_nothing_without_a_dll(monkeypatch, tmpdir):
    tmpdir.mkdir('Scripts')
    monkeypatch.setattr(dllpath.sys, 'prefix', str(tmpdir))
    fake = _win(monkeypatch)
    assert dllpath.prepare() is None and fake.added == []


def test_nothing_on_python37_or_other_systems(monkeypatch, tmpdir):
    tmpdir.mkdir('Scripts').join('rtlsdr.dll').write_binary(b'x')
    monkeypatch.setattr(dllpath.sys, 'prefix', str(tmpdir))
    monkeypatch.setattr(dllpath.sys, 'platform', 'win32')
    monkeypatch.delattr(dllpath.os, 'add_dll_directory', raising=False)     # Python 3.7
    assert dllpath.prepare() is None
    monkeypatch.setattr(dllpath.sys, 'platform', 'linux')
    assert dllpath.prepare() is None


def test_package_import_never_fails_on_it(monkeypatch):
    def boom(d):
        raise OSError('no such folder')
    monkeypatch.setattr(dllpath, 'find_dll_dir', lambda prefix=None: 'C:\\x')
    monkeypatch.setattr(dllpath.sys, 'platform', 'win32')
    monkeypatch.setattr(dllpath, '_added', [])
    monkeypatch.setattr(dllpath.os, 'add_dll_directory', boom, raising=False)
    assert dllpath.prepare() is None


# --------------------------------------------------------------- packaging

def _read(p):
    path = os.path.join(ROOT, p)
    if not os.path.isfile(path):                  # qa-kit: no repository files
        pytest.skip('repository layout only (%s)' % p)
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def test_pyproject_builds_with_setuptools():
    t = _read('pyproject.toml')
    assert re.search(r'build-backend\s*=\s*"setuptools\.build_meta"', t)
    assert 'setuptools' in t and 'wheel' in t


def test_dependencies_by_python_version():
    cfg = _read('setup.cfg')
    req = cfg.split('install_requires', 1)[1].split('\n[', 1)[0]
    # Python 3.7-3.9 keep the tested stack; 3.10+ get the new one
    assert re.search(r'pyrtlsdr[^;\n]*<\s*0\.4\s*;\s*python_version\s*<\s*"3\.10"', req), req
    assert re.search(r'pyrtlsdr\s*>=\s*0\.4\s*;\s*python_version\s*>=\s*"3\.10"', req), req
    assert re.search(r'PySide2.*;\s*python_version\s*<\s*"3\.11"', req), req
    # PySide2 5.15 is built against numpy 1.x ("may crash" with numpy 2)
    assert re.search(r'numpy[^;\n]*<\s*2\s*;\s*python_version\s*<\s*"3\.11"', req), req
    assert 'tinydb' in req and 'numpy' in req and 'scipy' in req
    # pyrtlsdr 0.3 imports pkg_resources without declaring setuptools;
    # setuptools 82 removed pkg_resources (checked 13.11.2026)
    assert re.search(r'^\s*setuptools\s*<\s*82\s*;\s*python_version\s*<\s*"3\.10"', req, re.M), req
    assert re.search(r'python_requires\s*=\s*>=\s*3\.7', cfg)


def test_version_and_marker():
    assert re.search(r'^version = 21\.3\.0$', _read('setup.cfg'), re.M)


# --------------------------------------------------------------- end to end

def _e2e_sources():
    d, l = os.environ.get('DRV_V7_SRC'), os.environ.get('LIBUSB_SRC')
    if not d or not l or not shutil.which('gcc') or sys.platform != 'linux':
        return None
    try:
        import importlib.metadata as md
        v = tuple(int(x) for x in md.version('pyrtlsdr').split('.')[:2])
    except Exception:
        return None
    return (d, l) if v >= (0, 4) else None


@pytest.mark.skipif(_e2e_sources() is None,
                    reason='set DRV_V7_SRC and LIBUSB_SRC; needs Linux, gcc and pyrtlsdr >= 0.4')
def test_real_pyrtlsdr_with_our_driver_and_the_usb_model(tmpdir):
    d, l = _e2e_sources()
    model = os.path.join(ROOT, 'drivers', 'rtlsdr-scanner-v7', 'source', 'model')
    lib = str(tmpdir.join('librtlsdr.so'))
    subprocess.check_call(['gcc', '-O1', '-shared', '-fPIC', '-o', lib,
                           '-I' + os.path.join(d, 'include'), '-I' + os.path.join(d, 'src'),
                           '-I' + os.path.join(l, 'libusb'),
                           os.path.join(d, 'src', 'librtlsdr.c')]
                          + [os.path.join(d, 'src', f) for f in sorted(os.listdir(os.path.join(d, 'src')))
                             if f.startswith('tuner_') and f.endswith('.c')]
                          + [os.path.join(model, 'mock_libusb.c'), '-lm'])
    script = r'''
import json, os, sys
sys.path.insert(0, os.environ['TESTS'])
from rtlsdr.librtlsdr import librtlsdr
import importlib.metadata as md
import sim
from wwb_scanner.scanner import main
from wwb_scanner.utils import dbstore
dbstore.DBStore.DB_PATH = os.path.join(os.environ['HOME'], 'db.json')
dbstore.DBStore.SCAN_DB_PATH = os.path.join(os.environ['HOME'], 'scan_db.json')
sc = main.Scanner(config=sim.make_config(470, 520, gain=33.8))
sc.run_scan()
f, y = sim.spectrum_arrays(sc)
c = sc.sample_collection
print('RESULT ' + json.dumps({'pyrtlsdr': md.version('pyrtlsdr'), 'lib': librtlsdr._name,
    'points': int(f.size), 'f0': float(f.min()), 'f1': float(f.max()), 'finite': bool((y == y).all()),
    'missing': len(getattr(c, 'missing_tiles', []) or []), 'aborted': bool(c.aborted)}))
'''
    home = tmpdir.mkdir('home')
    env = dict(os.environ, LD_LIBRARY_PATH=str(tmpdir), HOME=str(home), TESTS=HERE)
    r = subprocess.run([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env=env, timeout=600, cwd=str(tmpdir))
    out = r.stdout.decode('utf-8', 'replace')
    assert r.returncode == 0, out
    res = json.loads(out.split('RESULT ', 1)[1].splitlines()[0])
    assert res['lib'].endswith('librtlsdr.so') and res['lib'].startswith(str(tmpdir))
    assert res['points'] == 2001 and res['f0'] == 470.0 and res['f1'] == 520.0
    assert res['finite'] and res['missing'] == 0 and not res['aborted']
    assert 'rtlsdr library: rtl-sdr-blog + scanner-v7' in out


# ------------------------------------------------------------ window entry

def test_window_entry_explains_missing_pyside2(monkeypatch, capsys):
    from wwb_scanner.ui import launch
    monkeypatch.setattr(launch, 'pyside_missing', lambda: True)
    assert launch.run() == 1
    err = capsys.readouterr().err
    assert 'PySide2' in err and '3.7-3.10' in err and 'PySide6' in err


def test_console_script_uses_the_launcher():
    assert "wwb_scanner-ui = wwb_scanner.ui.launch:run" in _read('setup.py')


# ------------------------------------------------- simulated gain survey

def test_simulated_survey_never_calls_a_real_library(monkeypatch):
    """With pyrtlsdr 0.4+ a real librtlsdr is often loadable; the simulated
    dongle's fake device pointer must not reach it (crash on Linux 13.11)."""
    import importlib.util
    from wwb_scanner import scan_dll
    path = os.path.join(ROOT, 'tools', 'gain_survey.py')
    if not os.path.isfile(path):
        path = os.path.join(ROOT, 'gain_survey.py')                  # qa-kit layout
    spec = importlib.util.spec_from_file_location('gain_survey_v213', path)
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    monkeypatch.setitem(scan_dll._cache, 'lib', object())          # "a real library"
    d = gs.Dongle(simulate=True, antenna=False)
    sdr = d.open()
    assert scan_dll._cache['lib'] is None
    assert scan_dll.i2c_stats(sdr) is None and scan_dll.tuner_locked(sdr) is None


def test_qa_tools_prepare_the_dll_search_before_importing_rtlsdr(monkeypatch, tmpdir):
    """qa_pc (PC4) asks qa_hw which DLL pyrtlsdr loads, without importing the
    scanner first: on Windows Python 3.8+ the DLL in the environment's Scripts
    folder would not be found."""
    import importlib.util
    path = os.path.join(ROOT, 'tools', 'qa_hw.py')
    if not os.path.isfile(path):
        path = os.path.join(ROOT, 'qa_hw.py')                         # qa-kit layout
    tmpdir.mkdir('Scripts').join('rtlsdr.dll').write_binary(b'x')
    monkeypatch.setattr(dllpath.sys, 'prefix', str(tmpdir))
    fake = _win(monkeypatch)
    spec = importlib.util.spec_from_file_location('qa_hw_v213', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert fake.added == [str(tmpdir.join('Scripts'))]
