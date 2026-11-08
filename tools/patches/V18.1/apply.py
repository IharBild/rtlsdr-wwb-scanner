"""V18.1 "Reliability": fixes after the independent code audit (2026-11-07).

Apply to a tree that contains wwb_scanner/ (user install copy or repo).
Each fix is marked "V18.1 (audit N.N)" in the code.
"""
import os
import sys

ROOT = os.path.join(sys.argv[1], 'wwb_scanner')
APPLIED, SKIPPED = [], []


def patch(rel, pairs, optional=False):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        if optional:
            SKIPPED.append(rel)
            return
        raise SystemExit('missing ' + rel)
    s = open(p, encoding='utf-8').read()
    for pair in pairs:
        if isinstance(pair, list):          # alternatives: user install / repo
            pair = next((x for x in pair if s.count(x[0]) == 1), pair[0])
        a, b = pair
        n = s.count(a)
        if n != 1:
            if optional:
                SKIPPED.append('%s: %s' % (rel, a.strip().splitlines()[0][:60]))
                continue
            raise SystemExit('%s: anchor found %d times: %r' % (rel, n, a[:80]))
        s = s.replace(a, b)
    open(p, 'w', encoding='utf-8', newline='\n').write(s)
    APPLIED.append(rel)


# ------------------------------------------------------------------ 1.1 no USB in the window thread
patch('scanner/main.py', [
    ("""        self.sdr_wrapper = SdrWrapper(scanner=self)
        self.gain = self.gain
""", """        self.sdr_wrapper = SdrWrapper(scanner=self)
        # V18.1 (audit 1.1): the gain used to be rounded to the dongle's
        # gain table here, which opened the dongle in the window thread
        # (no timeout: a stuck USB froze the window). Now the table is
        # read when the scan opens the dongle (SdrWrapper.open_sdr).
"""),
    ("""    @gain.setter
    def gain(self, value):
        if value is not None and hasattr(self, 'sdr_wrapper'):
            value = self.get_nearest_gain(value)
        self.device_config.gain = value""", """    @gain.setter
    def gain(self, value):
        # V18.1: round only with a gain table already read from the dongle;
        # never open the dongle from here
        if value is not None and getattr(self, '_gains', None):
            value = self.get_nearest_gain(value)
        self.device_config.gain = value
    def learn_gains(self, sdr):
        \"\"\"V18.1: read the gain table of an open dongle and round the gain.\"\"\"
        try:
            gains = [g / 10. for g in sdr.get_gains()]
        except Exception:
            return
        if gains:
            self._gains = gains
            if self.gain is not None:
                self.gain = self.gain"""),
    # 1.2: a dongle that hung in the last scan is not used again
    ("""    def run_scan(self):
        with self.sdr_wrapper:
            super(Scanner, self).run_scan()
    def _run_scan(self, running):
        if self.sdr is None:""", """    def run_scan(self):
        # V18.1 (audit 1.2): a USB call of an earlier scan never returned.
        # Opening the dongle again would only hang another thread.
        from wwb_scanner.scanner.sample_processing import stuck_usb_calls
        if stuck_usb_calls():
            self._refused = True
            return super(Scanner, self).run_scan()
        with self.sdr_wrapper:
            super(Scanner, self).run_scan()
    def _run_scan(self, running):
        if getattr(self, '_refused', False):
            raise IOError('The dongle stopped answering in the last scan. Unplug it, plug it back '
                          'in and start again; if this message comes back, restart the program')
        if self.sdr is None:"""),
    # 2.2 (code is not used now, but keep it safe)
    ("""            f_min, f_max = self.config.scan_range
            self.progress = (value - f_min) / (f_max - f_min)""",
     """            f_min, f_max = self.config.scan_range
            # V18.1 (audit 2.2): a range of one point
            self.progress = (value - f_min) / (f_max - f_min) if f_max > f_min else 0."""),
    # 1.5 / 3.1
    ("""def scan_and_plot(**kwargs):""", """def scan_and_plot(**kwargs):
    # not used by the window (old command line helper)"""),
    ("""def scan_and_save(filename=None, frequency_format=None, **kwargs):
    scanner = Scanner(**kwargs)""", """def scan_and_save(filename=None, frequency_format=None, **kwargs):
    # not used by the window (command line helper)
    if not filename:
        raise ValueError('scan_and_save: give a file name (.csv, .sdb2, .sdb3 or .npz)')
    scanner = Scanner(**kwargs)"""),
])

