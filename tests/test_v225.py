"""V22.5: the portable program (wwb_scanner/portable.py, ui/launch.py,
ui/selftest.py, tools/portable)."""
import os
import sys

import pytest

from wwb_scanner import paths, portable
from wwb_scanner.ui import launch


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(portable, '_state', {'portable': None, 'data': None, 'why': None})
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    yield
    paths.set_data_dir(None)


def _frozen(monkeypatch, tmpdir):
    exe = tmpdir.mkdir('WWB-Scanner').join('wwb_scanner.exe')
    exe.write('')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe))
    return os.path.dirname(str(exe))


def test_not_portable_changes_nothing(fresh):
    assert not portable.frozen()
    assert portable.setup() is None
    assert not portable.is_portable()
    assert portable.app_dir() is None


def test_portable_keeps_its_data_next_to_the_exe(fresh, monkeypatch, tmpdir):
    folder = _frozen(monkeypatch, tmpdir)
    data = portable.setup()
    assert data == os.path.join(folder, 'data') == paths.data_dir()
    assert portable.is_portable() and portable.note() is None
    # calibrations, logs and the history go there too
    assert paths.calibration_root().startswith(data)
    assert paths.log_file().startswith(data)
    assert portable.setup() == data          # once


def test_the_users_data_folder_wins(fresh, monkeypatch, tmpdir):
    _frozen(monkeypatch, tmpdir)
    monkeypatch.setenv(paths.ENV_VAR, str(tmpdir.join('mine')))
    assert portable.setup() == str(tmpdir.join('mine'))
    assert 'WWB_SCANNER_DATA' in portable.note()


def test_a_folder_that_can_not_be_written_falls_back(fresh, monkeypatch, tmpdir):
    _frozen(monkeypatch, tmpdir)
    monkeypatch.setattr(portable, '_writable', lambda folder: False)
    monkeypatch.setenv('HOME', str(tmpdir.join('home')))
    monkeypatch.setenv('USERPROFILE', str(tmpdir.join('home')))
    data = portable.setup()
    assert data == paths.data_dir() and 'can not be written' in portable.note()
    assert os.path.join('WWB-Scanner', 'data') not in data


def test_a_windowed_exe_prints_into_a_file(fresh, monkeypatch, tmpdir):
    folder = _frozen(monkeypatch, tmpdir)
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    portable.setup()
    print('hello from the exe')
    sys.stderr.write('and an error\n')
    sys.stdout.flush()
    text = open(os.path.join(folder, 'data', 'logs', 'console.txt'), encoding='utf-8').read()
    assert 'hello from the exe' in text and 'and an error' in text


def test_version_and_arguments(capsys, fresh):
    assert launch.run(['wwb_scanner.exe', '--version']) == 0
    assert capsys.readouterr().out.startswith('rtlsdr-wwb-scanner ')
    def a(argv):
        return launch._arg_value(argv, '--selftest')
    assert a(['x', '--selftest']) == ''
    assert a(['x', '--selftest', 'r.txt']) == 'r.txt'
    assert a(['x', '--selftest=r.txt']) == 'r.txt'
    assert a(['x', '--selftest', '--other']) == ''
    assert a(['x']) is None


def test_portable_window_does_not_take_the_pcs_settings(fresh, monkeypatch, tmpdir):
    """The registry (NativeFormat) settings are copied to the INI only for an
    installed program, not for the portable one."""
    qt = pytest.importorskip('wwb_scanner.ui.pyside.qt')
    from wwb_scanner.ui.pyside import main
    S = qt.QtCore.QSettings
    org, app = 'rtlsdr-wwb-scanner-test225', 'wwb_scanner'
    native = S(S.Format.NativeFormat, S.Scope.UserScope, org, app)
    native.setValue('Test/value', 7)
    native.sync()
    try:
        _frozen(monkeypatch, tmpdir)
        portable.setup()
        path = main.setup_settings(org, app)
        assert S(path, S.Format.IniFormat).allKeys() == []
        # the installed program copies
        paths.set_data_dir(str(tmpdir.join('installed')))
        path = main.setup_settings(org, app, migrate=True)
        assert S(path, S.Format.IniFormat).value('Test/value') in (7, '7')
    finally:
        native.clear()
        native.sync()
        S.setDefaultFormat(S.Format.NativeFormat)


def test_selftest_report(monkeypatch, tmpdir):
    """The window loads, the report is written; without a dongle library the
    exit code says so."""
    pytest.importorskip('wwb_scanner.ui.pyside.qt')
    import subprocess
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', WWB_SCANNER_DATA=str(tmpdir.join('d')))
    out = str(tmpdir.join('selftest.txt'))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, '-m', 'wwb_scanner.ui.launch', '--selftest', out],
                       cwd=root, env=env, capture_output=True, text=True, timeout=180)
    text = open(out, encoding='utf-8').read()
    assert 'OK   window: loaded, no QML warnings' in text, text + r.stderr[-2000:]
    assert 'OK   data folder: ' + str(tmpdir.join('d')) in text
    lib_ok = 'OK   dongle library' in text
    assert (r.returncode == 0) == lib_ok, text
