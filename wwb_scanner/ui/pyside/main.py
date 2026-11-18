import os
import sys
from pathlib import Path

from wwb_scanner import log_config
# V22.0: the log file is opened in run() (data folder known then), not on import
log_config.setup(use_console=True, use_file=False)

import logging
logger = logging.getLogger()

from wwb_scanner.ui.pyside import qt
from wwb_scanner.ui.pyside.qt import QtCore, QtQml, QApplication

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
    if not qt.set_quick_style('Fusion'):
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

ORG_NAME = 'rtlsdr-wwb-scanner'
APP_NAME = 'wwb_scanner'


def setup_settings(org=ORG_NAME, app_name=APP_NAME, migrate=None):
    """V22.0 (audit 16.11, item 1): the window settings (scan and device
    settings, presets, bands, theme, folders) in an INI file in the data
    folder (wwb_scanner/paths.py), not in the Windows registry: they move
    with the data folder (portable program on a USB stick, second PC) and
    the future web interface can read them.

    The first time, everything the earlier versions stored in the registry
    (Linux: ~/.config) is copied into the file; the registry is not changed,
    so an earlier version still finds its settings. Call after the
    organization and application names are set and before QML is loaded.
    Returns the path of the INI file.

    V22.5: the portable build does not copy (``migrate`` None: copy unless
    portable): a USB stick should not take the settings of each PC."""
    from wwb_scanner import paths
    if migrate is None:
        from wwb_scanner import portable
        migrate = not portable.is_portable()
    S = QtCore.QSettings
    S.setPath(S.Format.IniFormat, S.Scope.UserScope, paths.data_dir(create=True))
    S.setDefaultFormat(S.Format.IniFormat)
    ini = S(S.Format.IniFormat, S.Scope.UserScope, org, app_name)
    path = os.path.normpath(ini.fileName())     # Qt gives / on Windows too
    if migrate and not ini.allKeys():
        native = S(S.Format.NativeFormat, S.Scope.UserScope, org, app_name)
        keys = [k for k in native.allKeys()]
        if keys:
            for k in keys:
                ini.setValue(k, native.value(k))
            ini.sync()
            logger.info('window settings copied from %s to %s (%d values)',
                        os.path.normpath(native.fileName()), path, len(keys))
    return path


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
    return qt.msg_level_name(mode)

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
    if lvl >= logging.WARNING:
        QML_PROBLEMS.append('%s: %s' % (levelname, message))

QML_PROBLEMS = []       # V22.5: QML warnings and errors (selftest)

def run(argv=None, selftest=None):
    """The window. V22.5: ``selftest`` (a file name, or '' for the default)
    loads the window, writes a report and closes (selftest.py)."""
    if argv is None:
        argv = sys.argv
    log_config.add_file_handler()
    logger.info('starting application (%s)', qt.version_text())
    app = QApplication(argv)
    install_error_hooks()
    apply_style()
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    logger.info('window settings: %s', setup_settings())
    app.aboutToQuit.connect(on_app_quit)
    QtCore.qInstallMessageHandler(qt_message_handler)
    engine = QtQml.QQmlApplicationEngine()
    engine.setBaseUrl(str(QML_PATH))
    engine.addImportPath(str(QML_PATH))
    engine.addImportPath(qt.compat_qml_dir(QML_PATH))     # WwbCompat module
    register_qml_types()
    qml_main = QML_PATH / 'main.qml'
    engine.load(str(qml_main))
    if selftest is not None:
        from wwb_scanner.ui import selftest as st
        return st.finish(app, engine, selftest, QML_PROBLEMS)
    win = engine.rootObjects()[0]
    win.show()
    sys.exit(qt.exec_app(app))

if __name__ == '__main__':
    run()
