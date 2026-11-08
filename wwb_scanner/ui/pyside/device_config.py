import enum
import threading

from rtlsdr import RtlSdr

from PySide2 import QtCore, QtQml
from PySide2.QtCore import Signal, Property
import logging
logger = logging.getLogger(__name__)

from wwb_scanner.ui.pyside.utils import GenericQObject

# def get_devices():
#     serials = RtlSdr.get_device_serial_addresses()


class TUNER_TYPE(enum.Enum):
    UNKNOWN     = 0
    E4000       = 1
    FC0012      = 2
    FC0013      = 3
    FC2580      = 4
    R820T       = 5
    R828D       = 6


class DeviceInfo(GenericQObject):
    _n_text = Signal()
    _n_device_index = Signal()
    _n_device_serial = Signal()
    _n_tuner_type = Signal()
    _n_gains = Signal()
    def __init__(self, *args):
        self._device_index = None
        self._device_serial = None
        self._tuner_type = None
        self._gains = []
        super().__init__(*args)

    def _g_text(self): return str(self)
    text = Property(str, _g_text, notify=_n_text)

    def _g_device_index(self): return self._device_index
    def _s_device_index(self, value): self._generic_setter('_device_index', value)
    device_index = Property(int, _g_device_index, _s_device_index, notify=_n_device_index)

    def _g_device_serial(self): return self._device_serial
    def _s_device_serial(self, value): self._generic_setter('_device_serial', value)
    device_serial = Property(str, _g_device_serial, _s_device_serial, notify=_n_device_serial)

    def _g_tuner_type(self): return self._tuner_type
    def _s_tuner_type(self, value): self._generic_setter('_tuner_type', value)
    tuner_type = Property(str, _g_tuner_type, _s_tuner_type, notify=_n_tuner_type)

    def _g_gains(self): return self._gains
    def _s_gains(self, value): self._generic_setter('_gains', value)
    gains = Property('QVariantList', _g_gains, _s_gains, notify=_n_gains)

    def _get_info_from_device(self, sdr):
        tuner_type = sdr.get_tuner_type()
        self.tuner_type = TUNER_TYPE(tuner_type).name
        self.gains = [g / 10 for g in sdr.gain_values]
        self._n_text.emit()
    def get_info_from_device_index(self, device_index):
        self.device_index = device_index
        if self.device_serial is None:
            self.device_serial = RtlSdr.get_device_serial_addresses()[device_index]
        sdr = None
        try:
            sdr = RtlSdr(device_index)
            self._get_info_from_device(sdr)
        except Exception:
            logger.exception('could not read device %d', device_index)
        finally:
            if sdr is not None:
                sdr.close()
    def get_info_from_device_serial(self, device_serial):
        self.device_serial = device_serial
        if self.device_index is None:
            self.device_index = RtlSdr.get_device_index_by_serial(device_serial)
        sdr = None
        try:
            sdr = RtlSdr(device_serial=device_serial)
            self._get_info_from_device(sdr)
        except Exception:
            logger.exception('could not read device %s', device_serial)
        finally:
            if sdr is not None:
                sdr.close()
    def __repr__(self):
        return f'<{self.__class__}: {self}>'
    def __str__(self):
        return f'{self.tuner_type} - {self.device_index} ({self.device_serial})'

class DeviceInfoList(GenericQObject):
    _n_devices = Signal()
    update_devices = Signal()
    _device_ready = Signal(int, str, str, list)   # index, serial, tuner_type, gains

    def __init__(self, *args):
        self._devices = {}
        self._devices_by_index = {}
        self._devices_by_serial = {}
        self._devices_lock = threading.Lock()
        super().__init__(*args)
        self.update_devices.connect(self._on_update_devices)
        self._device_ready.connect(self._on_device_ready, QtCore.Qt.QueuedConnection)

    def _g_devices(self):
        d = self._devices
        return [d[key] for key in sorted(d.keys())]
    def _s_devices(self, value):
        changed = False
        for i, val in enumerate(value):
            if self._devices.get(i) != val:
                changed = True
                self._devices[i] = val
        if changed:
            self._n_devices.emit()
    devices = Property('QVariantList', _g_devices, _s_devices, notify=_n_devices)


    def _on_update_devices(self):
        # USB enumeration + opening each dongle is slow and can hang, so it
        # must not run on the UI thread
        threading.Thread(target=self._update_devices_bg, daemon=True).start()

    def _update_devices_bg(self):
        try:
            device_serials = RtlSdr.get_device_serial_addresses()
        except Exception:
            logger.exception('could not list SDR devices')
            return
        logger.debug('found sdr serial numbers: %s', device_serials)
        for i, device_serial in enumerate(device_serials):
            with self._devices_lock:
                if i in self._devices:
                    continue
            self._read_device_bg(i, device_serial)

    def _read_device_bg(self, device_index, device_serial):
        tuner_type = 'UNKNOWN'
        gains = []
        sdr = None
        try:
            sdr = RtlSdr(device_index)
            tuner_type = TUNER_TYPE(sdr.get_tuner_type()).name
            gains = [g / 10 for g in sdr.gain_values]
        except Exception:
            logger.exception('could not read device %d', device_index)
        finally:
            if sdr is not None:
                try:
                    sdr.close()
                except Exception:
                    pass
        self._device_ready.emit(device_index, device_serial, tuner_type, gains)

    def _on_device_ready(self, device_index, device_serial, tuner_type, gains):
        with self._devices_lock:
            if device_index in self._devices:
                return
            device = DeviceInfo()
            device.device_index = device_index
            device.device_serial = device_serial
            device.tuner_type = tuner_type
            device.gains = gains
            self._devices[device_index] = device
            self._devices_by_index[device_index] = device
            self._devices_by_serial[device_serial] = device
        self._n_devices.emit()

    def add_device(self, device_index, device_serial):
        # non-blocking: read the device info in the background
        if device_index not in self._devices:
            threading.Thread(target=self._read_device_bg,
                             args=(device_index, device_serial), daemon=True).start()

def register_qml_types():
    QtQml.qmlRegisterType(DeviceInfo, 'DeviceConfig', 1, 0, 'DeviceInfo')
    QtQml.qmlRegisterType(DeviceInfoList, 'DeviceConfig', 1, 0, 'DeviceInfoList')
