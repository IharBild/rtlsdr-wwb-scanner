"""V22.5: ``wwb_scanner.exe --selftest`` (``wwb_scanner-ui --selftest``).

Checks what a new PC (or the CI build) needs, without a scan:
the window loads without QML errors, the dongle library (rtlsdr.dll) loads,
the data folder can be written to. Lists the dongles that are plugged in
(none is not an error: the build machine has none).

The report goes to ``logs/selftest.txt`` in the data folder (or the file
given), and to the console when there is one. Exit code 0: all is right.
"""
import datetime
import io
import os
import sys


def _lines_and_ok(engine, qml_problems):
    from wwb_scanner import dllpath, paths, portable
    from wwb_scanner.ui import launch
    from wwb_scanner.ui.pyside import qt
    lines, ok = [], True

    def line(status, what, detail=''):
        lines.append('%-4s %s%s' % (status, what, (': %s' % detail) if detail else ''))

    line('INFO', 'rtlsdr-wwb-scanner', launch.version())
    line('INFO', 'Python', '%s (%s)' % (sys.version.split()[0], sys.executable))
    line('INFO', 'portable build', 'yes' if portable.is_portable() else 'no')
    line('INFO', 'Qt', qt.version_text())
    # data folder
    data = paths.data_dir()
    try:
        probe = os.path.join(paths.data_dir(create=True), '.selftest')
        with open(probe, 'w') as fh:
            fh.write('ok')
        os.remove(probe)
        line('OK', 'data folder', data + ('' if not portable.note() else ' (%s)' % portable.note()))
    except OSError as exc:
        ok = False
        line('FAIL', 'data folder', '%s: %s' % (data, exc))
    # window
    roots = engine.rootObjects()
    if roots and not qml_problems:
        line('OK', 'window', 'loaded, no QML warnings')
    else:
        ok = False
        line('FAIL', 'window', '%d window(s); QML: %s' % (len(roots), '; '.join(qml_problems[:8]) or '-'))
    # every QML file of the program compiles (its imports exist in this Qt:
    # the portable build leaves unused Qt modules out)
    try:
        from wwb_scanner.ui.pyside.main import QML_PATH
        bad, n = [], 0
        for f in sorted(QML_PATH.rglob('*.qml')):
            n += 1
            comp = qt.QtQml.QQmlComponent(engine, qt.QtCore.QUrl.fromLocalFile(str(f)))
            if comp.isError():
                bad.append('%s: %s' % (f.name, '; '.join(e.toString() for e in comp.errors())[:300]))
        if bad:
            ok = False
            line('FAIL', 'QML files', '%d of %d do not compile: %s' % (len(bad), n, ' | '.join(bad[:5])))
        else:
            line('OK', 'QML files', '%d compile' % n)
    except Exception as exc:
        ok = False
        line('FAIL', 'QML files', '%s: %s' % (type(exc).__name__, exc))
    # dongle library
    try:
        line('INFO', 'DLL folder', dllpath.find_dll_dir() or '-')
        import importlib
        importlib.import_module('rtlsdr.librtlsdr')      # loads the library
        from wwb_scanner import scan_dll
        info = scan_dll.build_info()
        line('OK', 'dongle library', info or 'stock librtlsdr (no PLL lock check)')
    except Exception as exc:
        ok = False
        line('FAIL', 'dongle library', '%s: %s' % (type(exc).__name__, exc))
    # dongles
    try:
        from rtlsdr import RtlSdr
        serials = list(RtlSdr.get_device_serial_addresses())
        from wwb_scanner import dongles
        dup = dongles.duplicates(serials)
        line('INFO', 'dongles plugged in', '%d %s%s' % (
            len(serials), serials,
            ('; the same serial number: %s (see README)' % dup) if dup else ''))
    except Exception as exc:
        line('INFO', 'dongles plugged in', 'not listed: %s' % exc)
    return lines, ok


def finish(app, engine, out, qml_problems):
    """Called by ui/pyside/main.run after the window is loaded. Returns the
    exit code."""
    from wwb_scanner import paths
    from wwb_scanner.ui.pyside import qt
    # let QML finish loading (Loaders, bindings) before judging it
    timer = qt.QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(app.quit)
    timer.start(1500)
    qt.exec_app(app)
    try:
        lines, ok = _lines_and_ok(engine, qml_problems)
    except Exception as exc:
        lines, ok = ['FAIL selftest: %s: %s' % (type(exc).__name__, exc)], False
    head = 'selftest %s: %s' % (datetime.datetime.now().isoformat(timespec='seconds'),
                                'all right' if ok else 'PROBLEMS')
    text = '\n'.join([head] + lines) + '\n'
    path = out or os.path.join(paths.logs_dir(create=True), 'selftest.txt')
    try:
        with io.open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
    except OSError:
        pass
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass
    for w in engine.rootObjects():
        try:
            w.close()
        except Exception:
            pass
    return 0 if ok else 1
