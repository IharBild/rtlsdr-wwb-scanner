"""Messages for the window (V16): warnings and errors of the scanner shown
in the status line at the bottom, errors also in a dialog, and everything
written to the log file of the program (V22.0: logs/ in the data folder).

StatusHub is a QML type (main.qml creates one). Python code does not need
to know about it: it logs with the standard logging module; the core turns
records of the "wwb_scanner" loggers (WARNING and above, plus INFO of
"wwb_scanner.status") into status events (V22.1, wwb_scanner/events.py) and
the hub that exists at that moment shows them. Events can come from the
scan thread; they reach QML through a queued signal.
"""
import logging
import logging.handlers
import sys

from wwb_scanner.ui.pyside.qt import QtCore, QtQml, Signal, Property, Slot

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


def _on_event(event):
    """V22.1: status events of the core (wwb_scanner/events.py) -> the hub
    of the window (any thread; the hub queues them to the window thread)."""
    hub = _active['hub']
    if hub is not None:
        hub.post(event.get('level', 'info'), event.get('text', ''))


def setup_logging():
    """Once per process: status handler, log file, console for warnings."""
    if _state['logging']:
        return
    _state['logging'] = True
    log = logging.getLogger('wwb_scanner')
    if log.level == logging.NOTSET or log.level > logging.INFO:
        log.setLevel(logging.INFO)
    # V22.1: the core sends status events; the window listens on the bus
    # (was a logging handler of its own with the same rule)
    from wwb_scanner import events
    events.install_log_bridge()
    events.bus.subscribe(_on_event, kinds=('status',))
    try:
        # V22.0: the one log file of the program (log_config, data folder)
        from wwb_scanner import log_config
        _state['log_path'] = log_config.add_file_handler()
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
