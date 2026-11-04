import os
import os.path
import logging
import datetime
import xml.etree.ElementTree as ET
import itertools

import numpy as np

from wwb_scanner.scan_objects import Spectrum

logger = logging.getLogger(__name__)

class BaseImporter(object):
    def __init__(self, **kwargs):
        self.spectrum = Spectrum()
        self.filename = kwargs.get('filename')
    @classmethod
    def import_file(cls, filename):
        ext = os.path.splitext(filename)[1].strip('.').lower()
        def find_importer(_cls):
            if getattr(_cls, '_extension', None) == ext:
                return _cls
            for _subcls in _cls.__subclasses__():
                r = find_importer(_subcls)
                if r is not None:
                    return r
            return None
        cls = find_importer(BaseImporter)
        fh = cls(filename=filename)
        return fh()
    def __call__(self):
        self.file_data = self.load_file()
        self.parse_file_data()
        return self.spectrum
    def load_file(self):
        with open(self.filename, 'r') as f:
            s = f.read()
        return s
    def parse_file_data(self):
        raise NotImplementedError('Method must be implemented by subclasses')

class NumpyImporter(BaseImporter):
    _extension = 'npz'
    def __call__(self):
        data = np.load(self.filename)
        spectrum = self.spectrum
        spectrum.name = os.path.basename(self.filename)
        spectrum.add_sample_set(data=data['sample_data'])
        return spectrum


class CSVImporter(BaseImporter):
    delimiter_char = ','
    _extension = 'csv'
    def parse_file_data(self):
        spectrum = self.spectrum
        spectrum.name = os.path.basename(self.filename)
        bad = []
        def iter_lines():
            for n, line in enumerate(self.file_data.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                parts = line.split(',')
                try:
                    if len(parts) != 2:
                        raise ValueError(line)
                    f, v = float(parts[0]), float(parts[1])
                except ValueError:
                    bad.append((n, line[:40]))
                    continue
                yield f
                yield v
        a = np.fromiter(iter_lines(), dtype=np.float64)
        if bad:
            logger.warning('%s: %d lines skipped (first: line %d %r)',
                           self.filename, len(bad), bad[0][0], bad[0][1])
        if a.size < 4:
            raise ValueError('%s: no scan data found (expected lines like "470000,-95.5")'
                             % (os.path.basename(self.filename),))
        freqs = a[::2]
        dB = a[1::2]
        # WWB files hold kHz (470000), our older files hold MHz (470.0).
        # No scan goes above 10 GHz, so a top value that big means kHz.
        # (The lowest value can be 0 kHz - a scan from 0 MHz - so it must
        # not decide: that file was read as MHz up to 1 766 000 MHz.)
        if freqs.size and np.nanmax(freqs) >= 10000.:
            freqs = freqs / 1000.
        spectrum.add_sample_set(frequency=freqs, dbFS=dB)

class BaseWWBImporter(BaseImporter):
    def load_file(self):
        return ET.parse(self.filename)

class WWBImporter(BaseWWBImporter):
    _extension = 'sdb2'
    def parse_file_data(self):
        spectrum = self.spectrum
        root = self.file_data.getroot()
        color = root.get('color')
        if color is not None:
            spectrum.color = spectrum.color.from_hex(color)
        name = root.get('name')
        if name is not None:
            spectrum.name = name
        freq_set = root.find('*/freq_set')
        data_set = root.find('*/data_set')
        ts = data_set.get('date_time')
        if ts is not None:
            spectrum.timestamp_utc = float(ts) / 1000.
        else:
            # WWB 7.9 writes 'Wed Nov 4 2026' and '14:21:22.182'
            dt_str = ' '.join([root.get('date'), root.get('time').split('.')[0]])
            try:
                spectrum.datetime_utc = datetime.datetime.strptime(dt_str, '%a %b %d %Y %H:%M:%S')
            except ValueError:
                logger.warning('%s: date %r not understood', self.filename, dt_str)
        freqs = np.fromiter((float(t.text) / 1000. for t in freq_set), dtype=np.float64)
        dB = np.fromiter((float(t.text) for t in data_set), dtype=np.float64)
        spectrum.add_sample_set(frequency=freqs, dbFS=dB)


class WWB3Importer(BaseImporter):
    """Shure WWB 7 scan file (.sdb3): JSON header, binary sweeps (big-endian),
    see exporters.sdb3_bytes. Several sweeps -> peak hold (maximum per point),
    like WWB shows a scan made with a Shure receiver."""
    _extension = 'sdb3'
    def load_file(self):
        with open(self.filename, 'rb') as f:
            return f.read()
    def parse_file_data(self):
        import json
        import struct
        b = self.file_data
        if not b.startswith(b'//@ShureScan'):
            raise ValueError('%s: not a WWB sdb3 file' % os.path.basename(self.filename))
        i = b.index(b'@Binary:')
        header = json.loads(b[len(b'//@ShureScan'):i].decode('utf-8'))
        j = b.rfind(b'@Extended:')
        body = b[i + len(b'@Binary:'):j if j > 0 else len(b)]
        width = int(header.get('BitWidth', 16)) // 8
        dtype = {1: '>i1', 2: '>i2', 4: '>i4'}[width]
        scale = float(header.get('Scale Factor', 1)) or 1.
        nodata = header.get('NoDataValue')
        layout, freqs = [], []
        for item in header['BinarySchema']:
            if 'Curve' in item:
                n = 0
                for r in item['Curve']['FreqRanges']:
                    f = np.arange(r['StartFreq'], r['EndFreq'] + r['StepFreq'] / 2., r['StepFreq'])
                    freqs.append(f)
                    n += f.size
                layout.append(('curve', n * width))
            else:
                layout.append((item.get('DataValue'), int(item['Bytes'])))
        rec = sum(size for _, size in layout)
        nsweeps = len(body) // rec
        if nsweeps < 1:
            raise ValueError('%s: no sweeps' % os.path.basename(self.filename))
        sweeps = []
        for k in range(nsweeps):
            pos = k * rec
            for name, size in layout:
                if name == 'curve':
                    sweeps.append(np.frombuffer(body[pos:pos + size], dtype=dtype).astype(np.float64))
                pos += size
        data = np.vstack(sweeps)
        if nodata is not None:
            data[data == nodata] = np.nan
        dB = np.nanmax(data, axis=0) / scale
        dB[~np.isfinite(dB)] = -140.
        f_khz = np.concatenate(freqs)
        unit = str(header.get('FreqUnits', 'KHz')).lower()
        f_mhz = f_khz / {'khz': 1000., 'mhz': 1., 'hz': 1e6}.get(unit, 1000.)
        spectrum = self.spectrum
        spectrum.name = header.get('Title') or os.path.basename(self.filename)
        if nsweeps > 1:
            logger.info('%s: %d sweeps, peak hold used', self.filename, nsweeps)
        spectrum.add_sample_set(frequency=f_mhz, dbFS=dB)
