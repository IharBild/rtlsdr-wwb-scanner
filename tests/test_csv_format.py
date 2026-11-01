"""Format checks for CSV export (plan step A1).

Reference: CSV files exported by Shure Wireless Workbench (AD600):
two columns, no header, frequency in kHz as an integer, level in dBm with
one decimal, step 25 kHz, one point per frequency, "\\n" line endings.
"""
import os

import numpy as np
import pytest

from wwb_scanner.scan_objects import Spectrum, SampleArray
from wwb_scanner.utils import dbmath

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reference')


def make_spectrum(freqs_mhz, magnitude):
    spectrum = Spectrum()
    spectrum.add_sample_set(
        frequency=np.asarray(freqs_mhz, dtype=np.float64),
        magnitude=np.asarray(magnitude, dtype=np.float64),
    )
    return spectrum


def export_csv(spectrum, tmpdir, name='out.csv', **kwargs):
    fn = str(tmpdir.join(name))
    spectrum.export_to_file(filename=fn, **kwargs)
    with open(fn, 'rb') as f:
        return fn, f.read()


def check_wwb_csv(raw):
    """Return a list of problems found in a WWB-style CSV (empty list = ok)."""
    problems = []
    if b'\r' in raw:
        problems.append('contains \\r')
    lines = raw.decode('ascii').split('\n')
    if lines and lines[-1] == '':
        problems.append('trailing newline / empty last line')
        lines = lines[:-1]
    freqs = []
    for ln in lines:
        parts = ln.split(',')
        if len(parts) != 2:
            problems.append('line is not two columns: %r' % ln)
            break
        f, v = parts
        if not f.isdigit():
            problems.append('frequency is not an integer kHz value: %r' % f)
            break
        if not np.isfinite(float(v)) or ('.' in v and len(v.split('.')[1]) > 1):
            problems.append('bad level value: %r' % v)
            break
        freqs.append(int(f))
    if len(freqs) > 1:
        d = np.diff(freqs)
        if not np.all(d == 25):
            problems.append('step is not exactly 25 kHz')
    return problems


def test_reference_file_passes_own_checker():
    with open(os.path.join(REF_DIR, 'wwb_ad600_head.csv'), 'rb') as f:
        raw = f.read()
    assert check_wwb_csv(raw) == []


def test_csv_written_without_newline_translation(tmpdir, monkeypatch):
    """On Windows text mode turns '\\r\\n' into '\\r\\r\\n' unless newline=''."""
    from wwb_scanner.file_handlers import exporters
    calls = []
    real_open = open

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(exporters, 'open', spy, raising=False)
    spectrum = make_spectrum([470.0, 470.025, 470.05], [1e-6, 2e-6, 3e-6])
    export_csv(spectrum, tmpdir)
    assert calls and calls[-1].get('newline') == ''


def test_csv_has_no_double_cr(tmpdir):
    spectrum = make_spectrum([470.0, 470.025, 470.05], [1e-6, 2e-6, 3e-6])
    _, raw = export_csv(spectrum, tmpdir)
    assert b'\r\r' not in raw


def test_to_dB_zero_is_finite():
    assert np.all(np.isfinite(dbmath.to_dB(np.array([0., 1e-12, 1.]))))


def test_csv_zero_power_is_written_as_no_data(tmpdir):
    spectrum = make_spectrum([470.0, 470.025], [1e-6, 0.])
    fn, raw = export_csv(spectrum, tmpdir)
    assert raw.decode('ascii').split('\r\n')[-1] == '470.025,-140.0'


def test_csv_has_no_inf_or_nan(tmpdir):
    spectrum = make_spectrum([470.0, 470.025, 470.05, 470.075], [1e-6, 0., 2e-6, 0.])
    _, raw = export_csv(spectrum, tmpdir)
    text = raw.decode('ascii').lower()
    assert 'inf' not in text and 'nan' not in text


def test_csv_roundtrip_still_works(tmpdir):
    freqs = np.arange(470., 470.5, 0.025)
    spectrum = make_spectrum(freqs, np.linspace(1e-6, 1e-3, freqs.size))
    fn, _ = export_csv(spectrum, tmpdir)
    imp = Spectrum.import_from_file(fn)
    assert np.allclose(imp.sample_data['frequency'], np.around(freqs, 3))


@pytest.mark.xfail(reason='A3: frequencies are compared exactly as floats', strict=False)
def test_nearly_equal_frequencies_are_merged():
    base = np.arange(470., 470.5, 0.025)
    a = SampleArray.create(frequency=base, magnitude=np.full(base.size, 1e-6))
    b = SampleArray.create(frequency=base + 1e-10, magnitude=np.full(base.size, 1e-6))
    a.insert_sorted(b)
    assert a.size == base.size


@pytest.mark.xfail(reason='A5: WWB-style export in kHz is not implemented yet', strict=False)
def test_khz_export_matches_wwb_format(tmpdir):
    freqs = np.around(np.arange(470., 471., 0.025), 3)
    spectrum = make_spectrum(freqs, np.linspace(1e-6, 1e-3, freqs.size))
    _, raw = export_csv(spectrum, tmpdir, frequency_format='khz')
    assert check_wwb_csv(raw) == []