patch('scanner/sdrwrapper.py', [
    ("""                if self.sdr is not None:
                    self.set_sdr_values()
                    self.device_open.set()""", """                if self.sdr is not None:
                    # V18.1 (audit 1.1): gain table read here, in the scan thread
                    learn = getattr(self.scanner, 'learn_gains', None)
                    if learn is not None and self.enable_scanner_updates:
                        learn(self.sdr)
                    self.set_sdr_values()
                    self.device_open.set()"""),
])

patch('scanner/sample_processing.py', [
    ("""def run_with_timeout(func, timeout, *args):""", """_STUCK = []      # V18.1: helper threads still inside a USB call that never returned


def stuck_usb_calls():
    \"\"\"V18.1 (audit 1.2): how many USB calls of earlier scans still hang.\"\"\"
    _STUCK[:] = [t for t in _STUCK if t.is_alive()]
    return len(_STUCK)


def run_with_timeout(func, timeout, *args):"""),
    ("""    if t.is_alive():
        raise SdrTimeout('no answer from the dongle in %s s' % timeout)""",
     """    if t.is_alive():
        _STUCK.append(t)
        raise SdrTimeout('no answer from the dongle in %s s' % timeout)"""),
    ("""    def translate_freq(""", """    # not used (old tile layout), kept for reference
    def translate_freq("""),
])

# ------------------------------------------------------------------ 1.3 / 1.4 scale and smooth
patch('scan_objects/spectrum.py', [
    ("""    def smooth(self, N):
        with self.data_update_lock:
            if N % 2 != 0:
                N += 1
            self.sample_data.smooth(N)
        self.set_data_updated()""", """    def smooth(self, N):
        \"\"\"Returns False when the scan is too short for this window (V18.1,
        audit 1.4: it used to fail and leave the window in "scanning").\"\"\"
        N = int(N)
        if N % 2 != 0:
            N += 1
        with self.data_update_lock:
            done = self.sample_data.smooth(N)
        if done:
            self.set_data_updated()
        return done"""),
    ("""    def scale(self, min_dB, max_dB):
        with self.data_update_lock:
            y = self.sample_data['dbFS']
            ymin = y.min()
            ymax = y.max()""", """    def scale(self, min_dB, max_dB):
        \"\"\"Returns False when there is nothing to scale (V18.1, audit 1.3:
        an empty or flat scan used to fail or give NaN).\"\"\"
        y = self.sample_data['dbFS']
        if y.size == 0 or not np.isfinite(y).all() or float(y.max() - y.min()) <= 0:
            return False
        with self.data_update_lock:
            y = self.sample_data['dbFS']
            ymin = y.min()
            ymax = y.max()"""),
    ("""            self.sample_data['magnitude'] = dbmath.from_dB(y)
            self.sample_data['dbFS'] = y
        self.set_data_updated()
    def add_sample(self, **kwargs):""", """            self.sample_data['magnitude'] = dbmath.from_dB(y)
            self.sample_data['dbFS'] = y
        self.set_data_updated()
        return True
    def add_sample(self, **kwargs):"""),
])

patch('scan_objects/samplearray.py', [
    ("""    def smooth(self, window_size):
        x = self.magnitude
        w = np.hanning(window_size)""", """    def smooth(self, window_size):
        x = self.magnitude
        # V18.1 (audit 1.4): a window under 4 points is all zeros (NaN), a
        # window longer than the data gives a wrong size
        window_size = int(window_size)
        if window_size < 4 or window_size > x.size:
            return False
        w = np.hanning(window_size)"""),
    ("""        self.data['magnitude'] = m
        self.data['dbFS'] = dbmath.to_dB(m)
    def interpolate(self, spacing=0.025):""", """        self.data['magnitude'] = m
        self.data['dbFS'] = dbmath.to_dB(m)
        return True
    def interpolate(self, spacing=0.025):
        # not used by the window (the call is switched off in ui/pyside/scanner.py)"""),
    ("""        n_dec = len(str(spacing).split('.')[1])""", """        from decimal import Decimal
        n_dec = max(0, -Decimal(str(spacing)).normalize().as_tuple().exponent)   # V18.1 (audit 2.5)"""),
])

