"""V21.4: the window on PySide6 (Qt 6). V22.0: PySide6 only (PySide2 removed)."""
import glob
import io
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, 'wwb_scanner')
UI = os.path.join(PKG, 'ui', 'pyside')
QML = os.path.join(UI, 'qml')


def _read(*p):
    path = os.path.join(*p)
    if not os.path.isfile(path):
        pytest.skip('repository layout only (%s)' % path)
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def _installed_ui():
    """The UI folder of the scanner that is imported (repo or installed)."""
    import wwb_scanner
    return os.path.join(os.path.dirname(wwb_scanner.__file__), 'ui', 'pyside')


# ---------------------------------------------------------------- Python side

def test_only_qt_py_imports_a_binding():
    ui = _installed_ui()
    bad = []
    for p in glob.glob(os.path.join(ui, '*.py')):
        if os.path.basename(p) == 'qt.py':
            continue
        for i, line in enumerate(io.open(p, encoding='utf-8'), 1):
            if re.match(r'\s*(from|import)\s+PySide[26]\b', line):
                bad.append('%s:%d %s' % (os.path.basename(p), i, line.strip()))
    assert not bad, bad


def test_no_signal_decorator():
    """@Signal as a decorator: PySide6 does not accept it."""
    for p in glob.glob(os.path.join(_installed_ui(), '*.py')):
        assert not re.search(r'^\s*@Signal\b', io.open(p, encoding='utf-8').read(), re.M), p


def _run(code, **env):
    e = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p), QT_QPA_PLATFORM='offscreen',
             QT_QUICK_BACKEND='software')      # Qt 5 offscreen: no OpenGL context -> abort
    e.update(env)
    r = subprocess.run([sys.executable, '-c', code], env=e, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=120)
    return r.returncode, r.stdout.decode('utf-8', 'replace')


def test_shim_is_pyside6():
    rc, out = _run("from wwb_scanner.ui.pyside import qt; print('API', qt.API, qt.QT6, qt.version_text())")
    if rc != 0:
        pytest.skip('no Qt binding here')
    assert 'API PySide6 True PySide6 ' in out, out


def _qt_or_skip():
    try:
        from wwb_scanner.ui.pyside import qt
    except ImportError:           # (pytest 8.2+ importorskip warns on a plain ImportError)
        pytest.skip('no Qt binding here')
    return qt


def _msg(qt, n):
    v = getattr(qt.QtCore.QtMsgType, n, None)      # PySide6: QtDebugMsg == 0 (falsy)
    return getattr(qt.QtCore, n) if v is None else v


def test_message_levels_and_helpers():
    qt = _qt_or_skip()
    names = [qt.msg_level_name(_msg(qt, n))
             for n in ('QtDebugMsg', 'QtInfoMsg', 'QtWarningMsg', 'QtCriticalMsg', 'QtFatalMsg')]
    assert names == ['DEBUG', 'INFO', 'WARNING', 'CRITICAL', 'FATAL']
    assert qt.js_value((5, False)) == 5 and qt.js_value(7) == 7
    d = qt.compat_qml_dir('/x/qml')
    assert d.replace('\\', '/').endswith('/x/qml/compat')
    assert qt.load_charts() is not None


def test_main_sets_the_style_before_the_engine():
    """Qt 6 on Windows defaults to the native style, which does not allow our
    customised controls (warnings in CI). main.run() must set Fusion first,
    and qa_ui must do the same."""
    src = _read(UI, 'main.py')
    body = src[src.index('def run('):]
    assert body.index('apply_style()') < body.index('QQmlApplicationEngine()')
    ui = _read(ROOT, 'tools', 'qa_ui.py')
    m = ui[ui.index('def main('):]
    assert m.index('ui_main.apply_style()') < m.index('QQmlApplicationEngine()')


def test_window_loads_without_qml_warnings():
    """main.qml with the WwbCompat module of this Qt (the full check is
    tools/qa_ui.py U1-U41)."""
    rc, out = _run(r'''
import sys, types, time, os, tempfile
os.environ['HOME'] = os.environ['USERPROFILE'] = tempfile.mkdtemp(prefix='v214_')
try:
    from wwb_scanner.ui.pyside import qt
except ImportError:
    print('NO_QT'); raise SystemExit(0)
try:
    import rtlsdr
except Exception:
    m = types.ModuleType('rtlsdr')
    class RtlSdr(object):
        def __init__(self, *a, **k): raise IOError('No devices found')
        @staticmethod
        def get_device_serial_addresses(): return []
    m.RtlSdr = RtlSdr; sys.modules['rtlsdr'] = m
from wwb_scanner import scan_dll
scan_dll._cache['lib'] = None
from wwb_scanner.ui.pyside import main as M
app = qt.QApplication([])
M.apply_style()          # as main.run(): Fusion (Qt 6 on Windows: native style warns)
app.setOrganizationName('rtlsdr-wwb-scanner-v214'); app.setApplicationName('wwb_scanner')
warn = []
def h(mode, ctx, msg):
    if qt.msg_level_name(mode) in ('WARNING', 'CRITICAL', 'FATAL') and 'QFontDatabase' not in msg:
        warn.append(msg)
qt.QtCore.qInstallMessageHandler(h)
e = qt.QtQml.QQmlApplicationEngine()
e.addImportPath(str(M.QML_PATH)); e.addImportPath(qt.compat_qml_dir(M.QML_PATH))
M.register_qml_types()
e.load(str(M.QML_PATH / 'main.qml'))
t = time.time()
while time.time() - t < 1.0:
    app.processEvents()
print('ROOTS', len(e.rootObjects()), 'WARN', warn[:3])
''')
    if 'NO_QT' in out:
        pytest.skip('no Qt binding here')
    assert rc == 0 and 'ROOTS 1 WARN []' in out, out[-3000:]


