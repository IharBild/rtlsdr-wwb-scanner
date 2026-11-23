import os
import os.path
import logging
import datetime
import re
import struct
import xml.etree.ElementTree as ET

import numpy as np

from wwb_scanner.scan_objects import Spectrum

logger = logging.getLogger(__name__)

# V22.9 (external audit P3-04): points one imported file may have. A full
# 0.5-1766 MHz scan has ~70 000; an sdb3 header asking for 2e9 points hung
# the window in np.arange.
MAX_IMPORT_POINTS = 20000000
# V22.11 (external audit P2-CSV1): the largest file that is read at all. A
# full scan is ~2 MB as CSV, ~0.3 MB as sdb3; 20 million CSV lines are
# ~400 MB. Anything bigger is refused before it is read into memory.
MAX_IMPORT_BYTES = 512 * 1024 * 1024


def check_file_size(filename):
    try:
        size = os.path.getsize(filename)
    except OSError as exc:
        raise ValueError('%s: the file can not be read (%s)' % (os.path.basename(str(filename)), exc))
    if size > MAX_IMPORT_BYTES:
        raise ValueError('%s: the file is too large for a scan (%d MB, at most %d MB)'
                         % (os.path.basename(str(filename)), size // (1 << 20), MAX_IMPORT_BYTES >> 20))
    return size


def decode_text(raw):
    """V20.8: the text of a scan file whatever program wrote it. UTF-8 first
    (with or without the byte order mark), then the code page of this
    computer (files of older versions of this program, WSM files with
    umlauts), then Latin-1, which reads any byte. Before this a file was read
    in the code page of Windows only: a UTF-8 file failed or was not
    recognised. Line ends become '\n' as in text mode."""
    import locale
    text = None
    for enc in ('utf-8-sig', locale.getpreferredencoding(False) or 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode('latin-1')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def local_timestamp(dt):
    """Naive local time (as WWB, WSM and our exports write it) -> epoch
    seconds for Spectrum.timestamp_utc."""
    import time as _time
    return _time.mktime(dt.timetuple()) + dt.microsecond / 1e6


def parse_wsm_time(text):
    """'08-11-2026 02-44-57 PM' (exporters.wsm_time) -> local time, or None."""
    text = (text or '').strip()
    for fmt in ('%d-%m-%Y %I-%M-%S %p', '%d-%m-%Y %H-%M-%S'):
        try:
            if fmt.endswith('%p'):
                parts = text.rsplit(' ', 1)
                if len(parts) != 2 or parts[1].upper() not in ('AM', 'PM'):
                    continue
                d = datetime.datetime.strptime(parts[0], '%d-%m-%Y %I-%M-%S')
                h = d.hour % 12 + (12 if parts[1].upper() == 'PM' else 0)
                return d.replace(hour=h)
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_sdb3_time(date, time_):
    """sdb3 header 'StartDate' '11/04/2026' and 'StartTime' '14:21:22'
    (local, as exporters.sdb3_bytes and WWB write them) -> local time, or None."""
    try:
        d = datetime.datetime.strptime('%s %s' % (str(date).strip(), str(time_).strip().split('.')[0]),
                                       '%m/%d/%Y %H:%M:%S')
    except (ValueError, TypeError):
        return None
    return d


class BaseImporter(object):
    def __init__(self, **kwargs):
        self.spectrum = Spectrum()
        self.filename = kwargs.get('filename')
    @classmethod
    def import_file(cls, filename):
        if not filename:
            raise ValueError('Open: no file name given')
        ext = os.path.splitext(str(filename))[1].strip('.').lower()
        def find_importer(_cls):
            if getattr(_cls, '_extension', None) == ext:
                return _cls
            for _subcls in _cls.__subclasses__():
                r = find_importer(_subcls)
                if r is not None:
                    return r
            return None
        found = find_importer(BaseImporter)
        if found is None:                                            # V18.1 (audit 3.2)
            raise ValueError('Open: format ".%s" is not supported - use .csv, .sdb2, .sdb3 or .npz' % ext)
        fh = found(filename=filename)
        return fh()
    def __call__(self):
        self.file_data = self.load_file()
        try:
            self.parse_file_data()
        except (KeyError, IndexError, TypeError, AttributeError, ZeroDivisionError,
                OverflowError, MemoryError) as exc:
            # V18.1 (audit 3.3): a damaged file gives one clear message
            raise ValueError('%s: the file is damaged or not a supported scan file (%s: %s)'
                             % (os.path.basename(str(self.filename)), type(exc).__name__, exc))
        return self.spectrum
    def load_file(self):
        check_file_size(self.filename)
        with open(self.filename, 'rb') as f:
            raw = f.read()
        return decode_text(raw)
    def parse_file_data(self):
        raise NotImplementedError('Method must be implemented by subclasses')

class NumpyImporter(BaseImporter):
    """V22.9 (external audit P3-05, P3-12): the size in the file is checked
    before it is read, no Python objects, and every problem is one clear
    ValueError with the file name (a missing key gave a bare KeyError).
    V22.11 (P2-NPZ1): the values as measured (``raw_dbFS``) and the passes
    of the scan come in too when the file has them (history files, our
    own .npz export); before only the points were read."""
    _extension = 'npz'
    KEYS = ('sample_data', 'raw_dbFS', 'pass_freqs', 'pass_levels', 'pass_times', 'timestamp_utc',
            'counts')
    def __call__(self):
        from wwb_scanner.utils.numpyjson import load_npz_members
        from wwb_scanner.scan_objects.passes import ScanPasses
        name = os.path.basename(str(self.filename))
        try:
            arrays = load_npz_members(self.filename, self.KEYS, name, required=('sample_data',))
        except ValueError as exc:
            raise ValueError('%s - the file is damaged or not a scan of this program' % (exc,))
        data = arrays['sample_data']
        if data.dtype.names is None or not {'frequency', 'dbFS'} <= set(data.dtype.names):
            raise ValueError('%s: the file is damaged or not a scan of this program (no frequency/'
                             'dbFS points)' % name)
        spectrum = self.spectrum
        spectrum.name = name
        spectrum.add_sample_set(data=data)
        # V22.13: the time of the scan and the weights of the points
        ts = arrays.get('timestamp_utc')
        if ts is not None and np.size(ts) == 1 and np.isfinite(np.ravel(ts)[0]) and np.ravel(ts)[0] > 0:
            spectrum.timestamp_utc = float(np.ravel(ts)[0])
        if arrays.get('counts') is not None:
            spectrum.sample_data.set_counts(arrays['counts'])
        raw = arrays.get('raw_dbFS')
        if raw is not None:
            raw = np.asarray(raw, dtype=np.float64)
            if raw.ndim == 1 and raw.shape == (spectrum.sample_data.size,):
                spectrum.raw_dbFS = raw
            else:
                logger.warning('%s: raw_dbFS of %s values for %d points, not used',
                               name, raw.shape, spectrum.sample_data.size)
        if {'pass_freqs', 'pass_levels', 'pass_times'} <= set(arrays):
            try:
                pf = np.asarray(arrays['pass_freqs'], dtype=np.float64)
                pl = np.asarray(arrays['pass_levels'], dtype=np.float64)
                pt = np.asarray(arrays['pass_times'], dtype=np.float64)
                if pl.ndim != 2 or pf.ndim != 1 or pl.shape[1] != pf.size or pt.shape != (pl.shape[0],):
                    raise ValueError('shapes %s %s %s' % (pf.shape, pl.shape, pt.shape))
                spectrum.passes = ScanPasses.from_arrays(
                    {'pass_freqs': pf, 'pass_levels': pl, 'pass_times': pt})
            except (ValueError, TypeError) as exc:
                logger.warning('%s: the passes of the scan are damaged, not used (%s)', name, exc)
        return spectrum


class CSVImporter(BaseImporter):
    delimiter_char = ','
    _extension = 'csv'
    def parse_file_data(self):
        spectrum = self.spectrum
        spectrum.name = os.path.basename(self.filename)
        if self.file_data.lstrip('\ufeff \r\n\t').startswith('Receiver;'):
            return self.parse_wsm()
        bad = []
        good = [0]
        def iter_lines():
            for n, line in enumerate(self.file_data.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                # V22.11 (external audit P2-CSV1): a limit on the points, as
                # for sdb3 (before: any number of lines went into memory)
                if good[0] >= MAX_IMPORT_POINTS:
                    raise ValueError('%s: too many points (more than %d)'
                                     % (os.path.basename(self.filename), MAX_IMPORT_POINTS))
                parts = line.split(',')
                try:
                    if len(parts) != 2:
                        raise ValueError(line)
                    f, v = float(parts[0]), float(parts[1])
                    # V22.9 (external audit P3-07): nan / inf are not levels
                    if not (np.isfinite(f) and np.isfinite(v)):
                        raise ValueError(line)
                except ValueError:
                    bad.append((n, line[:40]))
                    continue
                good[0] += 1
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

    # V19: Sennheiser WSM scan (receiver scans, or ours). WSM levels are dB
    # above the receiver's noise floor; they are shown on our dBFS axis as
    # WSM_FLOOR_DBFS + level / scale, so a WSM scan lies over an RTL-SDR scan
    # of the same band (the shape is what can be compared, not the absolute level).
    WSM_FLOOR_DBFS = -100.
    def parse_wsm(self):
        from wwb_scanner.file_handlers.exporters import WSM_LEVEL_SCALE
        lines = self.file_data.replace('\r', '').split('\n')
        head = [i for i, l in enumerate(lines) if l.startswith('Frequency;')]
        if not head:
            raise ValueError('%s: WSM file without the "Frequency;..." line' % os.path.basename(self.filename))
        cols = lines[head[0]].split(';')
        if 'RF level' not in cols:
            raise ValueError('%s: WSM file without an "RF level" column' % os.path.basename(self.filename))
        li = cols.index('RF level')
        freqs, levels = [], []
        for l in lines[head[0] + 1:]:
            if not l.strip():
                break                       # the data block ends at the first empty line
            parts = l.split(';')
            try:
                f, v = float(parts[0]), float(parts[li])
            except (ValueError, IndexError):
                continue
            if not (np.isfinite(f) and np.isfinite(v)):
                continue
            freqs.append(f)
            levels.append(v)
            if len(freqs) > MAX_IMPORT_POINTS:
                raise ValueError('%s: too many points (more than %d)'
                                 % (os.path.basename(self.filename), MAX_IMPORT_POINTS))
        if len(freqs) < 2:
            raise ValueError('%s: no scan data in the WSM file' % os.path.basename(self.filename))
        # V22.11: the time of the scan from the "Date/Time;" line (WSM writes
        # '08-11-2026 02-44-57 PM'); before, an imported scan got the time
        # of the import
        for l in lines[:head[0]]:
            if l.startswith('Date/Time;'):
                dt = parse_wsm_time(l.split(';', 1)[1])
                if dt is not None:
                    self.spectrum.timestamp_utc = local_timestamp(dt)
                else:
                    logger.warning('%s: date %r not understood', self.filename, l)
                break
        receiver = lines[0].split(';', 1)[1].strip() if ';' in lines[0] else ''
        self.spectrum.name = '%s (WSM%s)' % (os.path.basename(self.filename), ', ' + receiver if receiver else '')
        dbFS = self.WSM_FLOOR_DBFS + np.asarray(levels, dtype=np.float64) / WSM_LEVEL_SCALE
        self.spectrum.add_sample_set(frequency=np.asarray(freqs, dtype=np.float64) / 1000., dbFS=dbFS)


class BaseWWBImporter(BaseImporter):
    def load_file(self):
        # V17.1 (security): WWB files never carry a DTD. A DTD with entities
        # can blow a few KB up to gigabytes ("XML bomb") on the expat of
        # Python 3.7 and hang the program, so such files are refused.
        check_file_size(self.filename)
        with open(self.filename, 'rb') as f:
            data = f.read()
        refuse = ValueError('%s: XML with a DTD / entity declarations is not a WWB file and is not '
                            'accepted (security)' % os.path.basename(self.filename))
        if re.search(br'<!\s*(DOCTYPE|ENTITY)', data, re.IGNORECASE):
            raise refuse
        # V22.9 (external audit P3-06): the bytes of a UTF-16 file do not show
        # "<!DOCTYPE"; expat reads the file in its own encoding and stops at
        # the first declaration, before any entity is expanded
        from xml.parsers import expat
        pre = expat.ParserCreate()

        def no_dtd(*args):
            raise refuse
        pre.StartDoctypeDeclHandler = no_dtd
        pre.EntityDeclHandler = no_dtd
        try:
            pre.Parse(data, True)
        except expat.ExpatError as exc:
            raise ValueError('%s: not a WWB file (XML error: %s)' % (os.path.basename(self.filename), exc))
        return ET.ElementTree(ET.fromstring(data))

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
                # V22.11: the date and time are local (as WWB writes them), not UTC
                spectrum.timestamp_utc = local_timestamp(
                    datetime.datetime.strptime(dt_str, '%a %b %d %Y %H:%M:%S'))
            except ValueError:
                logger.warning('%s: date %r not understood', self.filename, dt_str)
        freqs = np.fromiter((float(t.text) / 1000. for t in freq_set), dtype=np.float64)
        dB = np.fromiter((float(t.text) for t in data_set), dtype=np.float64)
        n = min(freqs.size, dB.size)
        freqs, dB = freqs[:n], dB[:n]
        # V22.9 (external audit P3-18): a point without data (no_data_value,
        # -140 in the files of WWB and of this program) is a gap, not a level
        nodata = root.find('data_sets')
        nodata = nodata.get('no_data_value') if nodata is not None else None
        keep = np.isfinite(freqs) & np.isfinite(dB)
        if nodata is not None:
            try:
                keep &= dB > float(nodata) + 0.05
            except ValueError:
                pass
        if not keep.any():
            raise ValueError('%s: no measured point in the file' % os.path.basename(self.filename))
        spectrum.add_sample_set(frequency=freqs[keep], dbFS=dB[keep])


class WWB3Importer(BaseImporter):
    """Shure WWB 7 scan file (.sdb3): JSON header, binary sweeps (big-endian),
    see exporters.sdb3_bytes. Several sweeps -> peak hold (maximum per point),
    like WWB shows a scan made with a Shure receiver."""
    _extension = 'sdb3'
    def load_file(self):
        check_file_size(self.filename)
        with open(self.filename, 'rb') as f:
            return f.read()
    def parse_file_data(self):
        import json
        from wwb_scanner.file_handlers.exporters import _crc16_arc_np
        b = self.file_data
        if not b.startswith(b'//@ShureScan'):
            raise ValueError('%s: not a WWB sdb3 file' % os.path.basename(self.filename))
        name = os.path.basename(self.filename)
        i = b.find(b'@Binary:')
        if i < 0:
            raise ValueError('%s: the sdb3 file has no data part (@Binary:)' % name)
        # V22.13: a damaged header names the file (was a bare JSON error)
        try:
            header = json.loads(b[len(b'//@ShureScan'):i].decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError('%s: the header of the sdb3 file is damaged (%s)' % (name, exc))
        if not isinstance(header, dict):
            raise ValueError('%s: the header of the sdb3 file is damaged' % name)
        j = b.rfind(b'@Extended:')
        body = b[i + len(b'@Binary:'):j if j > 0 else len(b)]
        width = int(header.get('BitWidth', 16)) // 8
        if width not in (1, 2, 4):                                    # V18.1 (audit 3.3)
            raise ValueError('%s: BitWidth %r is not supported'
                             % (os.path.basename(self.filename), header.get('BitWidth')))
        dtype = {1: '>i1', 2: '>i2', 4: '>i4'}[width]
        scale = float(header.get('Scale Factor', 1)) or 1.
        nodata = header.get('NoDataValue')
        layout, freqs = [], []
        total = 0
        for item in header['BinarySchema']:
            if 'Curve' in item:
                n = 0
                for r in item['Curve']['FreqRanges']:
                    # V22.9 (external audit P3-04): the number of points is
                    # checked before the grid is made
                    lo, hi, step = float(r['StartFreq']), float(r['EndFreq']), float(r['StepFreq'])
                    if not (np.isfinite(lo) and np.isfinite(hi) and np.isfinite(step)) or \
                            step <= 0 or hi < lo:
                        raise ValueError('%s: frequency range %r is not valid'
                                         % (os.path.basename(self.filename), r))
                    count = int((hi - lo) / step) + 1
                    total += count
                    if total > MAX_IMPORT_POINTS:
                        raise ValueError('%s: too many points (%d, at most %d)'
                                         % (os.path.basename(self.filename), total, MAX_IMPORT_POINTS))
                    f = np.arange(lo, hi + step / 2., step)
                    freqs.append(f)
                    n += f.size
                layout.append(('curve', n * width))
            else:
                size = int(item['Bytes'])
                if not 0 <= size <= 1 << 20:
                    raise ValueError('%s: a sweep field of %d bytes is not valid'
                                     % (os.path.basename(self.filename), size))
                layout.append((item.get('DataValue'), size))
        rec = sum(size for _, size in layout)
        if rec <= 0:
            raise ValueError('%s: empty sweep layout' % os.path.basename(self.filename))
        if len(body) % rec:
            logger.warning('%s: %d bytes at the end do not make a whole sweep, ignored',
                           os.path.basename(self.filename), len(body) % rec)
        nsweeps = len(body) // rec
        if nsweeps < 1:
            raise ValueError('%s: no sweeps' % os.path.basename(self.filename))
        sweeps = []
        bad_crc = 0
        crc_at = None
        off = 0
        for name, size in layout:
            if name == 'crc16' and size == 2:
                crc_at = off
            off += size
        for k in range(nsweeps):
            pos = k * rec
            # V22.11 (external audit P3-11): CRC-16/ARC of each sweep (from
            # "@Swp" to the last value), the same sum WWB 7.9 writes
            # (tests/reference/wwb79_sdb3_from_our_sdb2.sdb3). A wrong sum is
            # reported, the sweep is still read: whether WWB itself refuses
            # such a file is not known.
            if crc_at is not None:
                want = struct.unpack('>H', body[pos + crc_at:pos + crc_at + 2])[0]
                if _crc16_arc_np(body[pos:pos + crc_at]) != want:
                    bad_crc += 1
            for name, size in layout:
                if name == 'curve':
                    sweeps.append(np.frombuffer(body[pos:pos + size], dtype=dtype).astype(np.float64))
                pos += size
        if bad_crc:
            logging.getLogger('wwb_scanner.status').warning(
                '%s: check sum wrong in %d of %d sweeps - the file may be damaged',
                os.path.basename(self.filename), bad_crc, nsweeps)
        self.bad_crc = bad_crc
        data = np.vstack(sweeps)
        if nodata is not None:
            data[data == nodata] = np.nan
        # V22.9 (external audit P3-18): a point without data in every sweep
        # is a gap (before: -140 dB, a level that was never measured)
        has = np.isfinite(data).any(axis=0)
        dB = np.full(data.shape[1], np.nan)
        if has.any():
            dB[has] = np.nanmax(data[:, has], axis=0) / scale
        f_khz = np.concatenate(freqs)
        if f_khz.size != dB.size:
            raise ValueError('%s: the sweep does not fit the frequency ranges'
                             % os.path.basename(self.filename))
        if not has.any():
            raise ValueError('%s: no measured point in the file' % os.path.basename(self.filename))
        f_khz, dB = f_khz[has], dB[has]
        unit = str(header.get('FreqUnits', 'KHz')).lower()
        f_mhz = f_khz / {'khz': 1000., 'mhz': 1., 'hz': 1e6}.get(unit, 1000.)
        spectrum = self.spectrum
        spectrum.name = header.get('Title') or os.path.basename(self.filename)
        if header.get('StartDate') and header.get('StartTime'):
            dt = parse_sdb3_time(header.get('StartDate'), header.get('StartTime'))
            if dt is not None:
                spectrum.timestamp_utc = local_timestamp(dt)
            else:
                logger.warning('%s: date %r %r not understood', self.filename,
                               header.get('StartDate'), header.get('StartTime'))
        if nsweeps > 1:
            logger.info('%s: %d sweeps, peak hold used', self.filename, nsweeps)
        spectrum.add_sample_set(frequency=f_mhz, dbFS=dB)