# ------------------------------------------------------------------ 2.1 sample setters
patch('scan_objects/sample.py', [
    ("""        ix = self.spectrum_index
        self.spectrum.sample_data['iq'][ix] = value""", """        ix = self.spectrum_index
        if ix is None:          # V18.1 (audit 2.1): array[None] = v writes every row
            return
        self.spectrum.sample_data['iq'][ix] = value"""),
    ("""        ix = self.spectrum_index
        self.spectrum.sample_data['magnitude'][ix] = value""", """        ix = self.spectrum_index
        if ix is None:          # V18.1 (audit 2.1)
            return
        self.spectrum.sample_data['magnitude'][ix] = value"""),
    ("""        ix = self.spectrum_index
        self.spectrum.sample_data['dbFS'][ix] = value""", """        ix = self.spectrum_index
        if ix is None:          # V18.1 (audit 2.1)
            return
        self.spectrum.sample_data['dbFS'][ix] = value"""),
])

# ------------------------------------------------------------------ 2.4 file names from the scan index
patch('utils/dbstore.py', [
    ("""    def _write_points(self, eid, data, sample_data, name=None, raw_dbFS=None):
        if not os.path.isdir(self.scans_dir):
            os.makedirs(self.scans_dir)
        name = name or self._scan_file_name(eid, data, sample_data)""",
     """    @staticmethod
    def _safe_name(name):
        \"\"\"V18.1 (audit 2.4): a points file name from scan_index.json must be
        a plain file name inside the scans folder (no ../, no other drive).\"\"\"
        if not name or not isinstance(name, str):
            return None
        bad_chars = any(c in name for c in ':/\\\\')
        if bad_chars or os.path.basename(name) != name or name in ('.', '..') or os.path.isabs(name):
            return None
        return name
    def _write_points(self, eid, data, sample_data, name=None, raw_dbFS=None):
        if not os.path.isdir(self.scans_dir):
            os.makedirs(self.scans_dir)
        name = self._safe_name(name) or self._scan_file_name(eid, data, sample_data)"""),
    ("""        name = rec.get('sample_data_file')
        if name:""", """        name = rec.get('sample_data_file')
        if name and self._safe_name(name) is None:
            import logging
            logging.getLogger(__name__).error(
                'scan %s: points file name %r is not a plain file name, not read', eid, name)
            name = None
        if name:"""),
])

# ------------------------------------------------------------------ 3.2 / 3.3 / 1.5 files
patch('file_handlers/exporters.py', [
    ("""    def export_to_file(cls, **kwargs):
        filename = kwargs.get('filename')
        ext = os.path.splitext(filename)[1].strip('.').lower()""", """    def export_to_file(cls, **kwargs):
        filename = kwargs.get('filename')
        if not filename:
            raise ValueError('Export: no file name given')          # V18.1 (audit 1.5)
        ext = os.path.splitext(str(filename))[1].strip('.').lower()"""),
    ("""        cls = find_exporter(cls)
        fh = cls(**kwargs)""", """        found = find_exporter(cls)
        if found is None:                                            # V18.1 (audit 3.2)
            raise ValueError('Export: format ".%s" is not supported - use .csv, .sdb2, .sdb3 or .npz' % ext)
        fh = found(**kwargs)"""),
])