# ---------------------------------------------------------------- QML side

def test_qml_uses_only_the_compat_module_for_dialogs_and_settings():
    for p in glob.glob(os.path.join(QML, '*.qml')):
        t = _read(p)
        assert 'QtQuick.Dialogs' not in t, p
        assert 'Qt.labs.settings' not in t, p
        assert not re.search(r'(?<![\w.])Settings\s*\{', t), p


def test_compat_module_types():
    d = os.path.join(QML, 'compat', 'WwbCompat')
    names = sorted(re.findall(r'^(\w+) 1\.0 (\w+)\.qml$', _read(d, 'qmldir'), re.M))
    assert [n for n, _ in names] == ['WwbColorDialog', 'WwbFileDialog', 'WwbSettings']
    for name, f in names:
        assert os.path.isfile(os.path.join(d, f + '.qml')), f
    assert 'import QtCore' in _read(d, 'WwbSettings.qml')
    fd = _read(d, 'WwbFileDialog.qml')
    for prop in ('saveMode', 'filterText'):
        assert 'property' in fd and prop in fd, prop
    assert not os.path.exists(os.path.join(QML, 'compat', 'qt5'))      # V22.0


def test_no_injected_signal_parameters():
    """Qt 6 warns 'Parameter X is not declared. Injection of parameters into
    signal handlers is deprecated' - handlers must name their parameters."""
    sigs = {}
    files = glob.glob(os.path.join(QML, '*.qml'))
    for p in files:
        for m in re.finditer(r'signal\s+(\w+)\s*\(([^)]*)\)', _read(p)):
            params = [x.split()[-1] for x in m.group(2).split(',') if x.strip()]
            if params:
                sigs[m.group(1)] = params
    builtin = {'onClicked': ['mouse', 'button'], 'onPressed': ['mouse'], 'onReleased': ['mouse'],
               'onPositionChanged': ['mouse'], 'onWheel': ['wheel'], 'onActivated': ['index']}
    bad = []
    for p in files:
        lines = _read(p).split('\n')
        for i, line in enumerate(lines):
            m = re.match(r'\s*(on[A-Z]\w*)\s*:\s*(.*)$', line)
            if not m or m.group(2).startswith('function') or '=>' in m.group(2):
                continue
            h, rest = m.groups()
            name = h[2].lower() + h[3:]
            params = sigs.get(name, []) + builtin.get(h, [])
            if not params:
                continue
            body, depth, j = rest, rest.count('{') - rest.count('}'), i + 1
            while depth > 0 and j < len(lines):
                body += '\n' + lines[j]
                depth += lines[j].count('{') - lines[j].count('}')
                j += 1
            used = [x for x in params if re.search(r'\b%s\b' % x, body)]
            if used:
                bad.append('%s:%d %s uses %s' % (os.path.basename(p), i + 1, h, used))
    assert not bad, bad


def test_file_dialogs_keep_settings_in_a_property():
    """A child Item in the wrapped Qt 5 FileDialog: 'Cannot assign to
    non-existent default property' (and on Linux even before V21.4)."""
    for name in ('ExportDialog.qml', 'ImportDialog.qml'):
        t = _read(QML, name)
        assert 'WwbFileDialog {' in t and 'property var folderSettings: WwbSettings {' in t, name
        assert '\n    Item {' not in t, name
    assert 'saveMode: true' in _read(QML, 'ExportDialog.qml')
    assert 'root.filterText' in _read(QML, 'ExportDialog.qml')


# ---------------------------------------------------------------- packaging

def test_pyside6_for_python_311_and_newer():
    cfg = _read(ROOT, 'setup.cfg')
    req = cfg.split('install_requires =', 1)[1].split('\n\n', 1)[0]
    for name in ('PySide6-Essentials', 'PySide6-Addons'):
        assert re.search(r'^\s*%s>=6\.8;\s*python_version\s*>=\s*"3\.12"' % name, req, re.M), req
        assert re.search(r'^\s*%s>=6\.8,<6\.12;\s*python_version\s*<\s*"3\.12"' % name, req, re.M), req
    assert not re.search(r'^\s*PySide6\s*[>=;]', req, re.M), 'the meta package pulls WebEngine'
    assert 'ui/pyside/qml/compat/WwbCompat/*' in cfg
    assert 'compat' in _read(ROOT, 'MANIFEST.in')


def test_launcher_names_pyside6(monkeypatch, capsys):
    from wwb_scanner.ui import launch
    monkeypatch.setattr(launch, 'qt_problem', lambda: 'No module named PySide6')
    assert launch.run() == 1
    err = capsys.readouterr().err
    assert 'PySide6' in err and 'pip install' in err
