"""V19: Sennheiser WSM scan files (export for WSM, import of receiver scans).

The layout is the one WSM writes for EM 300-500 G3 receiver scans (checked
against files saved by WSM on 06-08.11.2026)."""
import datetime
import io

import numpy as np
import pytest

from wwb_scanner.file_handlers import exporters as E
from wwb_scanner.file_handlers.importers import BaseImporter
from wwb_scanner.scan_objects.spectrum import Spectrum

HEAD = ['Receiver;', 'Date/Time;', 'RFUnit;dB;MinimumRFValue;0', 'Squelch;Supported', '',
        'Frequency Range [kHz];', 'Frequency;RF level (%);RF level;Memory (%);Memory;Squelch (%);Squelch']
TAIL = ['', 'Marker;0', 'Index;Comment', '', 'Scales;7', 'Level (%);Level (units)',
        '25;10', '37.5;15', '50;20', '62.5;25', '75;30', '87.5;35', '100;40', '']

# a receiver scan in the "06.11" layout (WSM scan export with project fields)
WSM_A = ('Receiver;ew300 G3\r\nDate/Time;06-11-2026 09-09-29 PM\r\nRFUnit;dB\r\nOwner;\r\nScanCity;\r\n'
         'ScanComment;\r\nScanCountry;\r\nScanDescription;\r\nScanInteriorExterior;\r\nScanLatitude;\r\n'
         'ScanLongitude;\r\nScanName;test\r\nScanPostalCode;\r\n\r\nFrequency Range [kHz];626000;626100;25\r\n'
         'Frequency;RF level (%);RF level\r\n626000;;6\r\n626025;;8\r\n626050;;30\r\n626075;;7\r\n626100;;6\r\n')


def _spectrum(f_mhz, db, name='scan'):
    sp = Spectrum()
    sp.name = name
    sp.add_sample_set(frequency=np.asarray(f_mhz, float), dbFS=np.asarray(db, float))
    return sp


def _band(seed=1):
    f = 626 + np.arange(1681) * 0.025                      # 626-668 MHz
    rng = np.random.RandomState(seed)
    y = -100 + rng.randn(f.size) * 0.7
    y[(f >= 646) & (f < 654)] += 20                       # a TV multiplex
    y[np.abs(f - 640.55) < 0.03] = -40                    # a mic
    return f, y


def test_W1_layout_like_wsm(tmpdir):
    f, y = _band()
    p = str(tmpdir.join('w.csv'))
    _spectrum(f, y).export_to_file(filename=p, frequency_format='wsm')
    raw = open(p, newline='').read()
    assert '\n' not in raw.replace('\r\n', '') and raw.endswith('\r\n')
    lines = raw.split('\r\n')
    for i, want in enumerate(HEAD):
        assert lines[i].startswith(want) if want.endswith(';') else lines[i] == want, (i, lines[i])
    assert lines[5] == 'Frequency Range [kHz];626000;668000;25'
    rows = lines[7:7 + 1681]
    assert lines[7 + 1681:] == TAIL
    k = [int(r.split(';')[0]) for r in rows]
    assert k == list(range(626000, 668001, 25))
    for r in rows:
        c = r.split(';')
        assert len(c) == 7 and c[3] == c[4] == '' and c[5] == '12.5' and c[6] == '5'
        lv = int(c[2])
        assert 0 <= lv <= 48 and float(c[1]) == min(100.0, lv * 2.5)


def test_W2_levels_floor_tv_and_mic(tmpdir):
    f, y = _band()
    grid, lv, floor = E.wsm_levels(_spectrum(f, y))
    assert abs(floor - (-100.6)) < 0.5                   # 20th percentile, not the TV mux
    tv = lv[(grid >= 646000) & (grid < 654000)]
    assert 22 <= np.median(tv) <= 30                      # 20 dB above floor x 1.3
    assert lv[np.argmin(np.abs(grid - 640550))] == 48    # the mic is clipped at the top
    assert np.median(lv[grid > 655000]) <= 2


def test_W3_gaps_of_several_ranges_are_zero_rows(tmpdir):
    f1 = 626 + np.arange(400) * 0.025
    f2 = 650 + np.arange(400) * 0.025
    y = np.r_[np.full(400, -80.), np.full(400, -80.)]
    grid, lv, floor = E.wsm_levels(_spectrum(np.r_[f1, f2], y + np.r_[np.zeros(399), 10, np.zeros(400)]))
    assert grid[0] == 626000 and np.all(np.diff(grid) == 25)       # every row present
    gap = (grid > 636100) & (grid < 649900)
    assert gap.any() and np.all(lv[gap] == 0)


def test_W4_time_without_locale():
    assert E.wsm_time(datetime.datetime(2026, 11, 8, 14, 44, 57)) == '08-11-2026 02-44-57 PM'
    assert E.wsm_time(datetime.datetime(2026, 11, 7, 0, 5, 1)) == '07-11-2026 12-05-01 AM'


def test_W5_wwb_csv_unchanged(tmpdir):
    f, y = _band()
    p = str(tmpdir.join('wwb.csv'))
    _spectrum(f, y).export_to_file(filename=p, frequency_format='khz')
    first = open(p, newline='').read().split('\n')[0]
    assert first.startswith('626000,') and ';' not in first


def test_W6_import_our_and_receiver_files(tmpdir):
    f, y = _band()
    p = str(tmpdir.join('ours.csv'))
    _spectrum(f, y, name='venue').export_to_file(filename=p, frequency_format='wsm')
    sp = BaseImporter.import_file(p)
    g = sp.sample_data
    assert g.size == 1681 and abs(g.frequency[0] - 626.0) < 1e-9 and abs(g.frequency[-1] - 668.0) < 1e-9
    i_tv = (g.frequency >= 646) & (g.frequency < 654)
    assert np.median(g['dbFS'][i_tv]) - np.median(g['dbFS'][g.frequency > 655]) > 15
    q = str(tmpdir.join('receiver_a.csv'))
    with io.open(q, 'w', newline='') as fh:
        fh.write(WSM_A)
    sa = BaseImporter.import_file(q)
    assert sa.sample_data.size == 5 and 'WSM' in sa.name and 'ew300 G3' in sa.name
    assert np.argmax(sa.sample_data['dbFS']) == 2


def test_W7_damaged_wsm_file(tmpdir):
    q = str(tmpdir.join('bad.csv'))
    with io.open(q, 'w', newline='') as fh:
        fh.write('Receiver;x\r\nDate/Time;1\r\n')
    with pytest.raises(ValueError):
        BaseImporter.import_file(q)


def test_W8_empty_scan_refused(tmpdir):
    with pytest.raises(ValueError):
        _spectrum([], []).export_to_file(filename=str(tmpdir.join('e.csv')), frequency_format='wsm')
