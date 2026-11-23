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


def scan_local_dt(spectrum, dt=None):
    """V22.11 (P2-DATE1 for sdb3 and WSM): the local time of the scan for
    the date in an exported file; ``dt`` when given; now() only for a scan
    without a time. V22.9 did this for sdb2 only - sdb3 and the WSM CSV
    still carried the time of the export."""
    if dt is not None:
        return dt
    ts = getattr(spectrum, 'timestamp_utc', None)
    try:
        dt = datetime.datetime.fromtimestamp(float(ts)) if ts else None
    except (TypeError, ValueError, OverflowError, OSError):
        dt = None
    return dt or datetime.datetime.now()


# V22.11 (external audit P2-RES1): names Windows keeps for devices. "NUL.csv"
# was "exported" and no file appeared (the data went to the NUL device).
_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | \
    {'COM%d' % i for i in range(10)} | {'LPT%d' % i for i in range(10)} | \
    {'COM\u00b9', 'COM\u00b2', 'COM\u00b3', 'LPT\u00b9', 'LPT\u00b2', 'LPT\u00b3'}


def reserved_name(filename):
    """True for a file name Windows reads as a device (NUL, CON, COM1 ...),
    with any extension, case, or trailing dots and spaces."""
    base = os.path.basename(str(filename))
    stem = base.split('.', 1)[0].rstrip(' .').upper()
    return stem in _RESERVED_NAMES


def write_atomic(path, data):
    """V22.11: write ``data`` (bytes) to a new file next to ``path`` and put
    it in place in one step: a failed export (disk full, USB stick pulled)
    no longer leaves a half-written file under the user's name; the file is
    checked to be there afterwards."""
    from wwb_scanner.utils.dbstore import _replace
    folder = os.path.dirname(os.path.abspath(path)) or '.'
    base = os.path.basename(path)
    tmp = os.path.join(folder, '~%s.%d.part' % (base, os.getpid()))
    try:
        with open(tmp, 'wb') as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        _replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    if not os.path.isfile(path) or os.path.getsize(path) != len(data):
        raise OSError('Export: %s was not written (the file is not there after saving)'
                      % os.path.basename(path))


def _text_bytes(text, newline=None):
    """``text`` as UTF-8 bytes the way a text-mode file with this newline
    would write it (None: line ends of this system)."""
    if newline is None and os.linesep != '\n':
        text = text.replace('\n', os.linesep)
    return text.encode('utf-8')


def gap_mask(grid_khz, freqs_khz):
    """V18: True for grid points inside a gap of the scan (a scan of several
    ranges): between two measured points more than ``max(200 kHz, 4 x point
    step)`` apart and more than one point step from both of them."""
    grid_khz = np.asarray(grid_khz, dtype=np.float64)
    f = np.asarray(freqs_khz, dtype=np.float64)
    if f.size < 2 or grid_khz.size == 0:
        return np.zeros(grid_khz.size, dtype=bool)
    step = float(np.median(np.diff(f)))
    limit = max(200.0, 4.0 * step)
    i = np.clip(np.searchsorted(f, grid_khz), 1, f.size - 1)
    wide = (f[i] - f[i - 1]) > limit
    dist = np.minimum(np.abs(grid_khz - f[i - 1]), np.abs(f[i] - grid_khz))
    return wide & (dist > step)


def wwb_grid(spectrum, step=WWB_STEP_KHZ, lo=WWB_MIN_KHZ, hi=WWB_MAX_KHZ, gaps=None):
    """Scan values on a whole-kHz grid (``step`` kHz) inside ``lo``..``hi`` kHz,
    as WWB expects. Returns (grid_khz int64 array, dB array). The spectrum is
    not changed. Grid points in a gap of the scan (V18, several ranges) get
    NO_DATA_DB; with ``gaps='drop'`` they are left out."""
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
    gap = gap_mask(grid, freqs_khz)
    if gap.any():
        if gaps == 'drop':
            grid, values = grid[~gap], values[~gap]
        else:
            values[gap] = NO_DATA_DB
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
        if not filename:
            raise ValueError('Export: no file name given')          # V18.1 (audit 1.5)
        ext = os.path.splitext(str(filename))[1].strip('.').lower()
        def find_exporter(_cls):
            if getattr(_cls, '_extension', None) == ext:
                return _cls
            for _subcls in _cls.__subclasses__():
                r = find_exporter(_subcls)
                if r is not None:
                    return r
            return None
        found = find_exporter(cls)
        if found is None:                                            # V18.1 (audit 3.2)
            raise ValueError('Export: format ".%s" is not supported - use .csv, .sdb2, .sdb3 or .npz' % ext)
        if reserved_name(filename):
            raise ValueError('Export: "%s" is a name Windows keeps for a device - choose another '
                             'file name' % os.path.basename(str(filename)))
        fh = found(**kwargs)
        # V22.11: export during a scan - the points must not change while
        # the file is made (the scan waits for the lock, export takes < 1 s)
        lock = getattr(fh.spectrum, 'data_update_lock', None)
        if lock is not None:
            with lock:
                fh()
        else:
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
        # V20.8: always UTF-8, not the code page of this Windows
        write_atomic(self.filename, _text_bytes(s))

