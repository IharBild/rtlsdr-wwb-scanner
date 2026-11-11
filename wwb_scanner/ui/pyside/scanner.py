import threading
import time

import numpy as np

from PySide2 import QtCore, QtQml, QtQuick
from PySide2.QtCore import Signal, Property, Slot
import logging
logger = logging.getLogger(__name__)

from wwb_scanner.scanner import config
from wwb_scanner.scanner import Scanner
from wwb_scanner.scanner.main import get_freq_resolution
from wwb_scanner.scan_objects import Spectrum

from wwb_scanner.ui.pyside.utils import GenericQObject, QObjectThread

# V20.8: settings of a fresh installation = the configuration the fork is
# tested with. Why these numbers:
#   2400 kHz / window 96 = one point per 25 kHz, the frequency grid of WWB;
#   overlap 0.65 -> tile step 1200 kHz x 0.65 = 780, rounded down to the grid
#     = 775 kHz (neighbouring tiles overlap, the filter edges are dropped);
#   2048 x 40 samples per tile (34 ms of signal);
#   gain 33.8 dB: at 0 dB the dongle is 30-37 dB less sensitive in 470-700 MHz
#     (default_gain_profile.json). "Suggest gain" picks the step for a range.
# The same values stand in qml/ScanConfig.qml and qml/DeviceConfigPopup.qml.
FACTORY = dict(sampleRate=2400., gain=33.8, windowSize=96, samplesPerSweep=2048,
               sweepsPerScan=40, sweepOverlapRatio=0.65)
# what a fresh installation had up to V20.7.3 (replaced once, only when all six
# values are still exactly these - see main.qml)
FACTORY_OLD = dict(sampleRate=2048., gain=0., windowSize=128, samplesPerSweep=8192,
                   sweepsPerScan=20, sweepOverlapRatio=0.5)


