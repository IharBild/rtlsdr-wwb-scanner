"""V20.8: first wave of the code audit of 11.11.2026.

B1  Start without a dongle or with a busy dongle: a Python error instead of
    "dongle not found", the same error after every later scan, a thread left
    for ever and a crash when the program is closed.
B2  "Calibrate" without a dongle left the calibration flag set: the next
    ordinary Start ran a calibration scan.
B3  Start at or above End, or an empty field: "zero-size array" (numpy).
C1  sdb2 was written in the code page of Windows although its header says
    UTF-8: a folder with Cyrillic or Polish letters gave a broken file or none.
C2  WSM CSV: the same for the scan name; UTF-8 files were not read.
A3  A fresh installation started with settings nothing was tested with."""
import builtins
import io
import locale
import os
import re
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import sim
from wwb_scanner.file_handlers import BaseImporter
from wwb_scanner.file_handlers import exporters, importers

import wwb_scanner

# the QML of the package under test (the repository, or the installed copy
# when the tests run from the qa-kit folder)
QML = os.path.join(os.path.dirname(os.path.abspath(wwb_scanner.__file__)), 'ui', 'pyside', 'qml')


# ----------------------------------------------------------------- B3 (core)

def test_end_below_start_is_named(monkeypatch, tmpdir):
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 480, 470)
    assert isinstance(scanner.error, ValueError)
    assert 'End frequency 470 MHz must be above Start frequency 480 MHz' in str(scanner.error)


def test_range_that_is_not_a_number_is_named(monkeypatch, tmpdir):
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, float('nan'), 480)
    assert isinstance(scanner.error, ValueError) and 'two numbers in MHz' in str(scanner.error)


def test_start_equal_to_end_is_named(monkeypatch, tmpdir):
    # it used to "scan" one tile and keep no point of it, without a word
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 470)
    assert isinstance(scanner.error, ValueError) and 'must be above Start' in str(scanner.error)


# ------------------------------------------------------------------ C1 / C2

def _windows_code_page(monkeypatch, code_page):
    """open() without encoding= as on a Windows with this code page."""
    real = builtins.open
    def win_open(file, mode='r', buffering=-1, encoding=None, errors=None, newline=None,
                 closefd=True, opener=None):
        if 'b' not in mode and encoding is None:
            encoding = code_page
        return real(file, mode, buffering, encoding, errors, newline, closefd, opener)
    monkeypatch.setattr(builtins, 'open', win_open)
    monkeypatch.setattr(io, 'open', win_open)
    monkeypatch.setattr(locale, 'getpreferredencoding', lambda do_setlocale=True: code_page)
    return real


def _spectrum(name='scan'):
    from wwb_scanner.scan_objects import Spectrum
    sp = Spectrum()
    f = np.arange(470.0, 480.0001, 0.025)
    y = -100. + 3. * np.sin(np.arange(f.size) / 7.)
    y[200] = -60.
    sp.add_sample_set(frequency=f, dbFS=y)
    sp.name = name
    return sp


@pytest.mark.parametrize('code_page', ['cp1251', 'cp1250', 'cp1252'])
@pytest.mark.parametrize('folder', [u'scans', u'Сканы', u'Skany_Łódź',
                                    u'Bühne'])
def test_sdb2_is_utf8_whatever_the_folder_is_called(tmpdir, monkeypatch, code_page, folder):
    real_open = _windows_code_page(monkeypatch, code_page)
    d = os.path.join(str(tmpdir), folder)
    os.makedirs(d)
    path = os.path.join(d, 'a.sdb2')
    _spectrum().export_to_file(filename=path)
    with real_open(path, 'rb') as fh:
        raw = fh.read()
    text = raw.decode('utf-8')                      # the header says UTF-8
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(raw)                       # well-formed XML
    assert folder in root.attrib['name']
    back = BaseImporter.import_file(path)
    assert back.sample_data.size == 401


def test_sdb2_with_a_latin_path_is_byte_for_byte_as_before(tmpdir, monkeypatch):
    path = str(tmpdir.join('a.sdb2'))
    _spectrum().export_to_file(filename=path)
    with open(path, 'rb') as fh:
        raw = fh.read()
    assert raw == raw.decode('ascii').encode('ascii')       # plain ASCII: the same bytes in any code page


@pytest.mark.parametrize('code_page', ['cp1251', 'cp1250'])
@pytest.mark.parametrize('name', [u'Stodola', u'Клуб Стодола',
                                  u'Łódź Wytwórnia', u'Bühne 1'])