class NumpyExporter(BaseExporter):
    _extension = 'npz'
    def build_data(self):
        pass
    def write_file(self):
        # V22.11 (P2-NPZ1): with the values as measured and the passes, so
        # that File > Import gives back the whole scan (like the history)
        sp = self.spectrum
        arrays = {'sample_data': sp.sample_data}
        raw = getattr(sp, 'raw_dbFS', None)
        if raw is not None and np.shape(raw) == (sp.sample_data.size,):
            arrays['raw_dbFS'] = np.asarray(raw, dtype=np.float64)
        passes = getattr(sp, 'passes', None)
        if passes is not None and len(passes):
            arrays.update(passes.to_arrays())
        buf = io.BytesIO()
        np.savez(buf, **arrays)
        write_atomic(self.filename, buf.getvalue())

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
        # V20.8: UTF-8. The scan name goes into the "Receiver;" line of a WSM
        # file; with the code page of Windows (cp1251, cp1250...) a name with
        # other letters stopped the export ('charmap' codec can't encode).
        # A file with a plain Latin name is byte for byte as before.
        write_atomic(self.filename, _text_bytes(s, newline=''))
    @property
    def is_khz(self):
        fmt = self.frequency_format
        return fmt is not None and str(fmt).lower() == 'khz'
    @property
    def is_wsm(self):
        fmt = self.frequency_format
        return fmt is not None and str(fmt).lower() == 'wsm'
    def build_khz_data(self):
        """Resample onto a whole-kHz grid (step ``khz_step``) like WWB does,
        only inside 30-2000 MHz (WWB refuses anything else)."""
        if self.spectrum.sample_data['frequency'].size == 0:
            return ''
        grid, values = wwb_grid(self.spectrum, step=self.khz_step, gaps='drop')
        lines = ['%d%s%.1f' % (f, self.delimiter_char, v) for f, v in zip(grid, values)]
        return self.khz_newline_chars.join(lines)
    def build_data(self):
        if self.is_wsm:                       # V19: Sennheiser WSM layout
            return wsm_csv(self.spectrum)
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

# ---------------------------------------------------------------- V19: Sennheiser WSM
# WSM (Wireless Systems Manager) opens CSV scans in the layout its own
# receiver scans have (EM 300-500 G3 "RF spectrum", checked against files
# saved by WSM on 06-08.11.2026). The level is whole dB above the noise
# floor, 0..48 (WSM shows 0..40 on its scale, 2.5 % per dB).
WSM_LEVEL_MAX_DB = 48
# dB above our floor -> WSM dB. Comparing the same band scanned by an EM 300
# G3 and the RTL-SDR gave 0.90, 1.13 and 1.34 on three days (same shape,
# r = 0.92-0.99); 1.3 errs on the side of "occupied".
WSM_LEVEL_SCALE = 1.3
WSM_SQUELCH_DB = 5           # squelch column (the receiver's setting, 12.5 %)
WSM_STEP_KHZ = 25


def wsm_time(dt):
    """'08-11-2026 02-44-57 PM' (AM/PM written by hand: %p depends on the locale)."""
    h = dt.hour % 12 or 12
    return '%02d-%02d-%04d %02d-%02d-%02d %s' % (dt.day, dt.month, dt.year, h, dt.minute, dt.second,
                                                 'AM' if dt.hour < 12 else 'PM')


def wsm_levels(spectrum, step=WSM_STEP_KHZ, scale=WSM_LEVEL_SCALE):
    """(grid_khz, level_db 0..48, floor_dbFS): our scan on the whole-kHz grid,
    as dB above the floor (20th percentile, not the median: in a band full of
    TV multiplexes the median sits on them). Gaps of a scan of several
    ranges get level 0 (WSM needs every row between start and stop)."""
    grid, values = wwb_grid(spectrum, step=step)
    measured = values > NO_DATA_DB + 0.5
    if not measured.any():
        raise ValueError('WSM export: the scan has no measured points')
    floor = float(np.percentile(values[measured], 20))
    lv = np.clip(np.rint((values - floor) * float(scale)), 0, WSM_LEVEL_MAX_DB).astype(np.int64)
    lv[~measured] = 0
    return grid, lv, floor


