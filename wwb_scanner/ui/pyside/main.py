import os
import sys
from pathlib import Path

from wwb_scanner import log_config
log_config.setup(use_console=True, use_file=True)

import logging
logger = logging.getLogger()

from PySide2 import QtCore, QtQml
from PySide2.QtWidgets import QApplication
from PySide2.QtQuick import QQuickView

from wwb_scanner.ui.pyside import get_resource_filename
from wwb_scanner.ui.pyside import device_config, graph, scanner

def apply_style():
    """Fusion style for Qt Quick Controls 2: compact desktop controls that
    take all colours from the window palette (main.qml, dark/light).
    QQuickStyle affects only Controls 2; the QT_QUICK_CONTROLS_STYLE variable
    is also read by Controls 1 (file dialogs), which has no Fusion and warns.
    A style given in QT_QUICK_CONTROLS_STYLE wins."""
    if os.environ.get('QT_QUICK_CONTROLS_STYLE'):
        return
    try:
        from PySide2.QtQuickControls2 import QQuickStyle
        QQuickStyle.setStyle('Fusion')
    except Exception:
        os.environ['QT_QUICK_CONTROLS_STYLE'] = 'Fusion'

def register_qml_types():
    from wwb_scanner.ui.pyside import status
    status.register_qml_types()
    device_config.register_qml_types()
    graph.register_qml_types()
    scanner.register_qml_types()
    from wwb_scanner.ui.pyside import bands_qt
    bands_qt.register_qml_types()

QML_PATH = get_resource_filename('qml')


def install_error_hooks():
    """V18.1: an error nobody caught (in a button handler, a file dialog,
    a background thread) used to show only in the console, or nowhere.
    Now it goes to the status line, the error dialog and the log file."""
    import logging
    import threading
    log = logging.getLogger('wwb_scanner.ui')
    old = sys.excepthook

    def hook(etype, value, tb):
        if issubclass(etype, KeyboardInterrupt):
            return old(etype, value, tb)
        try:
            log.error('Unexpected error: %s: %s (details in the log file)', etype.__name__, value,
                      exc_info=(etype, value, tb))
        except Exception:
            old(etype, value, tb)
    sys.excepthook = hook
    if getattr(threading, 'excepthook', None) is not None:      # Python 3.8+
        def thook(args):
            hook(args.exc_type, args.exc_value, args.exc_traceback)
        setattr(threading, 'excepthook', thook)

def on_app_quit():
    logger.info('exiting application')

def get_qmsg_levelname(mode):
    if mode == QtCore.QtSystemMsg:
        return None
    for name, value in QtCore.QtMsgType.values.items():
        if mode == value:
            name = name.split('Qt')[1].split('Msg')[0]
            return name.upper()

def qt_message_handler(mode, context, message):
    levelname = get_qmsg_levelname(mode)
    if levelname is None:
        return
    lvl = getattr(logging, levelname)
    # show_trace = lvl not in [logging.INFO, logging.DEBUG]
    fn = QtCore.QUrl(context.file)
    try:                         # V18.1 (audit 3.5): empty or qrc: path
        p = Path(fn.toLocalFile()).relative_to(QML_PATH)
        name = '.'.join(p.parts)
    except ValueError:
        name = 'qml'
    _logger = logging.getLogger(name)
    _logger.log(lvl, message)

def run(argv=None):
    if argv is None:
        argv = sys.argv
    logger.info('starting application')
    app = QApplication(argv)
    install_error_hooks()
    apply_style()
    app.setOrganizationName('rtlsdr-wwb-scanner')
    app.setApplicationName('wwb_scanner')
    app.aboutToQuit.connect(on_app_quit)
    QtCore.qInstallMessageHandler(qt_message_handler)
    engine = QtQml.QQmlApplicationEngine()
    engine.setBaseUrl(str(QML_PATH))
    engine.addImportPath(str(QML_PATH))
    register_qml_types()
    qml_main = QML_PATH / 'main.qml'
    engine.load(str(qml_main))
    win = engine.rootObjects()[0]
    win.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    run()