def test_wsm_export_with_any_scan_name(tmpdir, monkeypatch, code_page, name):
    real_open = _windows_code_page(monkeypatch, code_page)
    path = str(tmpdir.join('w.csv'))
    _spectrum(name).export_to_file(filename=path, frequency_format='wsm')
    with real_open(path, 'rb') as fh:
        raw = fh.read()
    first = raw.split(b'\r\n')[0].decode('utf-8')
    assert first == u'Receiver;RTL-SDR ' + name
    assert b'\r\r\n' not in raw
    assert BaseImporter.import_file(path).sample_data.size == 401


def test_wsm_file_with_a_latin_name_is_byte_for_byte_as_before(tmpdir):
    sp = _spectrum('Stodola')
    path = str(tmpdir.join('w.csv'))
    sp.export_to_file(filename=path, frequency_format='wsm')
    with open(path, 'rb') as fh:
        raw = fh.read()
    lines = raw.decode('ascii').split('\r\n')
    assert lines[0] == 'Receiver;RTL-SDR Stodola' and lines[2] == 'RFUnit;dB;MinimumRFValue;0'
    assert lines[6].startswith('Frequency;RF level (%);RF level')


def _wsm_bytes(tmpdir, receiver_bytes, bom=False):
    path = str(tmpdir.join('src.csv'))
    _spectrum('x').export_to_file(filename=path, frequency_format='wsm')
    with open(path, 'rb') as fh:
        raw = fh.read()
    raw = raw.replace(b'RTL-SDR x', receiver_bytes)
    out = str(tmpdir.join('in.csv'))
    with open(out, 'wb') as fh:
        fh.write((b'\xef\xbb\xbf' if bom else b'') + raw)
    return out


@pytest.mark.parametrize('code_page', ['cp1251', 'cp1250', 'utf-8'])
def test_wsm_import_reads_utf8_with_and_without_bom(tmpdir, monkeypatch, code_page):
    _windows_code_page(monkeypatch, code_page)
    name = u'EM 300 Сцена И'        # the last letter holds byte 0x98 in UTF-8
    for bom in (False, True):
        sp = BaseImporter.import_file(_wsm_bytes(tmpdir, name.encode('utf-8'), bom=bom))
        assert sp.sample_data.size == 401 and name in sp.name


def test_wsm_import_reads_a_file_in_another_code_page(tmpdir, monkeypatch):
    _windows_code_page(monkeypatch, 'cp1251')
    sp = BaseImporter.import_file(_wsm_bytes(tmpdir, u'Bühne'.encode('cp1252')))
    assert sp.sample_data.size == 401       # the name may show a wrong letter, the scan opens


def test_decode_text():
    assert importers.decode_text(b'\xef\xbb\xbfa\r\nb\rc\n') == 'a\nb\nc\n'
    assert importers.decode_text(u'И'.encode('utf-8')) == u'И'
    assert importers.decode_text(b'\xff\xfe\x98') is not None       # never raises


def test_file_handlers_never_open_text_without_an_encoding():
    """The trap of the cp1251 Windows (knowledge base, trap 1)."""
    bad = []
    for mod in (exporters, importers):
        with io.open(mod.__file__.replace('.pyc', '.py'), encoding='utf-8') as fh:
            for n, line in enumerate(fh, 1):
                m = re.search(r"\bopen\(([^)]*)\)", line)
                if not m or line.lstrip().startswith('#'):
                    continue
                args = m.group(1)
                if "'rb'" in args or "'wb'" in args or 'encoding=' in args:
                    continue
                bad.append('%s:%d %s' % (os.path.basename(mod.__file__), n, line.strip()))
    assert not bad, bad


# ----------------------------------------------------------------------- A3

def test_factory_settings_are_the_tested_configuration():
    pytest.importorskip('PySide2')
    out = _isolated("""
        from wwb_scanner.ui.pyside.scanner import FACTORY, FACTORY_OLD, ScanConfigData
        from wwb_scanner.scanner.main import get_freq_resolution
        assert FACTORY == dict(sampleRate=2400., gain=33.8, windowSize=96, samplesPerSweep=2048,
                               sweepsPerScan=40, sweepOverlapRatio=0.65), FACTORY
        assert FACTORY_OLD == dict(sampleRate=2048., gain=0., windowSize=128, samplesPerSweep=8192,
                                   sweepsPerScan=20, sweepOverlapRatio=0.5), FACTORY_OLD
        c = ScanConfigData()
        assert (c.windowSize, c.samplesPerSweep, c.sweepsPerScan, c.sweepOverlapRatio, c.windowType) == \\
            (96, 2048, 40, 0.65, 'hann')
        # one point per 25 kHz (the grid of WWB) and a tile step of 0.775 MHz
        assert abs(get_freq_resolution(96, 2.4e6) - 25e3) < 1e-6
        print('ALL_OK')
    """)
    assert 'ALL_OK' in out


