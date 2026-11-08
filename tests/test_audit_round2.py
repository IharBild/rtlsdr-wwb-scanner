# -*- coding: utf-8 -*-
"""Тесты второго аудита (Этап 2). Нацелены на найденные в отчёте места.

Часть тестов ОЖИДАЕМО падает: они фиксируют проблемы, найденные на Этапе 1,
до их исправления на Этапе 3 (TDD). Каждый падающий тест помечен, к какому
пункту AUDIT_REPORT.md он относится.
"""

import threading

import numpy as np
import pytest

from wwb_scanner.scanner.main import get_freq_resolution
from wwb_scanner.scan_objects.spectrum import merge_hdr
from wwb_scanner.scan_objects import Spectrum


def _make_spectrum(freqs_mhz, db):
    sp = Spectrum()
    sp.add_sample_set(frequency=np.asarray(freqs_mhz, float),
                      dbFS=np.asarray(db, float), force_lower_freq=True)
    return sp


# --------------------------------------------------------------------------
# Пункт 1 отчёта: импорт WSM не обратен нашему экспорту WSM.
# Уровень после экспорта+импорта должен сохраняться (хотя бы примерно).
# --------------------------------------------------------------------------

def test_wsm_roundtrip_relative_levels(tmpdir):
    """Отношение «сигнал над шумом» должно пережить цикл WSM экспорт->импорт."""
    from wwb_scanner.file_handlers.importers import BaseImporter

    # шум -99 дБ, слабый сигнал на 19 дБ выше (-80)
    sp = _make_spectrum([470.0, 470.025, 470.05], [-99.0, -80.0, -99.0])
    fn = str(tmpdir.join('wsm_roundtrip.csv'))
    sp.export_to_file(filename=fn, frequency_format='wsm')

    back = BaseImporter.import_file(fn)
    fb = np.asarray(back.sample_data.frequency, float)
    yb = np.asarray(back.sample_data['dbFS'], float)

    # частоты обязаны вернуться точно
    assert np.allclose(fb, [470.0, 470.025, 470.05])

    # сигнал должен остаться примерно на 19 дБ над шумовым полом
    gap = float(yb[1] - min(yb[0], yb[2]))
    assert abs(gap - 19.0) < 6.0, (
        'сигнал должен быть ~19 дБ над шумом, а стал на %s дБ (импорт не обратен экспорту)' % gap
    )


# --------------------------------------------------------------------------
# Пункт 8 отчёта: get_freq_resolution при вырожденном nfft=1 даёт NaN.
# --------------------------------------------------------------------------

def test_get_freq_resolution_single_bin_not_nan():
    r = get_freq_resolution(1, 2.4e6)
    assert np.isfinite(r), 'nfft=1 не должен давать NaN, а вернул %r' % r


# --------------------------------------------------------------------------
# Регрессионный тест (должен ПРОХОДИТЬ): склейка пустых спектров не падает.
# --------------------------------------------------------------------------

def test_merge_hdr_empty_spectra():
    merged = merge_hdr(Spectrum(), Spectrum())
    assert merged.sample_data['frequency'].size == 0


# --------------------------------------------------------------------------
# Пункт 3 отчёта: в run_hdr_scan значение hdr_gain_drop_db=0 подменяется на 48
# (оператор `or`). При сбросе 0 усиление не должно снижаться.
# --------------------------------------------------------------------------

def test_hdr_gain_drop_zero_not_replaced(monkeypatch, tmpdir):
    import sim
    from wwb_scanner.scanner import sdrwrapper, main
    from wwb_scanner import scan_dll
    from wwb_scanner.utils import dbstore

    air = sim.Air(noise=0.01).tone(500.0e6, 1.0)
    made = []

    def factory(serial_number=None):
        s = sim.FakeRtlSdr(serial_number=serial_number, air=air, gain_affects=True)
        made.append(s)
        return s

    monkeypatch.setattr(sdrwrapper, 'RtlSdr', factory)
    monkeypatch.setitem(scan_dll._cache, 'lib', None)
    monkeypatch.setattr(dbstore.DBStore, 'DB_PATH', str(tmpdir.join('db.json')))
    monkeypatch.setattr(dbstore.DBStore, 'SCAN_DB_PATH', str(tmpdir.join('scan_db.json')))
    monkeypatch.setattr(dbstore.db_store, '_db', None)
    monkeypatch.setattr(dbstore.db_store, '_scan_db', None)
    monkeypatch.setenv('HOME', str(tmpdir))
    monkeypatch.setenv('USERPROFILE', str(tmpdir))

    scanner = main.Scanner(config=sim.make_config(495, 505, gain=29.7,
                                                  hdr_enabled=True, hdr_gain_drop_db=0.0))
    th = threading.Thread(target=scanner.run_hdr_scan, daemon=True)
    th.start()
    th.join(120)
    assert not th.is_alive(), 'HDR scan did not finish in 120 s'

    all_gains = [g for s in made for g in s.gains_set if g is not None]
    assert all_gains, 'the dongle gain was never set'
    assert min(all_gains) > 10.0, (
        'drop 0 must not lower the gain below 29.7, but the gain was set to %s '
        '(0 was replaced by 48)' % all_gains
    )
