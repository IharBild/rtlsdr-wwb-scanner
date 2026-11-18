"""Entry point of the program window (wwb_scanner-ui).

V21.3: a clear message instead of a traceback when there is no Qt binding.
V22.0: the window runs on PySide6 (Python 3.11+).
"""
import sys

NO_QT = (
    'The program window needs PySide6; it does not load for this Python (%(py)s): %(why)s\n'
    'Install it into this environment:  python -m pip install "PySide6-Essentials>=6.8" "PySide6-Addons>=6.8"\n'
    '(or reinstall the scanner: pip installs it with the program)\n'
    '\n'
    'Окну программы нужен PySide6; для этого Python (%(py)s) он не загружается.\n'
    'Установите в это окружение:  python -m pip install "PySide6-Essentials>=6.8" "PySide6-Addons>=6.8"\n'
    '(или переустановите программу: pip поставит его вместе с ней)\n'
)


def qt_problem():
    """None when the Qt binding loads, else the reason (text)."""
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