patch('file_handlers/importers.py', [
    ("""    def import_file(cls, filename):
        ext = os.path.splitext(filename)[1].strip('.').lower()""", """    def import_file(cls, filename):
        if not filename:
            raise ValueError('Open: no file name given')
        ext = os.path.splitext(str(filename))[1].strip('.').lower()"""),
    ("""        cls = find_importer(BaseImporter)
        fh = cls(filename=filename)
        return fh()
    def __call__(self):
        self.file_data = self.load_file()
        self.parse_file_data()
        return self.spectrum""", """        found = find_importer(BaseImporter)
        if found is None:                                            # V18.1 (audit 3.2)
            raise ValueError('Open: format ".%s" is not supported - use .csv, .sdb2, .sdb3 or .npz' % ext)
        fh = found(filename=filename)
        return fh()
    def __call__(self):
        self.file_data = self.load_file()
        try:
            self.parse_file_data()
        except (KeyError, IndexError, TypeError, AttributeError, ZeroDivisionError,
                OverflowError) as exc:
            # V18.1 (audit 3.3): a damaged file gives one clear message
            raise ValueError('%s: the file is damaged or not a supported scan file (%s: %s)'
                             % (os.path.basename(str(self.filename)), type(exc).__name__, exc))
        return self.spectrum"""),
    ("""        width = int(header.get('BitWidth', 16)) // 8
        dtype = {1: '>i1', 2: '>i2', 4: '>i4'}[width]""", """        width = int(header.get('BitWidth', 16)) // 8
        if width not in (1, 2, 4):                                    # V18.1 (audit 3.3)
            raise ValueError('%s: BitWidth %r is not supported'
                             % (os.path.basename(self.filename), header.get('BitWidth')))
        dtype = {1: '>i1', 2: '>i2', 4: '>i4'}[width]"""),
    ("""        rec = sum(size for _, size in layout)
        nsweeps = len(body) // rec""", """        rec = sum(size for _, size in layout)
        if rec <= 0:
            raise ValueError('%s: empty sweep layout' % os.path.basename(self.filename))
        if len(body) % rec:
            logger.warning('%s: %d bytes at the end do not make a whole sweep, ignored',
                           os.path.basename(self.filename), len(body) % rec)
        nsweeps = len(body) // rec"""),
])

# ------------------------------------------------------------------ 1.6 device list
patch('ui/pyside/device_config.py', [
    [("""logger = logging.getLogger(__name__)""", """logger = logging.getLogger(__name__)"""),
     ("""import enum
from rtlsdr import RtlSdr

from PySide2""", """import enum
import logging
from rtlsdr import RtlSdr

logger = logging.getLogger(__name__)

from PySide2""")],
    ("""        sdr = RtlSdr(device_index)
        self._get_info_from_device(sdr)
        sdr.close()""", """        sdr = RtlSdr(device_index)
        try:                       # V18.1 (audit 1.6): always closed
            self._get_info_from_device(sdr)
        finally:
            sdr.close()"""),
    ("""        sdr = RtlSdr(device_serial=device_serial)
        self._get_info_from_device(sdr)
        sdr.close()""", """        sdr = RtlSdr(device_serial=device_serial)
        try:
            self._get_info_from_device(sdr)
        finally:
            sdr.close()"""),
[    ("""    def _on_update_devices(self):
        print('update_devices')
        device_serials = RtlSdr.get_device_serial_addresses()
        print(device_serials)
        for i, device_serial in enumerate(device_serials):
            if i in self._devices:
                continue
            device = self.add_device(i, device_serial)
            print(device)""", """    def _on_update_devices(self):
        \"\"\"V18.1 (audit 1.6): a dongle pulled out during the update, or busy in
        another program, is skipped with a message; dongles no longer
        plugged in leave the list.\"\"\"
        print('update_devices')
        try:
            device_serials = list(RtlSdr.get_device_serial_addresses())
        except Exception as exc:
            logger.warning('Could not list the RTL-SDR dongles: %s', exc)
            return
        print(device_serials)
        gone = [i for i, d in self._devices.items()
                if i >= len(device_serials) or d.device_serial != device_serials[i]]
        for i in gone:
            self._devices.pop(i, None)
            self._devices_by_index.pop(i, None)
        if gone:
            self._n_devices.emit()
        for i, device_serial in enumerate(device_serials):
            if i in self._devices:
                continue
            try:
                device = self.add_device(i, device_serial)
            except Exception as exc:
                self._devices_by_index.pop(i, None)
                logger.warning('Dongle %d (%s) can not be opened - pulled out, or busy in another '
                               'program: %s', i, device_serial, exc)
                continue
            print(device)"""),
    ("""    def _on_update_devices(self):
        device_serials = RtlSdr.get_device_serial_addresses()
        logger.debug(f'found sdr serial numbers: {device_serials}')
        for i, device_serial in enumerate(device_serials):
            if i in self._devices:
                continue
            device = self.add_device(i, device_serial)""", """    def _on_update_devices(self):
        \"\"\"V18.1 (audit 1.6): a dongle pulled out during the update, or busy in
        another program, is skipped with a message; dongles no longer
        plugged in leave the list.\"\"\"
        print('update_devices')
        try:
            device_serials = list(RtlSdr.get_device_serial_addresses())
        except Exception as exc:
            logger.warning('Could not list the RTL-SDR dongles: %s', exc)
            return
        print(device_serials)
        gone = [i for i, d in self._devices.items()
                if i >= len(device_serials) or d.device_serial != device_serials[i]]
        for i in gone:
            self._devices.pop(i, None)
            self._devices_by_index.pop(i, None)
        if gone:
            self._n_devices.emit()
        for i, device_serial in enumerate(device_serials):
            if i in self._devices:
                continue
            try:
                device = self.add_device(i, device_serial)
            except Exception as exc:
                self._devices_by_index.pop(i, None)
                logger.warning('Dongle %d (%s) can not be opened - pulled out, or busy in another '
                               'program: %s', i, device_serial, exc)
                continue
            logger.debug('added %s', device)"""),],
])

