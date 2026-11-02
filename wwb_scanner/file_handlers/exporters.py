import os
import io
import datetime
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
        self.dt = kwargs.get('dt', datetime.datetime.utcnow())
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
                model='TODO',
                name=os.path.abspath(self.filename),
                date=dt.strftime('%a %b %d %Y'),
                time=dt.strftime('%H:%M:%S'),
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
            date_time=str(int((dt - EPOCH).total_seconds() * 1000)),
        )
        return d
    def build_data(self):
        attribs = self.attribs = self.build_attribs()
        root = self.root = ET.Element('scan_data_source', attribs['scan_data_source'])
        ET.SubElement(root, 'data_sets', attribs['data_sets'])
        tree = self.tree = ET.ElementTree(root)
        return tree
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
        return tree
