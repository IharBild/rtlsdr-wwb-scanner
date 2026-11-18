"""V22.4: calibrations of each dongle (plan of 18.11.2026, stage 3.1).

Before V22.4 the program kept one set of calibrations (floor profiles, the
table of the real gain steps, the gain profile of a survey) for whatever
dongle was plugged in. Two dongles differ in their floor, spurs and gain
steps, so with a second dongle the levelled floor and the Overload
re-measuring used the numbers of the first one.

Now each dongle has its own folder inside ``calibration/``, named after its
USB serial number::

    calibration/
        dongles.json             known dongles, the last one used
        sn-00000001/             files of the dongle with serial 00000001
            dongle_gain33.8_fs2400k.npz
            gain_steps_fs2400k.json
            gain_profile.json
        dongle_gain33.8_fs2400k.npz   files made before V22.4 (kept for
        ...                           V22.3 and older)

The calibration functions (cleanup, gain_cal, gain_profile) find their
files through ``paths.calibration_dir()``, which is the folder of the
**active** dongle: the one the scanner opened last, or the one chosen in
the window. Before any dongle is known it is ``calibration/`` itself, as
before V22.4.

Files made before V22.4 are copied (not moved: V22.3 still reads them) into
the folder of the first dongle seen after the update, if it is the only
dongle the program knows. With two dongles at once the program can not
tell whose they were: they are left unused and each dongle is calibrated.

Many dongles (the RTL-SDR Blog V4 too) leave the factory with the same
serial number, 00000001. Two such dongles can not be told apart: they
share calibrations, and opening "by serial" opens the first one. The
program warns; the cure is a unique serial for each (``rtl_eeprom -s``).
"""
import datetime
import io
import json
import logging
import os
import re
import shutil
import threading

from wwb_scanner import paths

logger = logging.getLogger(__name__)
status = logging.getLogger('wwb_scanner.status')

REGISTRY_NAME = 'dongles.json'
PREFIX = 'sn-'
NO_SERIAL = 'no-serial'
# files of one dongle (names as cleanup, gain_cal and gain_profile write them)
CAL_FILE_RE = re.compile(r'^(dongle_gain.*\.npz|gain_steps_fs.*\.json|gain_profile\.json)$')

_lock = threading.RLock()
_active = None          # serial (str) of the active dongle, None: not set in this process
_warned = set()         # duplicate serials already reported in this process


def key(serial):
    """Folder name of a dongle: ``sn-`` + its serial number (only letters,
    digits, '.', '_' and '-'; anything else becomes '_')."""
    s = ('' if serial is None else str(serial)).strip()
    if not s:
        return NO_SERIAL
    return PREFIX + re.sub(r'[^A-Za-z0-9._-]+', '_', s)[:64]


def calibration_root(create=False):
    return paths.calibration_root(create)


def folder(serial, create=False):
    d = os.path.join(calibration_root(), key(serial))
    if create and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------- registry

def registry_path():
    return os.path.join(calibration_root(), REGISTRY_NAME)


def load_registry():
    try:
        with io.open(registry_path(), encoding='utf-8') as fh:
            reg = json.load(fh)
        if isinstance(reg, dict):
            reg.setdefault('dongles', {})
            return reg
    except (IOError, OSError, ValueError):
        pass
    return {'version': 1, 'last': None, 'dongles': {}, 'legacy': None}


def _save_registry(reg):
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(reg, fh, indent=1)
    os.replace(tmp, path)


def known():
    """[(serial, info)] of the dongles the program has seen, last used first."""
    reg = load_registry()
    items = list(reg.get('dongles', {}).values())
    items.sort(key=lambda d: d.get('last_seen') or '', reverse=True)
    return [(d.get('serial'), d) for d in items]


# ------------------------------------------------------------ active one

def active():
    """Serial number of the dongle whose calibrations are used, or None
    (no dongle known yet: the files of calibration/ itself)."""
    with _lock:
        if _active is not None:
            return _active
    last = load_registry().get('last')
    return last if last else None


def calibration_dir(create=False):
    """The calibration folder of the active dongle (see the module text)."""
    s = active()
    if s is None:
        return calibration_root(create)
    return folder(s, create)


def legacy_files():
    """Calibration files made before V22.4 (directly in calibration/)."""
    root = calibration_root()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names if CAL_FILE_RE.match(n) and os.path.isfile(os.path.join(root, n))]


def _dongle_folders(root):
    try:
        return [n for n in os.listdir(root)
                if (n.startswith(PREFIX) or n == NO_SERIAL) and os.path.isdir(os.path.join(root, n))]
    except OSError:
        return []