# ------------------------------------------------------------------ window: errors shown, state always reset
patch('ui/pyside/scanner.py', [
[    ("""    @Slot()
    def on_scanner_finished(self):
        self.scan_thread.stop()
        print('scan_thread stopped')
        if self.calibrating:
            self._finish_calibration()
        elif self.scanConfig.smoothingEnabled:
            self.smooth_scan()
        if self.scanConfig.scalingEnabled and not self.calibrating:
            self.scale_scan()
        self.calibrating = False
        self.scan_thread = None
        self.scanner = None
        self.running = False""", """    @Slot()
    def on_scanner_finished(self):
        # V18.1: whatever fails after the scan, the window leaves "scanning"
        try:
            if self.scan_thread is not None:
                self.scan_thread.stop()
            print('scan_thread stopped')
            if self.calibrating:
                self._finish_calibration()
            elif self.scanConfig.smoothingEnabled:
                self.smooth_scan()
            if self.scanConfig.scalingEnabled and not self.calibrating:
                self.scale_scan()
        except Exception as exc:
            logging.getLogger('wwb_scanner.ui').error('After the scan: %s', exc, exc_info=True)
        finally:
            self.calibrating = False
            self.scan_thread = None
            self.scanner = None
            self.running = False"""),
    ("""    @Slot()
    def on_scanner_finished(self):
        self.scan_thread.stop()
        logger.info('scan_thread stopped')
        if self.calibrating:
            self._finish_calibration()
        elif self.scanConfig.smoothingEnabled:
            self.smooth_scan()
        if self.scanConfig.scalingEnabled and not self.calibrating:
            self.scale_scan()
        self.calibrating = False
        self.scan_thread = None
        self.scanner = None
        self.running = False""", """    @Slot()
    def on_scanner_finished(self):
        # V18.1: whatever fails after the scan, the window leaves "scanning"
        try:
            if self.scan_thread is not None:
                self.scan_thread.stop()
            logger.info('scan_thread stopped')
            if self.calibrating:
                self._finish_calibration()
            elif self.scanConfig.smoothingEnabled:
                self.smooth_scan()
            if self.scanConfig.scalingEnabled and not self.calibrating:
                self.scale_scan()
        except Exception as exc:
            logging.getLogger('wwb_scanner.ui').error('After the scan: %s', exc, exc_info=True)
        finally:
            self.calibrating = False
            self.scan_thread = None
            self.scanner = None
            self.running = False"""),],
    ("""        N = int(self.spectrum.sample_data.size * self.scanConfig.smoothingFactor / 100.)
        self.spectrum.smooth(N)""", """        N = int(self.spectrum.sample_data.size * self.scanConfig.smoothingFactor / 100.)
        if not self.spectrum.smooth(N):
            # V18.1 (audit 1.4)
            logging.getLogger('wwb_scanner.status').warning(
                'Smoothing skipped: %d points is too short for a %d-point window', self.spectrum.sample_data.size, N)"""),
    ("""        conf = self.scanConfig
        self.spectrum.scale(conf.scalingMinDB, conf.scalingMaxDB)""", """        conf = self.scanConfig
        if not self.spectrum.scale(conf.scalingMinDB, conf.scalingMaxDB):
            # V18.1 (audit 1.3)
            logging.getLogger('wwb_scanner.status').warning('Scaling skipped: the scan is empty or flat')"""),
])