def wsm_csv(spectrum, dt=None, receiver=None, scale=WSM_LEVEL_SCALE, squelch=WSM_SQUELCH_DB):
    grid, lv, floor = wsm_levels(spectrum, scale=scale)
    dt = scan_local_dt(spectrum, dt)
    name = receiver or ('RTL-SDR %s' % (spectrum.name or 'scan')).strip()
    name = name.replace(';', ',').replace('\r', ' ').replace('\n', ' ')[:60]
    sq_pct = '%g' % min(100.0, squelch * 2.5)
    lines = [
        'Receiver;%s' % name,
        'Date/Time;%s' % wsm_time(dt),
        'RFUnit;dB;MinimumRFValue;0',
        'Squelch;Supported',
        '',
        'Frequency Range [kHz];%d;%d;%d' % (grid[0], grid[-1], WSM_STEP_KHZ),
        'Frequency;RF level (%);RF level;Memory (%);Memory;Squelch (%);Squelch',
    ]
    lines += ['%d;%s;%d;;;%s;%d' % (f, '%g' % min(100.0, v * 2.5), v, sq_pct, squelch)
              for f, v in zip(grid, lv)]
    lines += ['', 'Marker;0', 'Index;Comment', '', 'Scales;7', 'Level (%);Level (units)',
              '25;10', '37.5;15', '50;20', '62.5;25', '75;30', '87.5;35', '100;40']
    return '\r\n'.join(lines) + '\r\n'


class BaseWWBExporter(BaseExporter):
    def __init__(self, **kwargs):
        super(BaseWWBExporter, self).__init__(**kwargs)
        # dt: local time (date/time attributes, as WWB writes them);
        # date_time is always the UTC epoch in ms. (Before V12 the date/time
        # attributes were UTC, one hour off in Warsaw.) V22.9 (external audit
        # P2-DATE1): the time of the scan, not of the export - an old scan
        # exported today showed today's date in WWB. The WWB receiver scan
        # tests/data/572-636.sdb2 carries the time it was scanned.
        self.dt = scan_local_dt(self.spectrum, kwargs.get('dt'))
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
            # V22.11 (external audit AUD-15): a number, not the word 'TODO'
            # left from the original code. 0 = not given: the value WWB 7.9
            # itself writes ("ResolutionBandWidth" 0 in the sdb3 it made of
            # our sdb2, tests/reference/wwb79_sdb3_from_our_sdb2.sdb3).
            res_bandwidth='0',
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
        # V20.8: the header says UTF-8, so write UTF-8. The file was written in
        # the code page of Windows: a folder with Cyrillic or Polish letters
        # (the path is in the name attribute) gave a file that is not XML, or
        # no file at all. Line ends stay as before (text mode).
        write_atomic(self.filename, _text_bytes(s))


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


# V22.8 (external audit A3): the raw value of a point without data in sdb3
# (header "NoDataValue", the value WWB writes itself). Before V22.8 a gap got
# NO_DATA_DB * scale = -140, which WWB reads as a measured -140 dBm (free).
SDB3_NO_DATA = -1400


def sdb3_bytes(freqs_khz, dB, title, color='#38ad6b', dt=None, scale_factor=1,
               model=None, creator='rtlsdr-wwb-scanner', curve_name='Default curve',
               no_data=None):
    """Whole sdb3 file for one sweep on a regular grid (kHz). ``no_data``:
    mask of the points without data (written as NoDataValue)."""
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
        'NoDataValue': SDB3_NO_DATA,
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
    q = np.clip(q, -32768, 32767)
    if no_data is not None:
        nd = np.asarray(no_data, dtype=bool)
        if nd.shape != q.shape:
            raise ValueError('no_data mask does not fit the points')
        q[nd] = SDB3_NO_DATA
    q = q.astype('>i2')
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
        self.dt = scan_local_dt(self.spectrum, kwargs.get('dt'))
    def build_data(self):
        grid, values, step = wwb_points(self.spectrum)
        if np.unique(np.diff(grid)).size != 1:
            grid, values = wwb_grid(self.spectrum)
        # V22.8 (audit A3): gaps of a scan of several ranges and points
        # without a value get NoDataValue, not a level
        no_data = np.asarray(values, dtype=np.float64) <= NO_DATA_DB
        return sdb3_bytes(grid, values, title=os.path.basename(self.filename),
                          color=self.spectrum.color.to_hex(), dt=self.dt,
                          scale_factor=self.scale_factor, no_data=no_data)
    def write_file(self):
        data = self.build_data()
        write_atomic(self.filename, data)
