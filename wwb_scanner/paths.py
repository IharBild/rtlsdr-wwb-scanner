"""V22.0: where the program keeps its data.

One place for all files of the program (audit 16.11, item 2). Before V22.0
the folder ``~/wwb_scanner_data`` was written into four modules and the log
went to a second folder (``~/.config/rtlsdr-wwb-scanner/logs``).

The data folder is, in this order:

1. the folder given to :func:`set_data_dir` (a launcher, a test, the web
   server later);
2. the environment variable ``WWB_SCANNER_DATA``;
3. ``~/wwb_scanner_data`` (as before: existing scans, calibrations and
   settings are found where they are).

Inside it::

    db.json              scan settings of past scans
    scan_index.json      history of scans (description of each scan)
    scans/*.npz          points of every scan in the history
    calibration/         floor calibrations, gain step table, gain profile
    logs/                log files
    rtlsdr-wwb-scanner/wwb_scanner.ini
                         settings of the window (V22.0; was the registry)
    wwb_scan_timing.txt  time report of the last scan

Everything is resolved when it is asked for, never at import time, so a
test or a launcher can change the folder before the first use.
"""
import os

ENV_VAR = 'WWB_SCANNER_DATA'
DEFAULT_NAME = 'wwb_scanner_data'

_override = None


def set_data_dir(path):
    """Use ``path`` as the data folder for this process (None: default)."""
    global _override
    _override = None if path is None else os.path.abspath(os.path.expanduser(str(path)))


def default_data_dir():
    return os.path.join(os.path.expanduser('~'), DEFAULT_NAME)


def data_dir(create=False):
    if _override is not None:
        d = _override
    elif os.environ.get(ENV_VAR, '').strip():
        d = os.path.abspath(os.path.expanduser(os.environ[ENV_VAR].strip()))
    else:
        d = default_data_dir()
    if create and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def _sub(name, create):
    d = os.path.join(data_dir(), name)
    if create and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def file_path(name, create_dir=False):
    """A file directly in the data folder."""
    return os.path.join(data_dir(create=create_dir), name)


def scans_dir(create=False):
    return _sub('scans', create)


def calibration_dir(create=False):
    return _sub('calibration', create)


def logs_dir(create=False):
    return _sub('logs', create)


def log_file(create_dir=False):
    return os.path.join(logs_dir(create_dir), 'wwb_scanner.log')


def settings_file(org='rtlsdr-wwb-scanner', app='wwb_scanner'):
    """INI file of the window settings (QSettings IniFormat with the data
    folder as its user path, see ui/pyside/main.setup_settings)."""
    return os.path.join(data_dir(), org, app + '.ini')


def timing_report_file(create_dir=False):
    return file_path('wwb_scan_timing.txt', create_dir)