patch('ui/pyside/graph.py', [
    ("""        filename = uri.toLocalFile()
        spectrum = BaseImporter.import_file(filename)
        spectrum.set_data_updated()""", """        filename = uri.toLocalFile()
        try:
            spectrum = BaseImporter.import_file(filename)
        except Exception as exc:
            # V18.1: shown in the window (status line + dialog), not only the console
            logger.error('Could not open %s: %s', os.path.basename(filename), exc,
                         exc_info=not isinstance(exc, (ValueError, OSError)))
            return
        spectrum.set_data_updated()"""),
    ("""        print('export to %s' % filename, flush=True)
        self.spectrum.export_to_file(filename=filename, frequency_format=CSV_FREQUENCY_FORMAT)
        print('export done: %s' % filename, flush=True)""", """        print('export to %s' % filename, flush=True)
        try:
            self.spectrum.export_to_file(filename=filename, frequency_format=CSV_FREQUENCY_FORMAT)
        except Exception as exc:
            logger.error('Could not save %s: %s', os.path.basename(filename), exc,
                         exc_info=not isinstance(exc, (ValueError, OSError)))
            return
        print('export done: %s' % filename, flush=True)"""),
])

patch('ui/pyside/main.py', [
    ("""QML_PATH = get_resource_filename('qml')
""", """QML_PATH = get_resource_filename('qml')


def install_error_hooks():
    \"\"\"V18.1: an error nobody caught (in a button handler, a file dialog,
    a background thread) used to show only in the console, or nowhere.
    Now it goes to the status line, the error dialog and the log file.\"\"\"
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
"""),
    ("""    app = QApplication(argv)
    apply_style()""", """    app = QApplication(argv)
    install_error_hooks()
    apply_style()"""),
])

# ------------------------------------------------------------------ repo only (not in the pip install)
patch('ui/pyside/main.py', [
    ("""    fn = QtCore.QUrl(context.file)
    p = Path(fn.toLocalFile()).relative_to(QML_PATH)
    name = '.'.join(p.parts)""", """    fn = QtCore.QUrl(context.file)
    try:                         # V18.1 (audit 3.5): empty or qrc: path
        p = Path(fn.toLocalFile()).relative_to(QML_PATH)
        name = '.'.join(p.parts)
    except ValueError:
        name = 'qml'"""),
], optional=True)
patch('log_config.py', [
    ("""LOG_DIR = USER_HOME / '.config' / 'rtlsdr-wwb-scanner' / 'logs'
if not LOG_DIR.exists():
    LOG_DIR.mkdir(parents=True)
LOG_BASE_FILENAME""", """LOG_DIR = USER_HOME / '.config' / 'rtlsdr-wwb-scanner' / 'logs'
LOG_BASE_FILENAME"""),
    ("""def setup(use_console=True, use_file=False):
    handlers = []""", """def setup(use_console=True, use_file=False):
    handlers = []
    if use_file and not LOG_DIR.exists():     # V18.1 (audit 3.6): not at import time
        LOG_DIR.mkdir(parents=True)"""),
], optional=True)

print('V18.1 applied to', ROOT)
print('patched:', ', '.join(sorted(set(APPLIED))))
if SKIPPED:
    print('skipped (not in this tree):', '; '.join(SKIPPED))