def use(serial, product=None):
    """Make ``serial`` the active dongle (the scanner opened it, or the
    window chose it). Registers the dongle; the first dongle after the update
    gets the calibrations made before V22.4. Returns the number of files
    copied to it (0 normally)."""
    global _active
    serial = '' if serial is None else str(serial)
    with _lock:
        _active = serial
        try:
            return _register(serial, product)
        except Exception:
            logger.exception('dongle registry')
            return 0


def _register(serial, product):
    root = calibration_root()
    reg = load_registry()
    k = key(serial)
    now = datetime.datetime.now().isoformat(timespec='seconds')
    entry = reg['dongles'].get(k)
    first_time = entry is None
    if first_time:
        entry = {'serial': serial, 'first_seen': now}
    entry['last_seen'] = now
    if product:
        entry['product'] = str(product)
    reg['dongles'][k] = entry
    reg['last'] = serial
    copied = 0
    if not reg.get('legacy'):
        copied = _bind_legacy(reg, root, k, serial)
    _save_registry(reg)
    if first_time:
        logger.info('dongle %s (serial %r) seen for the first time; its calibrations: %s',
                    k, serial, os.path.join(root, k))
    return copied


def _bind_legacy(reg, root, k, serial):
    """Calibrations made before V22.4 go to the first dongle, if it is the
    only one the program knows. Decided once (reg['legacy'])."""
    files = legacy_files()
    if not files:
        reg['legacy'] = {'state': 'none'}
        return 0
    others = [n for n in _dongle_folders(root) if n != k and os.listdir(os.path.join(root, n))]
    if len(reg['dongles']) > 1 or others:
        reg['legacy'] = {'state': 'unused', 'files': files}
        status.warning('The calibrations made before V22.4 are not used: more than one dongle is '
                       'known and it is not clear whose they were. Calibrate each dongle '
                       '(Scan settings > Calibrate).')
        return 0
    dst = folder(serial, create=True)
    copied = 0
    for n in files:
        target = os.path.join(dst, n)
        if os.path.exists(target):
            continue
        shutil.copy2(os.path.join(root, n), target)
        copied += 1
    reg['legacy'] = {'state': 'bound', 'to': k, 'files': files}
    status.info('Calibrations made before V22.4 (%d file(s)) now belong to the dongle with serial '
                'number %s; a second dongle gets its own calibrations.', copied, serial or '(none)')
    return copied


def reset():
    """Forget the active dongle of this process (tests, a new data folder)."""
    global _active
    with _lock:
        _active = None
        _warned.clear()


# ------------------------------------------------------- same serials

def duplicates(serials):
    """Serial numbers that more than one connected dongle reports."""
    seen, dup = set(), []
    for s in serials:
        s = '' if s is None else str(s)
        if s in seen and s not in dup:
            dup.append(s)
        seen.add(s)
    return dup


def duplicate_text(serial, count):
    return ('%d dongles have the same serial number %s: the program can not tell them apart - '
            'they share their calibrations, and the one chosen may not be the one that opens. Give '
            'each dongle its own serial number (rtl_eeprom -s, one dongle plugged in at a time).'
            % (count, serial or '(none)'))


def warn_duplicates(serials):
    """Warn once per process for each serial number reported more than once."""
    out = []
    for s in duplicates(serials):
        out.append(duplicate_text(s, list(serials).count(s)))
        if s not in _warned:
            _warned.add(s)
            status.warning(out[-1])
    return out


def resolve(cls, serial_number=None, device_index=None):
    """Which dongle opening ``cls`` (pyrtlsdr RtlSdr) will give, and how to
    open it: (serial or None, keyword arguments for ``cls``).

    The serial number chosen in the window opens that dongle; without one,
    the dongle ``device_index`` (0 if not given). When two dongles report the
    same serial number, the index chosen in the window is used (opening by
    serial would always give the first one)."""
    lister = getattr(cls, 'get_device_serial_addresses', None)
    serials = None
    if lister is not None:
        try:
            serials = ['' if s is None else str(s) for s in lister()]
        except Exception as exc:
            logger.debug('serial numbers not listed: %s', exc)
    if serials is None:
        return serial_number, {'serial_number': serial_number}
    warn_duplicates(serials)
    idx_ok = isinstance(device_index, int) and 0 <= device_index < len(serials)
    if serial_number:
        if serials.count(str(serial_number)) > 1 and idx_ok and serials[device_index] == str(serial_number):
            return str(serial_number), {'device_index': device_index}
        return str(serial_number), {'serial_number': serial_number}
    idx = device_index if idx_ok else 0
    s = serials[idx] if idx < len(serials) else None
    return s, ({'device_index': idx} if idx else {'serial_number': None})
