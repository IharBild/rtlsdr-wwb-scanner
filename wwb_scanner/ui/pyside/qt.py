"""Qt binding of the window: PySide2 (Qt 5.15) or PySide6 (Qt 6).

V21.4: PySide2 exists only for Python 3.7-3.10; PySide6 for Python 3.9+.
Everything in the window imports Qt from here, never from PySide2/PySide6
directly. PySide2 is tried first (the tested stack of the maintainer's PC);
WWB_QT=pyside6 (or pyside2) changes the order when both are installed.

Differences handled here:
- QtCharts: PySide2 has a namespace class ``QtCharts.QtCharts``; PySide6
  has the classes in the module. Only the import matters to us (the chart
  types must be loaded before QML uses them).
- ``exec_()`` (PySide2) / ``exec()`` (PySide6).
- QtMsgType values: PySide2 ``QtMsgType.values``; PySide6 Python enums.
- The QML dialogs: QtQuick.Dialogs 1.x (Qt 5) and QtQuick.Dialogs (Qt 6) have
  different APIs; ``qml/compat/qt5`` and ``qml/compat/qt6`` give the same
  WwbDialogs module to the rest of the QML (``compat_qml_dir``).
"""
import os

API = None
_ORDER = ('PySide2', 'PySide6')
if os.environ.get('WWB_QT', '').strip().lower() == 'pyside6':
    _ORDER = ('PySide6', 'PySide2')

_errors = []
for _name in _ORDER:
    try:
        if _name == 'PySide2':
            from PySide2 import QtCore, QtGui, QtQml, QtQuick, QtWidgets
        else:
            from PySide6 import QtCore, QtGui, QtQml, QtQuick, QtWidgets
        API = _name
        break
    except ImportError as exc:            # not installed, or not for this Python
        _errors.append('%s: %s' % (_name, exc))
if API is None:
    raise ImportError('The window needs PySide2 (Python 3.7-3.10) or PySide6 '
                      '(Python 3.9+): ' + '; '.join(_errors))

QT6 = API == 'PySide6'
Signal = QtCore.Signal
Slot = QtCore.Slot
Property = QtCore.Property
QObject = QtCore.QObject
QApplication = QtWidgets.QApplication
QQuickView = QtQuick.QQuickView


def version_text():
    """'PySide6 6.12.0, Qt 6.12.0' - for the log and the QA reports."""
    try:
        mod = __import__(API)
        return '%s %s, Qt %s' % (API, mod.__version__, QtCore.qVersion())
    except Exception:
        return API


def load_charts():
    """Import QtCharts so its types exist for QML and for objects handed
    to Python (QXYSeries.replace in graph.py)."""
    if QT6:
        from PySide6 import QtCharts
        return QtCharts
    from PySide2.QtCharts import QtCharts
    return QtCharts


def exec_app(app):
    run = getattr(app, 'exec', None) if QT6 else getattr(app, 'exec_', None)
    if run is None:
        run = getattr(app, 'exec_', None) or getattr(app, 'exec')
    return run()


def set_quick_style(name):
    """Style of Qt Quick Controls 2 (False if not possible)."""
    try:
        if QT6:
            from PySide6.QtQuickControls2 import QQuickStyle
        else:
            from PySide2.QtQuickControls2 import QQuickStyle
        QQuickStyle.setStyle(name)
        return True
    except Exception:
        return False


_MSG_LEVELS = (('QtDebugMsg', 'DEBUG'), ('QtInfoMsg', 'INFO'), ('QtWarningMsg', 'WARNING'),
               ('QtCriticalMsg', 'CRITICAL'), ('QtFatalMsg', 'FATAL'))


def msg_level_name(mode):
    """Logging level name of a QtMsgType (None for an unknown one)."""
    for qt_name, name in _MSG_LEVELS:
        value = getattr(QtCore.QtMsgType, qt_name, None)
        if value is None:
            value = getattr(QtCore, qt_name, None)
        if value is not None and mode == value:
            return name
    return None


def compat_qml_dir(qml_root):
    """Folder with the WwbDialogs QML module for this Qt."""
    return os.path.join(str(qml_root), 'compat', 'qt6' if QT6 else 'qt5')


def js_value(val):
    """A value read from QML: PySide2 sometimes gives (value, isUndefined)."""
    if isinstance(val, tuple) and len(val) == 2 and isinstance(val[1], bool):
        return val[0]
    if hasattr(val, 'toVariant'):                     # QJSValue
        return val.toVariant()
    return val
