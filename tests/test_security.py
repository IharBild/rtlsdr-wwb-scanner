"""Security: files from outside (scans from colleagues, the internet) must not
hang the program, eat the memory or run code. Findings that are still open
are checked by tools/qa_sandbox.py (section SECURITY), not here."""
import io
import json
import os
import time

import numpy as np
import pytest

from wwb_scanner.file_handlers.importers import BaseImporter

HERE = os.path.dirname(os.path.abspath(__file__))
REF_SDB3 = os.path.join(HERE, 'reference', 'wwb79_sdb3_from_our_sdb2.sdb3')
REF_SDB2 = os.path.join(HERE, 'data', '572-636.sdb2')


def _import(path, limit_s=3.0):
    t0 = time.time()
    try:
        BaseImporter.import_file(path)
        res = 'ok'
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, OSError,
            UnicodeError, EOFError, AssertionError, ZeroDivisionError) as exc:
        res = type(exc).__name__
    except Exception as exc:          # e.g. xml ParseError, struct.error
        res = type(exc).__name__
    dt = time.time() - t0
    assert dt < limit_s, 'import took %.1f s' % dt
    return res


def test_SEC1_xml_external_entity_not_read(tmpdir):
    secret = tmpdir.join('secret.txt')
    secret.write('TOPSECRET')
    p = tmpdir.join('xxe.sdb2')
    p.write('<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x SYSTEM "file://%s">]>\n'
            '<scan_data_source><data_sets><data_set>&x;</data_set></data_sets></scan_data_source>'
            % str(secret).replace('\\', '/'))
    try:
        sp = BaseImporter.import_file(str(p))
        assert 'TOPSECRET' not in repr(getattr(sp, 'name', '')), 'external file was read'
    except Exception:
        pass


def _mutations(data, n, seed):
    rng = np.random.RandomState(seed)
    out = []
    for i in range(n):
        b = bytearray(data)
        kind = i % 4
        if kind == 0:                                  # flip bytes
            for _ in range(rng.randint(1, 20)):
                b[rng.randint(len(b))] = rng.randint(256)
        elif kind == 1:                                # cut
            b = b[:rng.randint(1, len(b))]
        elif kind == 2:                                # huge numbers in the binary part
            k = rng.randint(len(b) - 4)
            b[k:k + 4] = b'\xff\xff\xff\x7f'
        else:                                          # duplicate a chunk
            k = rng.randint(len(b))
            b = b[:k] + b[k:k + 500] * 20 + b[k:]
        out.append(bytes(b))
    return out


@pytest.mark.parametrize('src,ext', [(REF_SDB3, 'sdb3'), (REF_SDB2, 'sdb2')])
def test_SEC2_fuzz_wwb_files(tmpdir, src, ext):
    """200 damaged files: every import ends quickly with a result or an error."""
    data = open(src, 'rb').read()
    results = {}
    for i, b in enumerate(_mutations(data, 200, seed=7)):
        p = str(tmpdir.join('f%03d.%s' % (i, ext)))
        with open(p, 'wb') as fh:
            fh.write(b)
        r = _import(p)
        results[r] = results.get(r, 0) + 1
    assert sum(results.values()) == 200, results


def test_SEC3_fuzz_csv(tmpdir):
    rng = np.random.RandomState(3)
    rows = ['%d,%.1f' % (470000 + 25 * i, -100 + rng.randn()) for i in range(2000)]
    cases = ['\n'.join(rows[:500]) + '\n' + 'x' * 2000000,          # one 2 MB line
             '\x00' * 10000, '1e309,1e309\n' * 100, '-0,nan\n' * 100,
             '\n'.join(r.replace(',', ';') for r in rows), 'é,ü\n' * 100]
    for i, c in enumerate(cases):
        p = str(tmpdir.join('c%d.csv' % i))
        with io.open(p, 'w', encoding='utf-8') as fh:
            fh.write(c)
        _import(p)


def test_SEC4_npz_with_python_objects_refused(tmpdir):
    """A .npz scan may not carry pickled Python objects (would run code)."""
    p = str(tmpdir.join('evil.npz'))
    np.savez(p, sample_data=np.array([{'a': 1}], dtype=object))
    with pytest.raises(Exception) as exc:
        BaseImporter.import_file(p)
    assert 'pickle' in str(exc.value).lower() or 'object' in str(exc.value).lower()


def test_SEC5_calibration_file_damaged_or_hostile(tmpdir, monkeypatch):
    from wwb_scanner.scanner import cleanup as C
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))
    path = C.profile_path(29.7, 2.4e6)
    os.makedirs(os.path.dirname(path))
    np.savez(path, freq=np.array([{'x': 1}], dtype=object), info=np.array('{}'))
    prof, why = C.find_profile(29.7, 2.4e6)
    assert prof is None and 'damaged' in why
    with open(path, 'wb') as fh:
        fh.write(os.urandom(5000))
    prof, why = C.find_profile(29.7, 2.4e6)
    assert prof is None and why


def test_SEC6_no_secrets_in_code():
    import wwb_scanner
    root = os.path.dirname(wwb_scanner.__file__)
    bad = []
    for dp, _, files in os.walk(root):
        for fn in files:
            if fn.endswith(('.py', '.qml', '.json', '.cfg')):
                s = open(os.path.join(dp, fn), encoding='utf-8', errors='replace').read()
                for word in ('BEGIN RSA PRIVATE', 'AKIA', 'ghp_', 'xoxb-', 'password=', 'api_key'):
                    if word in s:
                        bad.append((fn, word))
    assert not bad, bad


def test_SEC7_scan_database_does_not_run_code(tmpdir):
    """V17.1: a planted scan database (pickled object instead of an array) is
    refused; old databases with pickled numpy arrays still load."""
    import base64
    import pickle
    from wwb_scanner.utils import numpyjson
    marker = str(tmpdir.join('RAN'))

    class P(object):
        def __reduce__(self):
            return (os.makedirs, (marker,))
    evil = json.dumps({'a': {'__ndarray__': base64.b64encode(pickle.dumps(P())).decode('ascii')}})
    with pytest.raises(pickle.UnpicklingError):
        numpyjson.loads(evil)
    assert not os.path.exists(marker)
    old = np.zeros(4, dtype=[('frequency', 'f8'), ('iq', 'c16'), ('magnitude', 'f8'), ('dbFS', 'f8')])
    old['dbFS'] = [-100, -99, -98, -97]
    legacy = json.dumps({'a': {'__ndarray__': base64.b64encode(old.dumps()).decode('ascii')}})
    back = numpyjson.loads(legacy)['a']
    assert np.array_equal(back, old)
    s = numpyjson.dumps({'a': old})
    assert '__npy__' in s and '__ndarray__' not in s
    assert np.array_equal(numpyjson.loads(s)['a'], old)


def test_SEC8_sdb2_with_dtd_refused(tmpdir):
    p = tmpdir.join('bomb.sdb2')
    p.write('<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>\n<r>&b;</r>')
    with pytest.raises(ValueError) as exc:
        BaseImporter.import_file(str(p))
    assert 'DTD' in str(exc.value)
    sp = BaseImporter.import_file(REF_SDB2)                  # a real WWB file still loads
    assert sp.sample_data.size > 1000
