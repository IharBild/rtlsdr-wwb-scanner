"""DLL scanner-v7 (13.11.2026: dithering + GPIO API for pyrtlsdr 0.4+): the delivered folder is complete and
consistent, the installer works, and - where gcc is available - the USB model
of the driver passes its failure-path scenarios."""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.join(os.path.dirname(HERE), 'drivers', 'rtlsdr-scanner-v7')
DLL = os.path.join(V7, 'x64', 'rtlsdr.dll')


def _sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_sha256sums_match():
    n = 0
    with io.open(os.path.join(V7, 'SHA256SUMS.txt'), encoding='utf-8') as f:
        for line in f:
            h, name = line.split(None, 1)
            assert _sha(os.path.join(V7, name.strip())) == h, name
            n += 1
    assert n >= 20


def test_dll_is_v7_with_the_new_functions():
    data = open(DLL, 'rb').read()
    assert b'(scanner-v7)' in data
    for name in (b'rtlsdr_set_dithering', b'rtlsdr_set_gpio_output', b'rtlsdr_set_gpio_input',
                 b'rtlsdr_set_gpio_bit', b'rtlsdr_get_gpio_bit', b'rtlsdr_set_gpio_byte',
                 b'rtlsdr_get_gpio_byte', b'rtlsdr_set_gpio_status',
                 b'rtlsdr_get_tuner_restores', b'rtlsdr_set_sync_read_timeout',
                 b'rtlsdr_get_i2c_stats', b'rtlsdr_get_tuner_pll_lock', b'rtlsdr_get_build_info'):
        assert name in data, name
    assert b'r82xx: tuner registers restored' in data


def test_build_info_and_qa_pc_know_this_dll():
    h = _sha(DLL)
    info = io.open(os.path.join(V7, 'source', 'BUILD_INFO.txt'), encoding='utf-8').read()
    assert '%s  rtlsdr.dll' % h in info
    qa = io.open(os.path.join(os.path.dirname(HERE), 'tools', 'qa_pc.py'), encoding='utf-8').read()
    assert "'%s': 'scanner-v7" % h in qa


def test_patches_0001_to_0009():
    names = sorted(os.listdir(os.path.join(V7, 'source', 'patches')))
    assert [n[:4] for n in names] == ['%04d' % i for i in range(1, 10)]


FAKE_RTLSDR = '''
import os
class _F(object):
    def __call__(self):
        data = open(self.path, 'rb').read()
        for tag in (b'scanner-v7', b'scanner-v6', b'scanner-v5'):
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
def test_install_over_v6_and_rollback(tmpdir):
    pkg = tmpdir.mkdir('fake').mkdir('rtlsdr')
    pkg.join('__init__.py').write('from .librtlsdr import librtlsdr\n')
    pkg.join('librtlsdr.py').write(FAKE_RTLSDR)
    dll = tmpdir.join('librtlsdr.dll')
    dll.write_binary(b'old dll scanner-v6')
    env = dict(os.environ, PYTHONPATH=str(tmpdir.join('fake')), FAKE_DLL=str(dll))
    script = os.path.join(V7, 'install_dll.py')

    def run(*a):
        r = subprocess.run([sys.executable, script] + list(a), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=env, timeout=60)
        return r.returncode, r.stdout.decode('utf-8', 'replace')
    rc, out = run()
    assert rc == 0 and 'OK - scanner-v7 installed' in out, out
    assert dll.read_binary() == open(DLL, 'rb').read()
    assert tmpdir.join('librtlsdr.dll.before-v7').read_binary() == b'old dll scanner-v6'
    rc, out = run()
    assert rc == 0 and 'already installed' in out, out
    rc, out = run('rollback')
    assert rc == 0 and dll.read_binary() == b'old dll scanner-v6', out


def _driver_sources():
    """Driver sources after git am 0001-0009 (DRV_V7_SRC) and the libusb
    source tree (LIBUSB_SRC), or None: the model needs both."""
    d, l = os.environ.get('DRV_V7_SRC'), os.environ.get('LIBUSB_SRC')
    if not d or not l or not shutil.which('gcc'):
        return None
    return d, l


@pytest.mark.skipif(_driver_sources() is None, reason='set DRV_V7_SRC and LIBUSB_SRC, needs gcc')
def test_usb_model_failure_paths():
    d, l = _driver_sources()
    r = subprocess.run(['sh', os.path.join(V7, 'source', 'model', 'run_model.sh'), d, l],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    out = r.stdout.decode('utf-8', 'replace')
    assert r.returncode == 0, out
    assert 'Sanitizer' not in out and 'runtime error' not in out, out
    # normal retunes unchanged
    assert re.search(r'470-800\s+tiles  426 \| USB ctrl per retune avg 9\.0', out), out
    assert 'max LO error 205.1 Hz' in out
    for line in ('registers chip != copy 0 (first 0x00)',
                 'T6 restores         : 1',
                 'shadow_dirty 0 (want <0, 0, 0, 0)',
                 'T11 read retry+reset: set_center_freq 0',
                 'tuner resets 2, registers chip != copy 0',
                 'T13 no lock after   : set_tuner_gain 0, PLL lock 0',
                 'T9 link lost (IO)   : set_center_freq -1, tuner I2C writes tried 1',
                 'T10 no samples      : read_sync -7, timeout passed 2068 ms',
                 'T10 timeout 0       : read_sync -99, timeout 0 ms',
                 'T8 close unplugged  : tuner I2C writes tried 0'):
        assert line in out, line
    assert 'T7 after   tiles  220 | USB ctrl per retune avg 9.0' in out
    assert 'T14 dithering       : set(1) 0 with 0 USB transfers, reg 0x12 bit4 on/off/on 0/16/0' in out
    assert 'T15 GPIO            : rc 0 0 0 0 0 0; out pin 3: GPO 08 GPOE 08 GPD 00 read 1; in pin 2: 1, status(GPD) 04, byte 0c; bad pin -1, NULL -1' in out
