import sys
import base64
import io
import json
import pickle

import numpy as np

PY3 = sys.version_info.major >= 3

import jsonfactory

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
    if PY3:
        return _SafeUnpickler(io.BytesIO(data), encoding='bytes').load()
    return _SafeUnpickler(io.BytesIO(data)).load()


@jsonfactory.register
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
            return np.load(io.BytesIO(data), allow_pickle=False)
        if '__ndarray__' in d:
            data = base64.b64decode(d['__ndarray__'])
            if PY3 and not isinstance(data, bytes):
                data = bytes(data, 'UTF-8')
            return safe_array_loads(data)
        return d

def dumps(obj, **kwargs):
    return jsonfactory.dumps(obj, **kwargs)

def loads(s, **kwargs):
    return jsonfactory.loads(s, **kwargs)

def dump(*args, **kwargs):
    kwargs.setdefault('cls', jsonfactory.Encoder)
    return json.dump(*args, **kwargs)

def load(*args, **kwargs):
    kwargs.setdefault('object_hook', jsonfactory.obj_hook)
    return json.load(*args, **kwargs)
