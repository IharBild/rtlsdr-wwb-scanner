import os
import sys
import types

import pytest
import numpy as np
from scipy import signal

@pytest.fixture(autouse=True)
def _forget_hung_usb_calls():
    """V18.1: a test that makes a USB call hang on purpose would make every
    later scan in this process refuse to start (as it must on a real
    dongle). Each test starts with a "fresh dongle"."""
    yield
    try:
        from wwb_scanner.scanner import sample_processing
        stuck = getattr(sample_processing, '_STUCK', None)
        if stuck is not None:
            del stuck[:]
    except ImportError:
        pass

@pytest.fixture
def tmp_db_store(tmpdir, monkeypatch):
    db_root = tmpdir.mkdir('db_root')
    db_path = db_root.join('db.json')
    scan_db_path = db_root.join('scan_db.json')
    monkeypatch.setattr('wwb_scanner.utils.dbstore.DBStore.DB_PATH', str(db_path))
    monkeypatch.setattr('wwb_scanner.utils.dbstore.DBStore.SCAN_DB_PATH', str(scan_db_path))
    return {'db_path':db_path, 'scan_db_path':scan_db_path}

@pytest.fixture
def random_samples():
    def gen(n=1024, rs=2.048e6, fc=800e6):
        a = np.random.randint(low=-128, high=128, size=n)
        sig = signal.hilbert(a / 256.)
        freqs, Pxx = signal.welch(sig, fs=rs, return_onesided=False)
        if np.count_nonzero(Pxx) != Pxx.size:
            ix = np.argwhere(Pxx==0)
            Pxx[ix] += 1. / 1e8
        s_ix = np.argsort(freqs)
        freqs = freqs[s_ix]
        Pxx = Pxx[s_ix]

        freqs += fc
        Pxx *= fc
        freqs /= 1e6

        return freqs, sig, Pxx
    return gen

@pytest.fixture
def data_files():
    base_path = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_path, 'data')

    files = {}
    for fn in os.listdir(data_dir):
        if os.path.splitext(fn)[1] not in ['.csv', '.sdb2']:
            continue
        s = os.path.splitext(os.path.basename(fn))[0]
        start_freq, end_freq = [float(v) for v in s.split('-')]
        fkey = (start_freq, end_freq)
        skey = os.path.splitext(fn)[1].strip('.')
        if fkey not in files:
            files[fkey] = {}
        files[fkey][skey] = os.path.join(data_dir, fn)

    return files


# The dongle driver is not needed by the tests; use a stub if it is not installed.
try:
    import rtlsdr  # noqa: F401
except Exception:
    _stub = types.ModuleType('rtlsdr')
    _stub.RtlSdr = object
    sys.modules['rtlsdr'] = _stub
