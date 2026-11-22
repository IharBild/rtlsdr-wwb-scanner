import base64
import io
import json
import pickle

import numpy as np

# V22.0: the JSON encoders and decoders of the program are registered here
# (was the package json-object-factory 0.0.2 of 2016). register() takes a
# class or object with encode(o) -> dict or None and decode(d) -> object or d,
# like before, so the files written by earlier versions read the same.
_CODECS = []


def register(obj):
    inst = obj() if isinstance(obj, type) else obj
    _CODECS.append(inst)
    return obj


class Encoder(json.JSONEncoder):
    def default(self, o):
        for c in _CODECS:
            r = c.encode(o)
            if r is not None:
                return r
        return super(Encoder, self).default(o)


def obj_hook(d):
    for c in _CODECS:
        d = c.decode(d)
        if not isinstance(d, dict):
            return d
    return d

## From http://stackoverflow.com/questions/27909658/
##
## V17.1 (security): arrays used to be stored as a pickle and read back with
## pickle.loads, so a planted database file could run any code. Now:
##  - new arrays are stored in the .npy format without pickle ("__npy__"),
##  - old arrays ("__ndarray__", every scan saved before V17.1) are read with
##    an unpickler that only knows how to rebuild numpy arrays; anything else
##    in such a file is refused with an error instead of being executed.

_ALLOWED = {
    ('numpy.core.multiarray', '_reconstruct'),
    ('numpy._core.multiarray', '_reconstruct'),
    ('numpy.core.multiarray', 'scalar'),
    ('numpy._core.multiarray', 'scalar'),
    ('numpy', 'ndarray'),
    ('numpy', 'dtype'),
    ('_codecs', 'encode'),
}


class UnsafeData(pickle.UnpicklingError):
    pass


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in _ALLOWED:
            return super(_SafeUnpickler, self).find_class(module, name)
        raise UnsafeData('refused to load %s.%s from a scan database: only numpy arrays are allowed'
                         % (module, name))


def safe_array_loads(data):
    return _SafeUnpickler(io.BytesIO(data), encoding='bytes').load()


@register
class NumpyEncoder(object):
    def encode(self, obj):
        if isinstance(obj, np.ndarray):
            if obj.dtype.hasobject:
                return None          # not storable without pickle
            buf = io.BytesIO()
            np.save(buf, obj, allow_pickle=False)
            data_b64 = base64.b64encode(buf.getvalue())
            if isinstance(data_b64, bytes):
                data_b64 = data_b64.decode('UTF-8')
            return dict(__npy__=data_b64)
        return None
    def decode(self, d):
        if '__npy__' in d:
            data = base64.b64decode(d['__npy__'])
            buf = io.BytesIO(data)
            check_npy_header(buf, 'stored array')        # V22.9 (audit P3-05)
            return np.load(buf, allow_pickle=False)
        if '__ndarray__' in d:
            data = base64.b64decode(d['__ndarray__'])
            return safe_array_loads(data)
        return d

# V22.9 (external audit P3-05): an .npy header can claim any shape; np.load
# then tries to allocate it (a 199-byte file asked for 7.28 TiB). The
# header is checked first. A full 0.5-1766 MHz scan has ~70 000 points.
MAX_ARRAY_ITEMS = 50000000
MAX_ARRAY_BYTES = 1 << 30


def check_npy_header(fp, what='array', max_items=None, max_bytes=None):
    """Read the .npy header at ``fp`` (position restored) and raise
    ValueError when the array would be too big or holds Python objects."""
    from numpy.lib import format as fmt
    max_items = MAX_ARRAY_ITEMS if max_items is None else max_items
    max_bytes = MAX_ARRAY_BYTES if max_bytes is None else max_bytes
    pos = fp.tell()
    try:
        version = fmt.read_magic(fp)
        if version == (1, 0):
            shape, _, dtype = fmt.read_array_header_1_0(fp)
        elif version == (2, 0):
            shape, _, dtype = fmt.read_array_header_2_0(fp)
        else:
            shape, _, dtype = fmt._read_array_header(fp, version)
    finally:
        fp.seek(pos)
    n = 1
    for d in shape:
        n *= int(d)
    if dtype.hasobject:
        raise ValueError('%s holds Python objects (not accepted)' % what)
    if n > max_items or n * dtype.itemsize > max_bytes:
        raise ValueError('%s is too large (%d values of %d bytes)' % (what, n, dtype.itemsize))
    return shape, dtype


def load_npz_member(path, key, what=None):
    """``key`` of the .npz file ``path``, its size checked before it is read
    (V22.9). A missing key or a damaged archive: ValueError."""
    import os
    import zipfile
    name = os.path.basename(str(path))
    what = what or name
    try:
        with zipfile.ZipFile(path) as z:
            member = key + '.npy'
            if member not in z.namelist():
                raise ValueError('%s: no %r in the file' % (what, key))
            with z.open(member) as fp:
                buf = io.BytesIO(fp.read(1 << 16))
            check_npy_header(buf, '%s: %s' % (what, key))
        with np.load(path, allow_pickle=False) as d:
            return d[key]
    except zipfile.BadZipFile as exc:
        raise ValueError('%s: not an .npz file (%s)' % (what, exc))
    except MemoryError:
        raise ValueError('%s: too large to load' % what)


def dumps(obj, **kwargs):
    kwargs.setdefault('cls', Encoder)
    return json.dumps(obj, **kwargs)

def loads(s, **kwargs):
    kwargs.setdefault('object_hook', obj_hook)
    return json.loads(s, **kwargs)

def dump(*args, **kwargs):
    kwargs.setdefault('cls', Encoder)
    return json.dump(*args, **kwargs)

def load(*args, **kwargs):
    kwargs.setdefault('object_hook', obj_hook)
    return json.load(*args, **kwargs)