class ScanConfigData(GenericQObject):
    _n_startFreq = Signal()
    _n_endFreq = Signal()
    _n_samplesPerSweep = Signal()
    _n_sweepsPerScan = Signal()
    _n_sweepOverlapRatio = Signal()
    _n_windowType = Signal()
    _n_windowSize = Signal()
    _n_smoothingEnabled = Signal()
    _n_smoothingFactor = Signal()
    _n_scalingEnabled = Signal()
    _n_scalingMinDB = Signal()
    _n_scalingMaxDB = Signal()
    _n_cleanRipple = Signal()
    _n_cleanSpurs = Signal()
    _n_cleanFloor = Signal()
    _n_autoGain = Signal()
    _n_scanRanges = Signal()
    configUpdate = Signal()
    def __init__(self, *args):
        self._startFreq = 470.
        self._endFreq = 536.
        # V20.8: the settings every test and every measurement of this fork
        # was made with (see FACTORY above); before: 8192 x 20, 0.5, 128
        self._samplesPerSweep = FACTORY['samplesPerSweep']
        self._sweepsPerScan = FACTORY['sweepsPerScan']
        self._sweepOverlapRatio = FACTORY['sweepOverlapRatio']
        self._windowType = 'hann'
        self._windowSize = FACTORY['windowSize']
        self._smoothingEnabled = False
        self._smoothingFactor = 1.
        self._scalingEnabled = False
        self._scalingMinDB = -140.
        self._scalingMaxDB = -55.
        self._cleanRipple = True
        self._cleanSpurs = True
        self._cleanFloor = True
        self._autoGain = True
        self._scanRanges = ''
        super().__init__(*args)

    def _generic_property_changed(self, attr, old_value, new_value):
        self.configUpdate.emit()

    def _g_startFreq(self): return self._startFreq
    def _s_startFreq(self, value): self._generic_setter('_startFreq', value)
    startFreq = Property(float, _g_startFreq, _s_startFreq, notify=_n_startFreq)

    def _g_endFreq(self): return self._endFreq
    def _s_endFreq(self, value): self._generic_setter('_endFreq', value)
    endFreq = Property(float, _g_endFreq, _s_endFreq, notify=_n_endFreq)

    def _g_samplesPerSweep(self): return self._samplesPerSweep
    def _s_samplesPerSweep(self, value): self._generic_setter('_samplesPerSweep', value)
    samplesPerSweep = Property(int, _g_samplesPerSweep, _s_samplesPerSweep, notify=_n_samplesPerSweep)

    def _g_sweepsPerScan(self): return self._sweepsPerScan
    def _s_sweepsPerScan(self, value): self._generic_setter('_sweepsPerScan', value)
    sweepsPerScan = Property(int, _g_sweepsPerScan, _s_sweepsPerScan, notify=_n_sweepsPerScan)

    def _g_sweepOverlapRatio(self): return self._sweepOverlapRatio
    def _s_sweepOverlapRatio(self, value): self._generic_setter('_sweepOverlapRatio', value)
    sweepOverlapRatio = Property(float, _g_sweepOverlapRatio, _s_sweepOverlapRatio, notify=_n_sweepOverlapRatio)

    def _g_windowType(self): return self._windowType
    def _s_windowType(self, value): self._generic_setter('_windowType', value)
    windowType = Property(str, _g_windowType, _s_windowType, notify=_n_windowType)

    def _g_windowSize(self): return self._windowSize
    def _s_windowSize(self, value): self._generic_setter('_windowSize', value)
    windowSize = Property(int, _g_windowSize, _s_windowSize, notify=_n_windowSize)

    def _g_smoothingEnabled(self): return self._smoothingEnabled
    def _s_smoothingEnabled(self, value): self._generic_setter('_smoothingEnabled', value)
    smoothingEnabled = Property(bool, _g_smoothingEnabled, _s_smoothingEnabled, notify=_n_smoothingEnabled)

    def _g_smoothingFactor(self): return self._smoothingFactor
    def _s_smoothingFactor(self, value): self._generic_setter('_smoothingFactor', value)
    smoothingFactor = Property(float, _g_smoothingFactor, _s_smoothingFactor, notify=_n_smoothingFactor)

    def _g_scalingEnabled(self): return self._scalingEnabled
    def _s_scalingEnabled(self, value): self._generic_setter('_scalingEnabled', value)
    scalingEnabled = Property(bool, _g_scalingEnabled, _s_scalingEnabled, notify=_n_scalingEnabled)

    def _g_scalingMinDB(self): return self._scalingMinDB
    def _s_scalingMinDB(self, value): self._generic_setter('_scalingMinDB', value)
    scalingMinDB = Property(float, _g_scalingMinDB, _s_scalingMinDB, notify=_n_scalingMinDB)

    def _g_scalingMaxDB(self): return self._scalingMaxDB
    def _s_scalingMaxDB(self, value): self._generic_setter('_scalingMaxDB', value)
    scalingMaxDB = Property(float, _g_scalingMaxDB, _s_scalingMaxDB, notify=_n_scalingMaxDB)

    def _g_cleanRipple(self): return self._cleanRipple
    def _s_cleanRipple(self, value): self._generic_setter('_cleanRipple', value)
    cleanRipple = Property(bool, _g_cleanRipple, _s_cleanRipple, notify=_n_cleanRipple)

    def _g_cleanSpurs(self): return self._cleanSpurs
    def _s_cleanSpurs(self, value): self._generic_setter('_cleanSpurs', value)
    cleanSpurs = Property(bool, _g_cleanSpurs, _s_cleanSpurs, notify=_n_cleanSpurs)

    def _g_cleanFloor(self): return self._cleanFloor
    def _s_cleanFloor(self, value): self._generic_setter('_cleanFloor', value)
    cleanFloor = Property(bool, _g_cleanFloor, _s_cleanFloor, notify=_n_cleanFloor)

    def _g_autoGain(self): return self._autoGain
    def _s_autoGain(self, value): self._generic_setter('_autoGain', value)
    autoGain = Property(bool, _g_autoGain, _s_autoGain, notify=_n_autoGain)

    # V18: several ranges as JSON '[[470, 534], [823, 832]]'; '' = one range
    def _g_scanRanges(self): return self._scanRanges
    def _s_scanRanges(self, value): self._generic_setter('_scanRanges', value or '')
    scanRanges = Property(str, _g_scanRanges, _s_scanRanges, notify=_n_scanRanges)

