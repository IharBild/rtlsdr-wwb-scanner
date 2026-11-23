"""DLL scanner-v8 (23.11.2026): the v7 driver on libusb 1.0.30 (CVE-2026-47104,
CVE-2026-23679; external audit 22.11, B6 / AUD-10). The delivered folder is
complete and consistent, it is the v7 driver with the same functions, and the
installer goes from v7 to v8 and back."""
import hashlib
import importlib.util
import io
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVERS = os.path.join(os.path.dirname(HERE), 'drivers')
V7 = os.path.join(DRIVERS, 'rtlsdr-scanner-v7')
V8 = os.path.join(DRIVERS, 'rtlsdr-scanner-v8')
DLL = os.path.join(V8, 'x64', 'rtlsdr.dll')

pytestmark = pytest.mark.skipif(not os.path.isdir(V8), reason='no drivers/rtlsdr-scanner-v8 in this copy')


def _sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def _names(path):
    data = open(path, 'rb').read()
    return set(re.findall(rb'(?<=\x00)(rtlsdr_[a-z0-9_]+)(?=\x00)', data))


def test_sha256sums_match():
    n = 0
    with io.open(os.path.join(V8, 'SHA256SUMS.txt'), encoding='utf-8') as f:
        for line in f:
            h, name = line.split(None, 1)
            assert _sha(os.path.join(V8, name.strip())) == h, name
            n += 1
    assert n >= 20


def test_dll_is_v8_on_libusb_1_0_30():
    data = open(DLL, 'rb').read()
    assert b'(scanner-v8)' in data and b'libusb 1.0.30' in data
    assert b'5049ecd' in data                      # the v7 driver revision
    assert b'libusb 1.0.29' not in data


def test_same_functions_as_v7():
    v7, v8 = _names(os.path.join(V7, 'x64', 'rtlsdr.dll')), _names(DLL)
    assert len(v7) >= 51 and v7 == v8


def test_tools_are_the_v7_tools():
    for t in ('rtl_test', 'rtl_sdr', 'rtl_power', 'rtl_biast', 'rtl_eeprom'):
        assert _sha(os.path.join(V8, 'x64', t + '.exe')) == _sha(os.path.join(V7, 'x64', t + '.exe')), t


def test_same_driver_sources_as_v7():
    for sub in ('patches', 'model'):
        a = sorted(os.listdir(os.path.join(V7, 'source', sub)))
        assert a == sorted(os.listdir(os.path.join(V8, 'source', sub)))
        for n in a:
            assert _sha(os.path.join(V7, 'source', sub, n)) == _sha(os.path.join(V8, 'source', sub, n)), n


def test_build_info_and_qa_pc_know_this_dll():
    h = _sha(DLL)
    info = io.open(os.path.join(V8, 'source', 'BUILD_INFO.txt'), encoding='utf-8').read()
    assert '%s  rtlsdr.dll' % h in info
    assert 'libusb v1.0.30 87a55632db62c9bdc58cd31d3ccfa673f1bb017f' in info
    qa = io.open(os.path.join(os.path.dirname(HERE), 'tools', 'qa_pc.py'), encoding='utf-8').read()
    assert "'%s': 'scanner-v8" % h in qa
    build = io.open(os.path.join(V8, 'source', 'build_all.sh'), encoding='utf-8').read()
    assert 'LIBUSB_TAG=v1.0.30' in build and 'LIBUSB_COMMIT=87a55632db62c9bdc58cd31d3ccfa673f1bb017f' in build


FAKE_RTLSDR = '''
import os
class _F(object):
    def __call__(self):
        data = open(self.path, 'rb').read()
        for tag in (b'scanner-v8', b'scanner-v7', b'scanner-v6'):
            if tag in data:
                return b'... (' + tag + b') ...'
        return b'... scanner-v4 ...'
class _Lib(object):
    pass
librtlsdr = _Lib()
librtlsdr._name = os.environ['FAKE_DLL']
f = _F(); f.path = os.environ['FAKE_DLL']
librtlsdr.rtlsdr_get_build_info = f
'''


@pytest.mark.skipif(sys.platform == 'win32', reason='uses a fake pyrtlsdr (Linux path lookup)')
def test_install_over_v7_and_rollback(tmpdir):
    venv = tmpdir.join('venv')
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True, timeout=120)
    py = str(venv.join('bin', 'python'))
    pkg = tmpdir.mkdir('fake').mkdir('rtlsdr')
    pkg.join('__init__.py').write('from .librtlsdr import librtlsdr\n')
    pkg.join('librtlsdr.py').write(FAKE_RTLSDR)
    dll = venv.mkdir('Scripts2').join('rtlsdr.dll')
    v7 = open(os.path.join(V7, 'x64', 'rtlsdr.dll'), 'rb').read()
    dll.write_binary(v7)
    env = dict(os.environ, PYTHONPATH=str(tmpdir.join('fake')), FAKE_DLL=str(dll))

    def run(*a):
        r = subprocess.run([py, os.path.join(V8, 'install_dll.py')] + list(a), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=env, timeout=60)
        return r.returncode, r.stdout.decode('utf-8', 'replace')
    rc, out = run()
    assert rc == 0 and 'OK - scanner-v8 installed' in out, out
    assert dll.read_binary() == open(DLL, 'rb').read()
    assert dll.dirpath().join('rtlsdr.dll.before-v8').read_binary() == v7
    rc, out = run()
    assert rc == 0 and 'already installed' in out, out
    rc, out = run('rollback')
    assert rc == 0 and dll.read_binary() == v7, out


def test_installer_tags():
    spec = importlib.util.spec_from_file_location('install_dll_v8', os.path.join(V8, 'install_dll.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.TAG == 'scanner-v8' and m.SUFFIX == '.before-v8'
    assert m.NEW == DLL