def test_factory_settings_in_qml_are_the_same():
    def qml(name):
        with io.open(os.path.join(QML, name), encoding='utf-8') as fh:
            return fh.read()
    sc = qml('ScanConfig.qml')
    head = sc[sc.index('ScanConfigData {'):sc.index('onConfigUpdate')]
    got = dict(re.findall(r'^\s*(samplesPerSweep|sweepsPerScan|sweepOverlapRatio|windowSize):\s*([0-9.]+)', head, re.M))
    assert got == {'samplesPerSweep': '2048', 'sweepsPerScan': '40', 'sweepOverlapRatio': '0.65', 'windowSize': '96'}
    dc = qml('DeviceConfigPopup.qml')
    assert re.search(r'property real sampleRate:\s*2400\b', dc)
    assert re.search(r'property real gain:\s*33\.8\b', dc)


def test_tile_step_of_the_factory_settings(monkeypatch, tmpdir):
    scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470, 480)        # sim.make_config = the tested set
    assert abs(scanner.step_size - 0.775) < 1e-9
    f = np.asarray(scanner.spectrum.sample_data['frequency'])
    assert abs(float(np.median(np.diff(f))) - 0.025) < 1e-9


# ------------------------------------------------------- B1 / B2 / B3 (window)

def _isolated(code, timeout=180):
    """A fresh Python with an offscreen Qt (see test_v181._isolated)."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p),
               QT_QPA_PLATFORM='offscreen')
    prelude = textwrap.dedent('''
        import sys, types, os, tempfile
        os.environ['HOME'] = os.environ['USERPROFILE'] = tempfile.mkdtemp(prefix='v208_')
        try:
            import PySide2
        except ImportError:
            print('NO_PYSIDE2'); raise SystemExit(0)
        try:
            import rtlsdr
        except Exception:
            m = types.ModuleType('rtlsdr')
            class RtlSdr(object):
                def __init__(self, *a, **k):
                    raise IOError('No devices found')
                @staticmethod
                def get_device_serial_addresses():
                    return []
            m.RtlSdr = RtlSdr
            m.RtlSdrTcpClient = None
            sys.modules['rtlsdr'] = m
    ''')
    r = subprocess.run([sys.executable, '-c', prelude + textwrap.dedent(code)], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = r.stdout.decode('utf-8', 'replace')
    if 'NO_PYSIDE2' in out:
        pytest.skip('PySide2 not installed')
    assert r.returncode == 0 and 'ALL_OK' in out, 'exit code %s\n%s' % (r.returncode, out[-3000:])
    return out


WINDOW = """
    import logging, time, threading
    import numpy as np
    from PySide2 import QtCore
    from PySide2.QtWidgets import QApplication
    import sim
    from wwb_scanner.scanner import sdrwrapper, sample_processing
    from wwb_scanner import scan_dll
    from wwb_scanner.utils import dbstore
    # the simulator is not a device of the real DLL: on a PC with the scanner
    # DLL its extra calls got the simulator as a device (access violation)
    scan_dll._cache['lib'] = None
    from wwb_scanner.ui.pyside import scanner as S, graph as G
    home = os.environ['HOME']
    dbstore.DBStore.DB_PATH = os.path.join(home, 'db.json')
    dbstore.DBStore.SCAN_DB_PATH = os.path.join(home, 'scan_db.json')
    app = QApplication([])
    said = []
    class H(logging.Handler):
        def emit(self, r):
            if r.levelno >= logging.ERROR:
                said.append(r.getMessage())
    logging.getLogger('wwb_scanner').addHandler(H())
    def pump(s):
        end = time.time() + s
        while time.time() < end:
            app.processEvents(QtCore.QEventLoop.AllEvents, 20); time.sleep(0.002)
    def wait_done(si, s=60):
        end = time.time() + s
        while time.time() < end and (si.running or si.scanner is not None):
            pump(0.02)
        pump(0.3)
        assert not si.running and si.scanner is None, 'scan did not end'
    class Dev(QtCore.QObject):
        device_serial = '00000001'
        gains = sim.R828D_GAINS_DB if hasattr(sim, 'R828D_GAINS_DB') else []
    def make(device=True):
        si = S.ScannerInterface()
        si.scanConfig = S.ScanConfigData()
        si.scanConfig.startFreq, si.scanConfig.endFreq = 470., 474.
        si.gain = 33.8
        si.sampleRate = 2400.
        if device:
            si.deviceInfo = Dev()
        return si
