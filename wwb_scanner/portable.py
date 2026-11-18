"""V22.5: the portable program (plan of 18.11.2026, stage 4).

The portable build (``tools/portable``, PyInstaller) is a folder with
``wwb_scanner.exe`` that runs without an installed Python, for example from
a USB stick::

    WWB-Scanner\\
        wwb_scanner.exe
        _internal\\              Python, Qt, numpy, the program, rtlsdr.dll
        data\\                   made on the first start: history, calibrations,
                                 window settings, logs (the usual data folder)
        rtl-tools\\              rtl_test.exe, rtl_eeprom.exe (+ rtlsdr.dll)
        README_PORTABLE.txt

Everything the program writes stays in ``data`` next to the exe, so the
stick can go from PC to PC with its scans and calibrations. A folder that
can not be written to (C:\\Program Files) falls back to the usual data
folder in the user's home (paths.py); ``WWB_SCANNER_DATA`` set by the user
always wins.

A windowed exe has no console: what the program prints goes to
``data\\logs\\console.txt``.
"""
import io
import os
import sys

from wwb_scanner import paths

DATA_NAME = 'data'
_state = {'portable': None, 'data': None, 'why': None}


def frozen():
    """True in the portable build (PyInstaller)."""
    return bool(getattr(sys, 'frozen', False))


def app_dir():
    """The folder of wwb_scanner.exe (None when not the portable build)."""
    if not frozen():
        return None
    return os.path.dirname(os.path.abspath(sys.executable))


def _writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, '.write_test')
        with open(probe, 'w') as fh:
            fh.write('ok')
        os.remove(probe)
        return True
    except OSError:
        return False


def setup():
    """Call first thing in the portable build (ui/launch.py). Sets the data
    folder next to the exe and sends the console output to a file. Returns
    the data folder, or None when not the portable build."""
    if _state['portable'] is not None:
        return _state['data']
    _state['portable'] = frozen()
    if not _state['portable']:
        return None
    if os.environ.get(paths.ENV_VAR):
        _state['why'] = 'the data folder is set by %s' % paths.ENV_VAR
    else:
        folder = os.path.join(app_dir(), DATA_NAME)
        if _writable(folder):
            paths.set_data_dir(folder)
        else:
            _state['why'] = ('%s can not be written to; the data are in %s'
                             % (folder, paths.data_dir()))
    _state['data'] = paths.data_dir(create=True)
    _console_to_file()
    return _state['data']


def is_portable():
    return bool(_state['portable'])


def note():
    """Why the data are not next to the exe (None when they are)."""
    return _state['why']


def _console_to_file():
    """A windowed exe has sys.stdout / sys.stderr None: print() is lost and
    a crash leaves no trace. Both go to data/logs/console.txt."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        path = os.path.join(paths.logs_dir(create=True), 'console.txt')
        if os.path.exists(path) and os.path.getsize(path) > 2000000:
            os.replace(path, path + '.1')
        fh = io.open(path, 'a', encoding='utf-8', errors='replace', buffering=1)
    except OSError:
        fh = io.open(os.devnull, 'w')
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh
