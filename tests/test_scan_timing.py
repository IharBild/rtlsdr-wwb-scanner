import os

from wwb_scanner.scanner.scan_timing import ScanTiming, STAGES


def fake_tile(timing, key, t0, parts):
    t = t0
    timing.tiles[key] = {}
    timing.tiles[key]['start'] = t
    for stage, d in zip(STAGES[1:], parts):
        t += d
        timing.tiles[key][stage] = t
    return t


def test_report_contains_stages_and_totals(tmpdir):
    timing = ScanTiming()
    t = 0.
    for i in range(10):
        t = fake_tile(timing, 470e6 + i * 1e6, t, (0.05, 0.10, 0.02, 0.01, 0.30)) + 0.01
    report = timing.build_report()
    assert 'tiles measured: 10' in report
    assert 'between tiles' in report
    path = timing.write_report(str(tmpdir.join('t.txt')))
    assert os.path.exists(path)
    assert 'Scan timing' in open(path).read()


def test_incomplete_tiles_are_skipped_and_no_crash():
    timing = ScanTiming()
    timing.tick(1.0, 'start')
    assert 'no complete tiles' in timing.build_report()
    assert timing.write_report('/nonexistent_dir/x.txt') is None
