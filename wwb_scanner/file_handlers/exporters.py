import os
import io
import datetime
import struct
import time
import uuid
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom

import logging

import numpy as np

logger = logging.getLogger(__name__)

EPOCH = datetime.datetime(1970, 1, 1)

# Value used for points without data (same as no_data_value in the sdb2 header)
NO_DATA_DB = -140.
# dbmath floors zero power at about -3077 dB; anything below this is "no data"
IMPOSSIBLE_DB = -1000.

# Shure WWB refuses files outside 30-2000 MHz (reported by the user)
WWB_MIN_KHZ = 30000
WWB_MAX_KHZ = 2000000
WWB_STEP_KHZ = 25
# shown by WWB as the scanner model (WWB itself writes '' for CSV imports)
SCANNER_MODEL = 'RTL-SDR (rtlsdr-wwb-scanner)'


def wwb_date(dt):
    """'Wed Nov 4 2026' - the way WWB 7.9 writes the date (no leading zero)."""
    return '%s %d %s' % (dt.strftime('%a %b'), dt.day, dt.strftime('%Y'))


def _epoch_ms(local_dt):
    return int(round(time.mktime(local_dt.timetuple()) * 1000 + local_dt.microsecond / 1000.))


def wwb_grid(spectrum, step=WWB_STEP_KHZ, lo=WWB_MIN_KHZ, hi=WWB_MAX_KHZ):
    """Scan values on a whole-kHz grid (``step`` kHz) inside ``lo``..``hi`` kHz,
    as WWB expects. Returns (grid_khz int64 array, dB array). The spectrum is
    not changed."""
    freqs_khz = np.rint(np.asarray(spectrum.sample_data['frequency'], dtype=np.float64) * 1000.)
    dB = clean_dB(spectrum.sample_data['dbFS'])
    if freqs_khz.size == 0:
        return np.zeros(0, np.int64), np.zeros(0)
    order = np.argsort(freqs_khz, kind='stable')
    freqs_khz, dB = freqs_khz[order], dB[order]
    first = int(np.ceil(max(freqs_khz[0], lo) / step)) * step
    last = int(np.floor(min(freqs_khz[-1], hi) / step)) * step
    if freqs_khz[0] < lo or freqs_khz[-1] > hi:
        logger.warning('WWB accepts only %d-%d MHz: export clipped to %.3f-%.3f MHz',
                       lo // 1000, hi // 1000, first / 1000., last / 1000.)
    if last < first:
        raise ValueError('scan %.3f-%.3f MHz is outside the %d-%d MHz range WWB accepts'
                         % (freqs_khz[0] / 1000., freqs_khz[-1] / 1000., lo // 1000, hi // 1000))
    grid = np.arange(first, last + 1, step, dtype=np.int64)
    values = np.around(np.interp(grid, freqs_khz, dB), decimals=1)
    return grid, values


def wwb_points(spectrum, lo=WWB_MIN_KHZ, hi=WWB_MAX_KHZ):
    """Scan points for sdb2: the scan's own grid when it is regular (sdb2
    lists every frequency, other steps such as 50 kHz are fine), else the
    25 kHz grid. Always inside the range WWB accepts. Returns
    (freqs_khz int64, dB, step_khz)."""
    freqs_khz = np.rint(np.asarray(spectrum.sample_data['frequency'], dtype=np.float64) * 1000.)
    dB = clean_dB(np.around(spectrum.sample_data['dbFS'], decimals=1))
    order = np.argsort(freqs_khz, kind='stable')
    freqs_khz, dB = freqs_khz[order], dB[order]
    inside = (freqs_khz >= lo) & (freqs_khz <= hi)
    if freqs_khz.size and not inside.all():
        logger.warning('WWB accepts only %d-%d MHz: export clipped', lo // 1000, hi // 1000)
    freqs_khz, dB = freqs_khz[inside], dB[inside]
    if freqs_khz.size == 0:
        raise ValueError('scan has no points inside the %d-%d MHz range WWB accepts'
                         % (lo // 1000, hi // 1000))
    steps = np.unique(np.diff(freqs_khz))
    if steps.size == 1 and steps[0] > 0:
        return freqs_khz.astype(np.int64), dB, float(steps[0])
    grid, values = wwb_grid(spectrum, lo=lo, hi=hi)
    return grid, values, float(WWB_STEP_KHZ)


def clean_dB(dB, floor=NO_DATA_DB):
    """Return a copy of ``dB`` where nan, inf and the "no signal at all"
    values (below ``IMPOSSIBLE_DB``) are replaced by ``floor``.
    The spectrum itself is not changed and real values are left as they are.
    """
    dB = np.array(dB, dtype=np.float64)
    dB[~np.isfinite(dB) | (dB < IMPOSSIBLE_DB)] = floor
    return dB

class BaseExporter(object):
    def __init__(self, **kwargs):
        self.spectrum = kwargs.get('spectrum')
        self.filename = kwargs.get('filename')
    def __call__(self):
        self.build_data()
        self.write_file()
    @classmethod
    def export_to_file(cls, **kwargs):
        filename = kwargs.get('filename')
        ext = os.path.splitext(filename)[1].strip('.').lower()
        def find_exporter(_cls):
            if getattr(_cls, '_extension', None) == ext:
                return _cls
            for _subcls in _cls.__subclasses__():
                r = find_exporter(_subcls)
                if r is not None:
                    return r
            return None
        cls = find_exporter(cls)
        fh = cls(**kwargs)
        fh()
        return fh
    @property
    def filename(self):
        return getattr(self, '_filename', None)
    @filename.setter
    def filename(self, value):
        if value is None:
            return
        self.set_filename(value)
    def set_filename(self, value):
        if value == self.filename:
            return
        self._filename = value
    def build_data(self):
        raise NotImplementedError('Method must be implemented by subclasses')
    def write_file(self):
        s = self.build_data()
        with open(self.filename, 'w') as f:
            f.write(s)

class NumpyExporter(BaseExporter):
    _extension = 'npz'
    def build_data(self):
        pass
    def write_file(self):
        np.savez(self.filename, sample_data=self.spectrum.sample_data)

class CSVExporter(BaseExporter):
    """Plain two column CSV.

    ``frequency_format='khz'`` writes the same layout as Shure WWB:
    frequency in whole kHz, level with one decimal, 25 kHz grid,
    ``\\n`` line endings and no newline after the last line.
    Any other value (default) keeps the old format: MHz, ``\\r\\n``.
    """
    _extension = 'csv'
    newline_chars = '\r\n'
    delimiter_char = ','
    khz_newline_chars = '\n'
    khz_step = 25
    def __init__(self, **kwargs):
        super(CSVExporter, self).__init__(**kwargs)
        self.frequency_format = kwargs.get('frequency_format')
    def set_filename(self, value):
        if os.path.splitext(value)[1] == '.CSV':
            value = '.'.join([os.path.splitext(value)[0], 'csv'])
        super(CSVExporter, self).set_filename(value)
    def write_file(self):
        s = self.build_data()
        # newline='' stops Python from translating '\r\n' again on Windows
        # (which produced '\r\r\n')
        with open(self.filename, 'w', newline='') as f:
            f.write(s)
    @property
    def is_khz(self):
        fmt = self.frequency_format
        return fmt is not None and str(fmt).lower() == 'khz'
    def build_khz_data(self):
        """Resample onto a whole-kHz grid (step ``khz_step``) like WWB does,
        only inside 30-2000 MHz (WWB refuses anything else)."""
        if self.spectrum.sample_data['frequency'].size == 0:
            return ''
        grid, values = wwb_grid(self.spectrum, step=self.khz_step)
        lines = ['%d%s%.1f' % (f, self.delimiter_char, v) for f, v in zip(grid, values)]
        return self.khz_newline_chars.join(lines)
    def build_data(self):
        if self.is_khz:
            return self.build_khz_data()
        newline_chars = self.newline_chars
        delim = self.delimiter_char
        lines = []
        freqs = np.around(self.spectrum.sample_data['frequency'], decimals=3)
        dB = clean_dB(np.around(self.spectrum.sample_data['dbFS'], decimals=1))
        for f, v in zip(freqs, dB):
            lines.append(delim.join([str(f), str(v)]))
        return newline_chars.join(lines)

class BaseWWBExporter(BaseExporter):
    def __init__(self, **kwargs):
        super(BaseWWBExporter, self).__init__(**kwargs)
        # dt: local time of the export (date/time attributes, as WWB writes
        # them); date_time is always the UTC epoch in ms. (Before V12 the
        # date/time attributes were UTC, one hour off in Warsaw.)
        self.dt = kwargs.get('dt') or datetime.datetime.now()
        self.epoch_ms = kwargs.get('epoch_ms')
    def set_filename(self, value):
        ext = self._extension
        if os.path.splitext(value)[1].lower() != '.%s' % (ext):
            value = '.'.join([os.path.splitext(value)[0], ext])
        super(BaseWWBExporter, self).set_filename(value)
    def build_attribs(self):
        dt = self.dt
        spectrum = self.spectrum
        d = dict(
            scan_data_source=dict(
                ver='0.0.0.1',
                id='{%s}' % (uuid.uuid4()),
                model=SCANNER_MODEL,
                name=os.path.abspath(self.filename),
                device_id='00000000-0000-0000-0000-000000000000',
                device_type_id='00000000-0000-0000-0000-000000000000',
                band='',
                mode='',
                date=wwb_date(dt),
                time=dt.strftime('%H:%M:%S.') + '%03d' % (dt.microsecond // 1000),
                color=spectrum.color.to_hex(),
            ),
            data_sets=dict(
                count='1',
                no_data_value='-140',
            ),
        )
        # The scan's own points, clipped to what WWB accepts. (This used to
        # smooth and re-interpolate the scan itself when step_size was
        # unknown, i.e. exporting sdb2 changed the data shown in the program.)
        grid, values, step = wwb_points(spectrum)
        self.grid = grid, values
        d['data_set'] = dict(
            index='0',
            freq_units='KHz',
            ampl_units='dBm',
            start_freq='%.1f' % grid[0],
            stop_freq='%.1f' % grid[-1],
            step_freq='%.3f' % (step,),
            res_bandwidth='TODO',
            scale_factor='1',
            date=d['scan_data_source']['date'],
            time=d['scan_data_source']['time'],
            date_time=str(int(self.epoch_ms if self.epoch_ms is not None else _epoch_ms(dt))),
        )
        return d
    def build_data(self):
        attribs = self.attribs = self.build_attribs()
        root = self.root = ET.Element('scan_data_source', attribs['scan_data_source'])
        ET.SubElement(root, 'data_sets', attribs['data_sets'])
        tree = self.tree = ET.ElementTree(root)
        return tree
    def finish(self, root):
        # WWB writes an (empty) markers element after data_sets
        if root.find('markers') is None:
            ET.SubElement(root, 'markers')
    def write_file(self):
        tree = self.build_data()
        fd = io.BytesIO()
        tree.write(fd, encoding='UTF-8', xml_declaration=True)
        doc = minidom.parseString(fd.getvalue())
        fd.close()
        s = doc.toprettyxml(encoding='UTF-8')
        if isinstance(s, bytes):
            s = s.decode('UTF-8')
        with open(self.filename, 'w') as f:
            f.write(s)


class WWBLegacyExporter(BaseWWBExporter):
    _extension = 'sbd'
    def build_data(self):
        tree = super(WWBLegacyExporter, self).build_data()
        root = tree.getroot()
        spectrum = self.spectrum
        attribs = self.attribs
        data_sets = root.find('data_sets')
        data_set = ET.SubElement(data_sets, 'data_set', attribs['data_set'])
        grid, dB = self.grid
        for val in dB:
            v = ET.SubElement(data_set, 'v')
            v.text = str(val)
        return tree

class WWBExporter(BaseWWBExporter):
    _extension = 'sdb2'
    def build_data(self):
        tree = super(WWBExporter, self).build_data()
        root = tree.getroot()
        spectrum = self.spectrum
        data_sets = root.find('data_sets')
        freq_set = ET.SubElement(data_sets, 'freq_set')
        data_set = ET.SubElement(data_sets, 'data_set', self.attribs['data_set'])
        freqs, dB = self.grid
        for freq, val in zip(freqs, dB):
            f = ET.SubElement(freq_set, 'f')
            f.text = '%d' % freq
            v = ET.SubElement(data_set, 'v')
            v.text = str(val)
        self.finish(root)
        return tree


# ---------------------------------------------------------------- sdb3 (WWB 7)
#
# Layout taken from a file WWB 7.9.0.104 wrote itself (our sdb2 converted in
# WWB, 04.11.2026), reproduced byte for byte by this writer:
#   "//@ShureScan\n" + JSON header (tabs, "key" : value, keys sorted)
#   "\n@Binary:" + one sweep record per sweep:
#       b"@Swp", id (uint32 BE, from 1), timestamp (uint32 BE, 0),
#       one int16 BE per point (dB / ScaleFactor... WWB writes whole dB with
#       "Scale Factor" 1, rounded half away from zero), CRC-16/ARC (BE) of
#       the record from "@Swp" to the last value
#   "@Extended:\n" + JSON + "\n@End" + uint32 BE sweeps + uint32 BE curves
# WWB itself rounds to whole dB when it converts sdb2 to sdb3.

def crc16_arc(data):
    crc = 0
    for b in bytearray(data):
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _crc16_arc_np(data):
    """Same as crc16_arc, table driven (70 000 points in ~50 ms)."""
    table = _crc16_arc_np.table
    crc = 0
    for b in bytearray(data):
        crc = (crc >> 8) ^ table[(crc ^ b) & 0xFF]
    return crc


def _make_table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
        t.append(c)
    return t


_crc16_arc_np.table = _make_table()


def _json_wwb(obj, indent=0):
    """JSON the way WWB writes it: tabs, '"key" : value', keys sorted,
    arrays of scalars on one line ('[ "a", "b" ]')."""
    tab = '\t' * indent
    if isinstance(obj, dict):
        items = []
        for k in sorted(obj):
            v = obj[k]
            if isinstance(v, (dict, list)) and not _scalar_list(v):
                items.append('%s\t"%s" : \n%s\t%s' % (tab, k, tab, _json_wwb(v, indent + 1)))
            else:
                items.append('%s\t"%s" : %s' % (tab, k, _json_wwb(v, indent + 1)))
        return '{\n' + ',\n'.join(items) + '\n' + tab + '}'
    if isinstance(obj, list):
        if _scalar_list(obj):
            return '[ ' + ', '.join(_json_wwb(x) for x in obj) + ' ]'
        return '[\n' + ',\n'.join(tab + '\t' + _json_wwb(x, indent + 1) for x in obj) + '\n' + tab + ']'
    if obj is True:
        return 'true'
    if obj is False:
        return 'false'
    if isinstance(obj, str):
        return '"%s"' % obj.replace('\\', '\\\\').replace('"', '\\"')
    return '%d' % obj if isinstance(obj, int) else repr(obj)


def _scalar_list(v):
    return isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v)


def sdb3_bytes(freqs_khz, dB, title, color='#38ad6b', dt=None, scale_factor=1,
               model=None, creator='rtlsdr-wwb-scanner', curve_name='Default curve'):
    """Whole sdb3 file for one sweep on a regular grid (kHz)."""
    freqs_khz = np.asarray(freqs_khz, dtype=np.int64)
    steps = np.unique(np.diff(freqs_khz))
    if steps.size != 1 or steps[0] <= 0:
        raise ValueError('sdb3 needs a regular frequency grid')
    dt = dt or datetime.datetime.now()
    header = {
        'AmplUnits': 'dBm',
        'BinarySchema': [
            {'Bytes': 4, 'DataValue': 'start-of-sweep'},
            {'Bytes': 4, 'DataValue': 'id'},
            {'Bytes': 4, 'DataValue': 'timestamp'},
            {'Curve': {'Color': color, 'CoordinationSource': True,
                       'FreqRanges': [{'EndFreq': int(freqs_khz[-1]), 'StartFreq': int(freqs_khz[0]),
                                       'StepFreq': int(steps[0])}],
                       'Name': curve_name, 'ResolutionBandWidth': 0}},
            {'Bytes': 2, 'DataValue': 'crc16'},
        ],
        'BitWidth': 16,
        'FreqUnits': 'KHz',
        'NoDataValue': -1400,
        'PeriodicSpecialSweeps': [
            {'Interval': 40, 'Type': 'Periodic running peakhold'},
            {'Interval': 100, 'Type': 'Periodic interval peakhold'},
        ],
        'Scale Factor': int(scale_factor),
        'ScannerModel': SCANNER_MODEL if model is None else model,
        'ScannerName': '',
        'StartDate': dt.strftime('%m/%d/%Y'),
        'StartTime': dt.strftime('%H:%M:%S'),
        'Title': title,
        'Version': '1.0.0.0',
    }
    v = np.asarray(dB, dtype=np.float64) * scale_factor
    q = np.sign(v) * np.floor(np.abs(v) + 0.5)          # half away from zero, like WWB
    q = np.clip(q, -32768, 32767).astype('>i2')
    rec = b'@Swp' + struct.pack('>I', 1) + struct.pack('>I', 0) + q.tobytes()
    rec += struct.pack('>H', _crc16_arc_np(rec))
    ext = {'Creator': creator, 'UserCurveColors': [color]}
    return (b'//@ShureScan\n' + _json_wwb(header).encode('utf-8') + b'\n@Binary:' + rec
            + b'@Extended:\n' + _json_wwb(ext).encode('utf-8') + b'\n@End'
            + struct.pack('>I', 1) + struct.pack('>I', 1))


class WWB3Exporter(BaseExporter):
    """Shure WWB 7 native scan file (.sdb3), whole dB like WWB itself.
    scale_factor=10 keeps 0.1 dB, but whether WWB reads it is not yet
    checked (experiment, 04.11.2026)."""
    _extension = 'sdb3'
    def __init__(self, **kwargs):
        super(WWB3Exporter, self).__init__(**kwargs)
        self.scale_factor = kwargs.get('scale_factor', 1)
        self.dt = kwargs.get('dt')
    def build_data(self):
        grid, values, step = wwb_points(self.spectrum)
        if np.unique(np.diff(grid)).size != 1:
            grid, values = wwb_grid(self.spectrum)
        return sdb3_bytes(grid, values, title=os.path.basename(self.filename),
                          color=self.spectrum.color.to_hex(), dt=self.dt,
                          scale_factor=self.scale_factor)
    def write_file(self):
        data = self.build_data()
        with open(self.filename, 'wb') as f:
            f.write(data)
