"""Entry point of the program window (wwb_scanner-ui).

V21.3: on Python 3.11+ PySide2 does not exist, and the window waits for the
PySide6 port. Instead of a traceback the user gets a short explanation of
what works and what to do.
"""
import sys

NO_QT = (
    'The program window needs PySide2, which is not installed for this Python '
    '(%(py)s).\n'
    'PySide2 exists only for Python 3.7-3.10; the window for Python 3.11+ '
    '(PySide6) is the next version.\n'
    'For the window now: use your Python 3.7-3.10 environment.\n'
    '\n'
    'Окно программы требует PySide2, а для этого Python (%(py)s) его нет.\n'
    'PySide2 есть только для Python 3.7-3.10; окно для Python 3.11+ '
    '(PySide6) будет в следующей версии.\n'
    'Чтобы работать с окном сейчас, запустите его из окружения Python 3.7-3.10.\n'
)


def pyside_missing():
    try:
        __import__('PySide2')
    except ImportError:
        return True
    return False


def run():
    if pyside_missing():
        sys.stderr.write(NO_QT % {'py': '%d.%d' % sys.version_info[:2]})
        return 1
    from wwb_scanner.ui.pyside import main
    return main.run()


if __name__ == '__main__':
    sys.exit(run())