CAL_OLD_DAYS = 30
READY_POLL_S = 0.1


def _wait_scanner_ready(scanner):
    """V20.8: wait until the scan runs OR has ended. A scan that fails before
    it starts (dongle missing or busy) never sets _running; waiting for that
    alone left a thread behind for ever and the program crashed on exit."""
    while not scanner._running.wait(READY_POLL_S):
        if scanner._stopped.is_set():
            return False
    return True
STEPS_WARN_DB = 6.     # a table this far off nominal: an anchor was probably compressed


def _short_date(text):
    """'2026-11-10 18:18:05' -> '10.11 18:18'"""
    t = str(text or '')
    if len(t) >= 16 and t[4] == '-' and t[7] == '-':
        return '%s.%s %s' % (t[8:10], t[5:7], t[11:16])
    return t[:16] or '?'


def _age_days(text):
    import datetime
    try:
        t = datetime.datetime.strptime(str(text)[:19], '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None
    return (datetime.datetime.now() - t).days


def _floor_info(cleanup, gain, sample_rate):
    if gain is None:
        return {'floorState': 'none', 'floorText': 'gain unknown', 'floorTip': ''}
    prof, why = cleanup.find_profile(gain, sample_rate)
    if prof is None and 'broken' in why:
        return {'floorState': 'warn', 'floorText': 'broken - calibrate again without antenna', 'floorTip': why}
    if prof is None:
        return {'floorState': 'none', 'floorText': 'none for %.1f dB - the floor is not levelled' % float(gain),
                'floorTip': 'No calibration for this gain (%s). Spurs and ripple are still removed; '
                            'the floor is levelled only with a calibration made at the same gain.' % why}
    m = prof.get('meta', {})
    created = m.get('created')
    ext = prof.get('external') or []
    parts = [_short_date(created), '%.1f dB' % float(m.get('gain', gain)), '%d spurs' % len(prof.get('spurs', []))]
    state = 'ok'
    age = _age_days(created)
    if ext:
        parts.append('%d outside' % len(ext))
        state = 'warn'
    if age is not None and age > CAL_OLD_DAYS:
        parts.append('%d days old' % age)
        state = 'warn'
    tip = cleanup.status_text(gain, sample_rate)
    if ext:
        tip += ' Outside signals mean the antenna was not fully off: calibrate again without antenna.'
    return {'floorState': state, 'floorText': ' \u00b7 '.join(parts), 'floorTip': tip}


def _steps_info(gain_cal, sample_rate):
    table = gain_cal.load_table(sample_rate)
    if table is None:
        return {'stepsState': 'none', 'stepsText': 'none - Overload uses the nominal steps',
                'stepsTip': gain_cal.status_text(sample_rate)}
    worst = table.get('worst') or {}
    err = float(worst.get('err', 0.) or 0.)
    parts = [_short_date(table.get('date')), '%d/%d steps' % (table.get('measured', 0), table.get('total', 0))]
    state = 'ok'
    if abs(err) > STEPS_WARN_DB:
        parts.append('%+.1f dB at %.0f MHz - calibrate again' % (err, worst.get('f_mhz', 0.)))
        state = 'warn'
    else:
        parts.append('\u00b1%.1f dB of nominal' % abs(err))
    tip = gain_cal.status_text(sample_rate)
    if state == 'warn':
        tip += (' So large a difference usually means a strong signal was compressed during the '
                'calibration; since V20.6.1 such readings are not used - run the calibration again.')
    return {'stepsState': state, 'stepsText': ' \u00b7 '.join(parts), 'stepsTip': tip}


class ScannerInterface(GenericQObject):
    _n_running = Signal()
    _n_scanConfig = Signal()
    _n_deviceInfo = Signal()
    _n_gain = Signal()
    _n_sampleRate = Signal()
    _n_spectrum = Signal()
    _n_progress = Signal()
    _n_scannerInitialized = Signal()
    _n_calibrating = Signal()
    scannerRunState = Signal(bool)
    calibrationChanged = Signal()
    def __init__(self, *args):
        self._running = False
        self._scanConfig = None
        self._deviceInfo = None
        self._gain = None
        self._sampleRate = None
        self._spectrum = None
        self._scannerInitialized = False
        self._progress = -1
        self._calibrating = False
        self._gain_calibrating = False      # V20.5
        super().__init__(*args)
        self.scanner = None
        self.scan_thread = None
        self.scan_init_thread = None

    @property
    def startFreq(self): return self.scanConfig.startFreq

    @property
    def endFreq(self): return self.scanConfig.endFreq

    def _g_running(self): return self._running
    def _s_running(self, value):
        if value == self._running:
            return
        self._running = value
        self._n_running.emit()
        self.scannerRunState.emit(value)
    running = Property(bool, _g_running, _s_running, notify='_n_running')

    def _g_scannerInitialized(self): return self._scannerInitialized
    def _s_scannerInitialized(self, value): self._generic_setter('_scannerInitialized', value)
    scannerInitialized = Property(bool, _g_scannerInitialized, _s_scannerInitialized, notify=_n_scannerInitialized)

    def _g_scanConfig(self): return self._scanConfig
    def _s_scanConfig(self, value): self._generic_setter('_scanConfig', value)
    scanConfig = Property(ScanConfigData, _g_scanConfig, _s_scanConfig, notify=_n_scanConfig)

    def _g_deviceInfo(self): return self._deviceInfo
    def _s_deviceInfo(self, value): self._generic_setter('_deviceInfo', value)
    deviceInfo = Property(QtCore.QObject, _g_deviceInfo, _s_deviceInfo, notify=_n_deviceInfo)

    def _g_gain(self): return self._gain
    def _s_gain(self, value): self._generic_setter('_gain', value)
    gain = Property(float, _g_gain, _s_gain, notify=_n_gain)

    def _g_sampleRate(self): return self._sampleRate
    def _s_sampleRate(self, value): self._generic_setter('_sampleRate', value)
    sampleRate = Property(float, _g_sampleRate, _s_sampleRate, notify=_n_sampleRate)

    def _g_spectrum(self): return self._spectrum
    def _s_spectrum(self, value): self._generic_setter('_spectrum', value)
    spectrum = Property(object, _g_spectrum, _s_spectrum, notify=_n_spectrum)

    def _g_progress(self): return self._progress
    def _s_progress(self, value): self._generic_setter('_progress', value)
    progress = Property(float, _g_progress, _s_progress, notify=_n_progress)

    def _g_calibrating(self): return self._calibrating
    def _s_calibrating(self, value): self._generic_setter('_calibrating', value)
    calibrating = Property(bool, _g_calibrating, _s_calibrating, notify=_n_calibrating)

    def start_problem(self):
        """V20.8: why the scan can not start, as a text for the window, or ''.
        Before this the window showed a Python error (no dongle: 'NoneType'
        object has no attribute 'device_serial'; Start above End: a numpy
        'zero-size array' message)."""
        if self.deviceInfo is None:
            return ('No RTL-SDR dongle found. Plug the dongle in (close other SDR programs) '
                    'and press Start again.')
        if self.scanConfig is None:
            return 'The scan settings are not ready yet.'
        try:
            rate = float(self.sampleRate)
        except (TypeError, ValueError):
            rate = float('nan')
        if not (rate == rate and rate > 0):
            return 'The sample rate is not set: enter it in Device Settings (for example 2400).'
        try:
            float(self.gain)
        except (TypeError, ValueError):
            return 'The gain is not set: choose it in Device Settings.'
        if self.calibrating or self._gain_calibrating or self.scan_segments():
            return ''       # these scans bring their own ranges
        try:
            lo, hi = float(self.startFreq), float(self.endFreq)
        except (TypeError, ValueError):
            lo = hi = float('nan')
        if not (lo == lo and hi == hi) or abs(lo) == float('inf') or abs(hi) == float('inf'):
            return 'Start Freq and End Freq must be numbers (MHz).'
        if hi <= lo:
            return 'End Freq (%g MHz) must be above Start Freq (%g MHz).' % (hi, lo)
        return ''

    def _begin(self, calibrating=False, gain_calibrating=False):
        """V20.8: every scan starts here. If it can not start, the reason is
        shown and nothing is left behind - above all not the calibration flags:
        'Calibrate' pressed without a dongle used to turn the NEXT ordinary
        Start into a calibration scan."""
        if self.running:
            return False
        self.calibrating = bool(calibrating)
        self._gain_calibrating = bool(gain_calibrating)
        started = False
        try:
            why = self.start_problem()
            if why:
                logging.getLogger('wwb_scanner.ui').error(why)
            else:
                self._start()
                started = True
        except Exception as exc:
            logging.getLogger('wwb_scanner.ui').error('The scan did not start: %s', exc, exc_info=True)
        finally:
            if not started:
                self.calibrating = False
                self._gain_calibrating = False
                self.scan_thread = None
                self.scanner = None
                self.progress = -1
                self.running = False
        return started

    def build_scan_config(self):
        conf = config.ScanConfig()
        conf.scan_range = [self.startFreq, self.endFreq]
        conf.device.serial_number = self.deviceInfo.device_serial
        conf.device.gain = self.gain
        conf.sampling.sample_rate = self.sampleRate * 1e3
        conf.sampling.samples_per_sweep = self.scanConfig.samplesPerSweep
        conf.sampling.sweeps_per_scan = self.scanConfig.sweepsPerScan
        conf.sampling.sweep_overlap_ratio = self.scanConfig.sweepOverlapRatio
        conf.sampling.window_size = self.scanConfig.windowSize
        conf.sampling.window_type = self.scanConfig.windowType
        sc = self.scanConfig
        conf.sampling.auto_gain = bool(getattr(sc, 'autoGain', False))
        conf.processing.clean_ripple = bool(getattr(sc, 'cleanRipple', False))
        conf.processing.clean_spurs = bool(getattr(sc, 'cleanSpurs', False))
        conf.processing.clean_floor = bool(getattr(sc, 'cleanFloor', False))
        segs = self.scan_segments()
        if len(segs) > 1:
            conf.scan_ranges = [list(s) for s in segs]
            conf.scan_range = [segs[0][0], segs[-1][1]]
        if self._gain_calibrating:
            # V20.5: survey tiles for the gain step calibration
            from wwb_scanner.scanner import gain_cal
            conf.scan_range = list(gain_cal.GAIN_CAL_RANGE_MHZ)
            conf.scan_ranges = gain_cal.survey_ranges()
            conf.gain_calibration = True
            conf.device.gain = gain_cal.SURVEY_GAIN_DB
            conf.sampling.auto_gain = False
            conf.processing.clean_ripple = False
            conf.processing.clean_spurs = False
            conf.processing.clean_floor = False
        if self.calibrating:
            # calibration: whole range, values exactly as measured
            from wwb_scanner.scanner import cleanup
            conf.scan_range = list(cleanup.CALIBRATION_RANGE_MHZ)
            conf.scan_ranges = None
            conf.sampling.auto_gain = False
            conf.processing.clean_ripple = False
            conf.processing.clean_spurs = False
            conf.processing.clean_floor = False
        return conf

    def scan_segments(self):
        """V18: [(lo, hi)] of the next scan (several when Bands chose them)."""
        from wwb_scanner import bands
        rs = bands.parse_ranges(getattr(self.scanConfig, 'scanRanges', ''))
        segs = bands.scan_segments(rs)[0] if rs else []
        return segs if len(segs) > 1 else []

    @Slot(result=str)
    def calibrationStatus(self):
        """One line about the dongle calibration for the current gain."""
        from wwb_scanner.scanner import cleanup, gain_cal
        try:
            text = cleanup.status_text(self.gain, (self.sampleRate or 0) * 1e3)
        except Exception as exc:
            text = 'calibration: %s' % exc
        try:
            text += '\n' + gain_cal.status_text((self.sampleRate or 0) * 1e3)    # V20.5
        except Exception as exc:
            text += '\ngain steps: %s' % exc
        return text

    @Slot(result='QVariantMap')
    def calibrationInfo(self):
        """V20.7: the two calibrations for the Scan settings, short:
        {floor,steps}{State: ok|warn|none, Text, Tip}."""
        from wwb_scanner.scanner import cleanup, gain_cal
        sr = (self.sampleRate or 0) * 1e3
        out = {}
        try:
            out.update(_floor_info(cleanup, self.gain, sr))
        except Exception as exc:
            out.update(floorState='warn', floorText='can not be read: %s' % exc, floorTip='')
        try:
            out.update(_steps_info(gain_cal, sr))
        except Exception as exc:
            out.update(stepsState='warn', stepsText='can not be read: %s' % exc, stepsTip='')
        return out

    @Slot(bool, result='QVariantList')
    def suggestGain(self, auto_gain):
        """V20.6: [gain or -1, text] for the next scan from the gain profile."""
        from wwb_scanner.scanner import gain_profile
        try:
            ranges = self.scan_segments() or [(self.startFreq, self.endFreq)]
            ranges = [(float(lo), float(hi)) for lo, hi in ranges]
            gains = list(getattr(self.deviceInfo, 'gains', None) or []) or None
            g, text = gain_profile.suggest(ranges, bool(auto_gain), gains)
        except Exception as exc:
            logger.exception('suggestGain')
            return [-1., 'Suggest gain: %s' % exc]
        return [-1. if g is None else float(g), text]

    @Slot()
    def calibrateGains(self):
        """V20.5: measure the real gain steps on signals of the air (antenna on)."""
        self._begin(gain_calibrating=True)

    @Slot()
    def calibrate(self):
        """Scan 31-1766 MHz WITHOUT antenna and save the dongle profile."""
        self._begin(calibrating=True)

    @Slot(result='QVariantMap')
    def factorySettings(self):
        """V20.8: {'now': FACTORY, 'old': FACTORY_OLD} for the one-time
        replacement of untouched old settings (main.qml)."""
        return {'now': dict(FACTORY), 'old': dict(FACTORY_OLD)}

    @Slot(str)
    def statusNote(self, text):
        """V20.8: a line for the status bar and the log from QML."""
        logging.getLogger('wwb_scanner.status').info('%s', text)

    @Slot(int, float, result=float)
    def getFreqResolution(self, nfft, fs):
        return get_freq_resolution(nfft, fs*1e3) / 1e6

    @Slot()
    def start(self):
        self._begin()

    @Slot()
    def stop(self):
        if not self.running:
            return
        self._stop()

    def _start(self):
        self.scannerInitialized = False
        conf = self.build_scan_config()
        self.scanner = Scanner(config=conf)
        self.spectrum = self.scanner.spectrum
        if self.calibrating:
            self.spectrum.name = 'Calibration without antenna, gain %s dB' % self.gain
        elif self._gain_calibrating:
            from wwb_scanner.scanner import gain_cal
            self.spectrum.name = 'Gain step calibration (survey, gain %s dB)' % gain_cal.SURVEY_GAIN_DB
        else:
            # V20.7: the start time in the name (rename in the scan list)
            segs = self.scan_segments()
            when = time.strftime('%d.%m %H:%M')
            if segs:
                self.spectrum.name = 'Bands %s MHz, %s' % (', '.join('%g-%g' % s for s in segs), when)
            else:
                self.spectrum.name = '%g-%g MHz, %s' % (float(self.startFreq), float(self.endFreq), when)
        self.progress = 0.
        self.running = True
        self.scan_thread = ScanThread(target=self.scanner.run_scan)
        self.scanner.on_progress = self.scan_thread._on_scanner_progress
        self.scan_thread.complete.connect(self.on_scanner_finished)
        self.scan_thread.scannerProgress.connect(self.on_scanner_progress)
        # V20.8: also ends when the scan never starts (see _wait_scanner_ready)
        scanner = self.scanner
        self.scan_init_thread = QObjectThread(target=lambda: _wait_scanner_ready(scanner))
        self.scan_init_thread.complete.connect(self.on_scanner_ready)
        self.scan_thread.start()
        self.scan_init_thread.start()

    def _stop(self):
        if self.scanner is not None:
            if self.scanner._running.is_set():
                self.scanner.stop_scan()

    @Slot()
    def on_scanner_progress(self, value):
        self.progress = value

    @Slot()
    def on_scanner_finished(self):
        # V18.1: whatever fails after the scan, the window leaves "scanning"
        try:
            if self.scan_thread is not None:
                self.scan_thread.stop()
            logger.info('scan_thread stopped')
            if self.calibrating:
                self._finish_calibration()
            elif self._gain_calibrating:
                self.calibrationChanged.emit()      # V20.5: status set by the scanner
            elif self.scanConfig.smoothingEnabled:
                self.smooth_scan()
            if self.scanConfig.scalingEnabled and not self.calibrating:
                self.scale_scan()
        except Exception as exc:
            logging.getLogger('wwb_scanner.ui').error('After the scan: %s', exc, exc_info=True)
        finally:
            self.calibrating = False
            self._gain_calibrating = False
            self.scan_thread = None
            self.scanner = None
            self.running = False
            # V20.8: last, and never in the way of leaving the "scanning" state
            try:
                ScannerInterface._stop_init_thread(self)
            except Exception:
                logger.exception('scan_init_thread')

    def _stop_init_thread(self):
        """V20.8: the scan is over, so the waiting thread ends within
        READY_POLL_S; no thread may outlive its scan."""
        t = getattr(self, 'scan_init_thread', None)
        self.scan_init_thread = None
        if t is None:
            return
        try:
            t.stop()
        except Exception:
            logger.exception('scan_init_thread.stop')

    def _finish_calibration(self):
        from wwb_scanner.scanner import cleanup
        status = logging.getLogger('wwb_scanner.status')
        scanner = self.scanner
        try:
            if scanner is None or getattr(scanner, 'error', None) is not None or \
                    getattr(scanner.sample_collection, 'aborted', False):
                status.warning('Calibration not saved: the scan did not finish')
                return
            sd = scanner.spectrum.sample_data
            meta = dict(gain=float(scanner.gain), sample_rate=float(scanner.sample_rate),
                        step_mhz=float(scanner.step_size),
                        window_size=int(scanner.window_size or 0),
                        window_type=str(scanner.sampling_config.window_type))
            try:
                meta['serial'] = self.deviceInfo.device_serial
            except Exception:
                pass
            try:
                sets = [x for x in scanner.sample_collection.sample_sets.values()
                        if not getattr(x, 'neighbour', False)]
                clipped = (sum(1 for x in sets if getattr(x, 'clip_ratio', 0.) >= 1e-3) / float(len(sets))
                           if sets else None)
            except Exception:
                clipped = None
            ok, text = cleanup.calibrate(
                np.asarray(sd['frequency'], dtype=float), np.asarray(sd['dbFS'], dtype=float),
                float(scanner.config.scan_range[0]), scanner.step_size, meta, clipped_share=clipped)
            (status.info if ok else status.warning)(text)
        except Exception as exc:
            logging.getLogger('wwb_scanner').error('Calibration failed: %s', exc, exc_info=True)
        finally:
            self.calibrationChanged.emit()

    @Slot()
    def smooth_scan(self):
        logger.info('Smoothing scan')
        N = int(self.spectrum.sample_data.size * self.scanConfig.smoothingFactor / 100.)
        if not self.spectrum.smooth(N):
            # V18.1 (audit 1.4)
            logging.getLogger('wwb_scanner.status').warning(
                'Smoothing skipped: %d points is too short for a %d-point window', self.spectrum.sample_data.size, N)
        # TODO: figure out why interpolate stopped working
        # self.spectrum.interpolate()

    @Slot()
    def scale_scan(self):
        logger.info('Scaling scan')
        conf = self.scanConfig
        if not self.spectrum.scale(conf.scalingMinDB, conf.scalingMaxDB):
            # V18.1 (audit 1.3)
            logging.getLogger('wwb_scanner.status').warning('Scaling skipped: the scan is empty or flat')

    @Slot()
    def on_scanner_ready(self):
        self.scannerInitialized = True
        self._stop_init_thread()        # V20.8: may already be stopped by the end of the scan


class ScanThread(QObjectThread):
    scannerProgress = Signal(float)
    def _on_scanner_progress(self, value):
        self.scannerProgress.emit(value)

def register_qml_types():
    QtQml.qmlRegisterType(ScanConfigData, 'ScanTools', 1, 0, 'ScanConfigData')
    QtQml.qmlRegisterType(ScannerInterface, 'ScanTools', 1, 0, 'ScannerInterface')
