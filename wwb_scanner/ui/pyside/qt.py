"""Qt binding of the window: PySide6 (Qt 6).

V21.4: PySide2 (Qt 5.15) and PySide6 (Qt 6) were both supported.
V22.0: PySide6 only (the program needs Python 3.11+; PySide2 does not exist
for it). Everything in the window still imports Qt from here, never from
PySide6 directly, so a later binding change touches one file.
"""
import os

from PySide6 import QtCore, QtGui, QtQml, QtQuick, QtWidgets

API = 'PySide6'
QT6 = True
Signal = QtCore.Signal
Slot = QtCore.Slot
Property = QtCore.Property
QObject = QtCore.QObject
QApplication = QtWidgets.QApplication
QQuickView = QtQuick.QQuickView


def version_text():
    """'PySide6 6.12.0, Qt 6.12.0' - for the log and the QA reports."""
    try:
        import PySide6
        return 'PySide6 %s, Qt %s' % (PySide6.__version__, QtCore.qVersion())
    except Exception:
        return API


def load_charts():
    """Import QtCharts so its types exist for QML and for objects handed
    to Python (QXYSeries.replace in graph.py)."""
    from PySide6 import QtCharts
    return QtCharts


def exec_app(app):
    return app.exec()


def set_quick_style(name):
    """Style of Qt Quick Controls 2 (False if not possible)."""
    try:
        from PySide6.QtQuickControls2 import QQuickStyle
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
        if value is not None and mode == value:
            return name
    return None


def compat_qml_dir(qml_root):
    """Folder with the WwbCompat QML module (file dialog, colour dialog,
    settings with the API the rest of the QML uses)."""
    return os.path.join(str(qml_root), 'compat')


def js_value(val):
    """A value read from QML as a plain Python value."""
    if isinstance(val, tuple) and len(val) == 2 and isinstance(val[1], bool):
        return val[0]                     # QQmlExpression.evaluate(): (value, isUndefined)
    if hasattr(val, 'toVariant'):         # QJSValue
        return val.toVariant()
    return val
