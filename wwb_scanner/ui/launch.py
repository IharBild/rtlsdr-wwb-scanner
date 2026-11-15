"""Entry point of the program window (wwb_scanner-ui).

V21.3: a clear message instead of a traceback when there is no Qt binding.
V21.4: the window runs on PySide2 (Python 3.7-3.10) or PySide6 (Python 3.10+).
"""
import sys

NO_QT = (
    'The program window needs PySide2 (Python 3.7-3.10) or PySide6 (Python 3.10+);\n'
    'neither works for this Python (%(py)s): %(why)s\n'
    'Install it into this environment:  python -m pip install "PySide6>=6.8"\n'
    '(or reinstall the scanner: pip picks the right one for the Python version)\n'
    '\n'
    'Окну программы нужен PySide2 (Python 3.7-3.10) или PySide6 (Python 3.10+);\n'
    'для этого Python (%(py)s) нет ни того, ни другого.\n'
    'Установите в это окружение:  python -m pip install "PySide6>=6.8"\n'
    '(или переустановите программу: pip сам выберет нужный под версию Python)\n'
)


def qt_problem():
    """None when a Qt binding loads, else the reason (text)."""
    try:
        from wwb_scanner.ui.pyside import qt  # noqa: F401
    except ImportError as exc:
        return str(exc)
    return None


def run():
    why = qt_problem()
    if why is not None:
        sys.stderr.write(NO_QT % {'py': '%d.%d' % sys.version_info[:2], 'why': why})
        return 1
    from wwb_scanner.ui.pyside import main
    return main.run()


if __name__ == '__main__':
    sys.exit(run())
