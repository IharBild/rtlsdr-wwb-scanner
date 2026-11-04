"""Messages for the window (V16): warnings and errors of the scanner shown
in the status line at the bottom, errors also in a dialog, and everything
written to a log file next to the scan database.

StatusHub is a QML type (main.qml creates one). Python code does not need
to know about it: it logs with the standard logging module, and a handler
forwards records of the "wwb_scanner" loggers (WARNING and above, plus INFO
of "wwb_scanner.status") to the hub that exists at that moment. Records can
come from the scan thread; they reach QML through a queued signal.
"""
import logging
import logging.handlers
import os
import sys

from PySide2 import QtCore, QtQml
from PySide2.QtCore import Signal, Property, Slot

STATUS_LOGGER = 'wwb_scanner.status'
_active = {'hub': None}
_state = {'logging': False, 'log_path': None}


def log_path():
    return _state['log_path']


class StatusHub(QtCore.QObject):
    _post = Signal(str, str)
    message = Signal(str, str)          # level ('info' / 'warning' / 'error'), text
    _n_text = Signal()
    _n_level = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ''
        self._level = 'info'
        self._post.connect(self._on_post, QtCore.Qt.QueuedConnection)
        _active['hub'] = self
        setup_logging()

    def post(self, level, text):
        """Any thread."""
        self._post.emit(level, text)

    @Slot(str, str)
    def _on_post(self, level, text):
        self._text = text
        self._level = level
        self._n_text.emit()
        self._n_level.emit()
        self.message.emit(level, text)

    def _g_text(self): return self._text
    text = Property(str, _g_text, notify=_n_text)

    def _g_level(self): return self._level
    level = Property(str, _g_level, notify=_n_level)

    def _g_logFile(self): return _state['log_path'] or ''
    logFile = Property(str, _g_logFile, constant=True)

    @Slot()
    def clear(self):
        self._on_post('info', '')


class StatusLogHandler(logging.Handler):
    def emit(self, record):
        hub = _active['hub']
        if hub is None:
            return
        try:
            if record.levelno < logging.WARNING and not record.name.startswith(STATUS_LOGGER):
                return
            if record.levelno >= logging.ERROR:
                level = 'error'
            elif record.levelno >= logging.WARNING:
                level = 'warning'
            else:
                level = 'info'
            text = record.getMessage().strip().split('\n')[0][:400]
            hub.post(level, text)
        except Exception:
            pass


def setup_logging():
    """Once per process: status handler, log file, console for warnings."""
    if _state['logging']:
        return
    _state['logging'] = True
    log = logging.getLogger('wwb_scanner')
    if log.level == logging.NOTSET or log.level > logging.INFO:
        log.setLevel(logging.INFO)
    log.addHandler(StatusLogHandler(logging.INFO))
    try:
        folder = os.path.join(os.path.expanduser('~'), 'wwb_scanner_data')
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = os.path.join(folder, 'wwb_scanner.log')
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1000000, backupCount=3, encoding='utf-8')
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        log.addHandler(fh)
        _state['log_path'] = path
    except Exception:
        pass
    if not logging.getLogger().handlers:
        # without a console handler warnings would still reach the console
        # through logging's last resort; keep that behaviour explicit
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        log.addHandler(ch)


def register_qml_types():
    QtQml.qmlRegisterType(StatusHub, 'StatusTools', 1, 0, 'StatusHub')