"""


def test_start_without_a_dongle_says_so_and_leaves_nothing_behind():
    _isolated(WINDOW + """
    si = make(device=False)
    si.start(); pump(0.2)
    assert not si.running and si.scanner is None and si.scan_thread is None
    assert said and 'No RTL-SDR dongle found' in said[-1], said
    # B2: the calibration flags do not survive a start that did not happen
    si.calibrate(); pump(0.1)
    assert si.calibrating is False and not si.running
    si.calibrateGains(); pump(0.1)
    assert si._gain_calibrating is False and not si.running
    assert all('NoneType' not in s for s in said), said
    # ... so the next ordinary Start is an ordinary scan
    made = []
    def factory(serial_number=None):
        made.append(sim.FakeRtlSdr(serial_number=serial_number, air=sim.Air(noise=0.02)))
        return made[-1]
    sdrwrapper.RtlSdr = factory
    si.deviceInfo = Dev()
    confs = []
    build = si.build_scan_config
    def recorded():
        confs.append(build())
        return confs[-1]
    si.build_scan_config = recorded     # a fast PC ends this scan before the next line runs
    si.start()
    wait_done(si)
    conf = confs[-1]
    assert list(conf.scan_range) == [470., 474.] and not conf.get('gain_calibration'), conf.scan_range
    assert si.spectrum.name.startswith('470-474 MHz')
    assert si.spectrum.sample_data.size == 161
    print('ALL_OK')
    """)


def test_start_refuses_a_wrong_range_with_a_clear_text():
    _isolated(WINDOW + """
    si = make()
    for lo, hi, text in ((480., 470., 'End Freq (470 MHz) must be above Start Freq (480 MHz)'),
                         (470., 470., 'must be above Start Freq'),
                         (float('nan'), 480., 'must be numbers'),
                         (470., float('inf'), 'must be numbers')):
        del said[:]
        si.scanConfig.startFreq, si.scanConfig.endFreq = lo, hi
        si.start(); pump(0.1)
        assert not si.running and si.scanner is None, (lo, hi)
        assert said and text in said[-1], (lo, hi, said)
    # the checks do not stand in the way of the calibrations (own ranges)
    si.calibrating = True
    assert si.start_problem() == ''
    si.calibrating = False
    si.sampleRate = float('nan')
    assert 'sample rate' in si.start_problem()
    print('ALL_OK')
    """)


def test_busy_dongle_keeps_its_message_leaves_no_thread_and_no_stale_graph():
    _isolated(WINDOW + """
    sample_processing.REOPEN_WAIT_S = 0.5
    def busy(serial_number=None):
        raise IOError('LIBUSB_ERROR_ACCESS')
    sdrwrapper.RtlSdr = busy
    si = make()
    graphs = []
    for n in range(3):
        del said[:]
        si.start()
        assert si.running
        g = G.LiveSpectrumGraphData()           # what main.qml creates for the scan
        g.scanner = si
        graphs.append(g)
        t = si.scan_init_thread
        wait_done(si)
        # the message of the scanner is the last one: no 'zero-size array' after it
        assert said and 'dongle not found or busy' in said[-1], said
        assert not any('zero-size' in s for s in said), said
        # B1: no thread outlives the scan, the graph has let the scanner go
        assert si.scan_init_thread is None and (t is None or not t._thread.isRunning())
        assert g.scanner is None and g.xy_data.size == 0
    # a good scan after the failed ones: its own message, nothing from the old graphs
    sdrwrapper.RtlSdr = lambda serial_number=None: sim.FakeRtlSdr(serial_number=serial_number,
                                                                  air=sim.Air(noise=0.02))
    del said[:]
    si.start()
    g = G.LiveSpectrumGraphData(); g.scanner = si
    wait_done(si)
    assert not said, said
    assert g.scanner is None and g.xy_data.size == 161
    assert abs(g.minValue.x() - 470.) < 1e-6 and abs(g.maxValue.x() - 474.) < 1e-6
    print('ALL_OK')
    """)
    # the subprocess ended with exit code 0: before V20.8 the thread left behind
    # ended the program with "QThread: Destroyed while thread is still running"


def test_graph_extents_come_from_the_frequency_axis():
    _isolated(WINDOW + """
    from wwb_scanner.scan_objects import Spectrum
    g = G.LiveSpectrumGraphData()
    g._update_extents()                 # no points: no error
    sp = Spectrum()
    sp.add_sample_set(frequency=np.arange(600., 610.0001, 0.025), dbFS=np.full(401, -95.))
    g.spectrum = sp
    sp.set_data_updated()
    g.update_spectrum_data()
    assert abs(g.minValue.x() - 600.) < 1e-6 and abs(g.maxValue.x() - 610.) < 1e-6, (g.minValue, g.maxValue)
    print('ALL_OK')
    """)
