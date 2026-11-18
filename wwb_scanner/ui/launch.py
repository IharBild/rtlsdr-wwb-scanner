"""Entry point of the program window (wwb_scanner-ui).

V21.3: a clear message instead of a traceback when there is no Qt binding.
V22.0: the window runs on PySide6 (Python 3.11+).
V22.5: --version, --selftest; the portable build (wwb_scanner/portable.py).
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


def version():
    try:
        from importlib import metadata
        return metadata.version('rtlsdr-wwb-scanner')
    except Exception:
        return 'unknown'


def _arg_value(argv, name):
    """``--name value`` or ``--name=value``; '' when given without a value,
    None when not given."""
    for i, a in enumerate(argv):
        if a == name:
            nxt = argv[i + 1] if i + 1 < len(argv) else ''
            return '' if nxt.startswith('--') else nxt
        if a.startswith(name + '='):
            return a.split('=', 1)[1]
    return None


def run(argv=None):
    """``wwb_scanner-ui`` / ``wwb_scanner.exe``.

    V22.5:
      --version            print the version and exit
      --selftest [FILE]    load the window, check the dongle library, write a
                           report (default: logs/selftest.txt in the data
                           folder), close; exit code 0 when all is right
    """
    argv = list(sys.argv if argv is None else argv)
    # V22.5: the portable build keeps its data next to the exe (portable.py)
    from wwb_scanner import portable
    portable.setup()
    if '--version' in argv:
        print('rtlsdr-wwb-scanner %s' % version())
        return 0
    selftest = _arg_value(argv, '--selftest')
    why = qt_problem()
    if why is not None:
        sys.stderr.write(NO_QT % {'py': '%d.%d' % sys.version_info[:2], 'why': why})
        return 1
    from wwb_scanner.ui.pyside import main
    qt_argv = [a for a in argv if a != '--selftest' and not a.startswith('--selftest=')
               and a != selftest]
    return main.run(qt_argv, selftest=selftest)


if __name__ == '__main__':
    sys.exit(run())
