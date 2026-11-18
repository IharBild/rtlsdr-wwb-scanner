"""V21.4.1: small items - the peak check in the console (time, every peak),
tuner I2C counters just before the dongle is closed."""
import sim
from wwb_scanner.scanner import sdrwrapper
from test_v212 import _scan


def test_peak_check_time_and_list(monkeypatch, tmpdir, capsys):
    sc, sdr, _ = _scan(monkeypatch, tmpdir)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if l.startswith('peak check: 2 product(s)')][0]
    assert line.endswith(' s)') and '(' in line, line
    assert sc.sample_collection.peak_check_seconds >= 0
    lst = [l for l in out.splitlines() if l.startswith('peaks checked: ')]
    assert len(lst) == 1, out
    items = lst[0][len('peaks checked: '):].split('; ')
    assert len(items) == 3
    assert items[0].startswith('599.600 +') and ' product (' in items[0], items
    assert items[1].startswith('600.800 +') and ' product (' in items[1], items
    assert items[2].startswith('601.500 +') and ' real' in items[2], items


def test_i2c_counters_before_close(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(sdrwrapper.scan_dll, 'i2c_stats', lambda sdr: seen.append(sdr) or (3, 0))
    assert sdrwrapper.report_i2c_at_close('dev') == (3, 0)
    assert 'tuner I2C before close: 3 transfer(s) retried, 0 failed' in capsys.readouterr().out
    monkeypatch.setattr(sdrwrapper.scan_dll, 'i2c_stats', lambda sdr: (0, 0))
    sdrwrapper.report_i2c_at_close('dev')
    assert capsys.readouterr().out == ''                       # nothing to tell
    monkeypatch.setattr(sdrwrapper.scan_dll, 'i2c_stats', lambda sdr: None)
    assert sdrwrapper.report_i2c_at_close('dev') is None        # old DLL


def test_close_sdr_reports_before_closing(monkeypatch):
    order = []

    class Dev(object):
        def close(self):
            order.append('close')
    monkeypatch.setattr(sdrwrapper, 'report_i2c_at_close', lambda sdr: order.append('report'))
    w = sdrwrapper.SdrWrapper.__new__(sdrwrapper.SdrWrapper)
    import threading
    w.device_lock = threading.RLock()
    w.device_open = threading.Event()
    w.sdr = Dev()
    w.close_sdr()
    assert order == ['report', 'close'] and w.sdr is None
