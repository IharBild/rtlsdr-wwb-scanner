# -*- coding: utf-8 -*-
"""Изолированные юнит-тесты критичной логики сканера (Этап 2 аудита).

Тесты не требуют реального RTL-SDR донгла: проверяются только «чистые»
функции — математика частот, расчёт сетки, слияние точек, конвертация dB,
маска «хороших» бинов и экспортная сетка WWB.

Автор: независимый аудит (не часть основного кода).
"""

import numpy as np
import pytest
from types import SimpleNamespace

from wwb_scanner.utils import dbmath
from wwb_scanner.scan_objects import SampleArray
from wwb_scanner.scanner.main import (
    mhz_to_hz,
    hz_to_mhz,
    get_freq_resolution,
    is_equal_spacing,
)
from wwb_scanner.scanner.sample_processing import (
    next_2_to_pow,
    calc_num_samples,
    clip_ratio,
    sort_psd,
    make_window,
    SampleSet,
)
from wwb_scanner.file_handlers.exporters import (
    clean_dB,
    wwb_grid,
    wwb_points,
    crc16_arc,
    dbFS_to_wsm_level,
    CSVExporter,
)


# --------------------------------------------------------------------------
# 1. Конвертация частот (MHz <-> Hz)
# --------------------------------------------------------------------------

def test_mhz_to_hz():
    assert mhz_to_hz(470.0) == 470000000.0
    assert mhz_to_hz(0.0) == 0.0
    assert mhz_to_hz(1766.0) == 1766000000.0


def test_hz_to_mhz():
    assert hz_to_mhz(470000000.0) == 470.0
    assert hz_to_mhz(0.0) == 0.0


def test_freq_roundtrip():
    for mhz in (0.0, 470.0, 536.125, 1766.0):
        assert np.isclose(hz_to_mhz(mhz_to_hz(mhz)), mhz)


# --------------------------------------------------------------------------
# 2. Расчёт разрешения и шага сетки частот
# --------------------------------------------------------------------------

def test_get_freq_resolution_exact():
    # 2.4 МГц / 96 бинов = 25 кГц ровно
    assert np.isclose(get_freq_resolution(96, 2400000.0), 25000.0)
    # 2.048 МГц / 128 бинов = 16 кГц ровно
    assert np.isclose(get_freq_resolution(128, 2048000.0), 16000.0)


def test_get_freq_resolution_is_positive():
    r = get_freq_resolution(96, 2400000.0)
    assert r > 0


def test_is_equal_spacing_true():
    # шаг ровно один бин -> сетка равномерная
    assert is_equal_spacing(96, 2400000.0, 25000.0) is True


def test_is_equal_spacing_false():
    # шаг, не кратный бину -> сетка неравномерная
    assert is_equal_spacing(96, 2400000.0, 12345.0) is False


# --------------------------------------------------------------------------
# 3. Математика в дБ (dbmath)
# --------------------------------------------------------------------------

def test_to_db_reference():
    # 10*log10(1) = 0
    assert np.isclose(dbmath.to_dB(1.0), 0.0)
    # 10*log10(0.01) = -20
    assert np.isclose(dbmath.to_dB(0.01), -20.0)


def test_from_db_roundtrip():
    for db in (-90.0, -50.0, 0.0, 10.0):
        assert np.isclose(dbmath.to_dB(dbmath.from_dB(db)), db)


def test_to_db_never_minus_inf():
    # логарифм нуля должен дать конечное (очень маленькое) число, а не -inf
    v = dbmath.to_dB(0.0)
    assert np.isfinite(v)
    assert v < -1000.0


def test_power_and_amplitude_consistency():
    # мощность: 10*log10, амплитуда: 20*log10
    assert np.isclose(dbmath.power_to_dB(0.01), -20.0)
    assert np.isclose(dbmath.amplitude_to_dB(0.1), -20.0)


# --------------------------------------------------------------------------
# 4. SampleArray: слияние точек, обрезка, поля
# --------------------------------------------------------------------------

def test_freq_keys_round_to_hz():
    # частоты, отличающиеся только шумом float, должны считаться равными
    keys = SampleArray._freq_keys(np.array([470.0, 470.0000001]))
    assert keys[0] == keys[1] == 470000000


def test_insert_sorted_merges_duplicates():
    a = SampleArray()
    a.set_fields(frequency=np.array([470.0, 470.025]), magnitude=np.array([1.0, 2.0]))
    b = SampleArray()
    b.set_fields(frequency=np.array([470.0, 470.05]), magnitude=np.array([3.0, 4.0]))

    a.insert_sorted(b)

    assert np.allclose(a.frequency, [470.0, 470.025, 470.05])
    # точка 470.0 измерена дважды: (1.0 + 3.0) / 2 = 2.0
    assert np.isclose(a['magnitude'][0], 2.0)
    assert np.isclose(a['dbFS'][0], dbmath.to_dB(2.0))


def test_insert_sorted_keeps_sorted():
    a = SampleArray()
    a.set_fields(frequency=np.array([500.0, 400.0]), magnitude=np.array([1.0, 2.0]))
    assert np.all(np.diff(a.frequency) > 0)  # отсортировано по возрастанию


def test_crop_is_inclusive():
    a = SampleArray()
    a.set_fields(frequency=np.array([100.0, 200.0, 300.0, 400.0]), magnitude=np.ones(4))
    a.crop(200.0, 300.0)
    assert np.allclose(a.frequency, [200.0, 300.0])


def test_set_fields_from_db():
    a = SampleArray()
    a.set_fields(frequency=470.0, dbFS=-50.0)
    assert np.isclose(a['dbFS'][0], -50.0)
    assert np.isclose(a['magnitude'][0], dbmath.from_dB(-50.0))


# --------------------------------------------------------------------------
# 5. Обработка сигнала: степени двойки, клиппинг, окно, маска бинов
# --------------------------------------------------------------------------

def test_next_2_to_pow():
    assert next_2_to_pow(1) == 1
    assert next_2_to_pow(2) == 2
    assert next_2_to_pow(3) == 4
    assert next_2_to_pow(100) == 128
    assert next_2_to_pow(8192) == 8192


def test_calc_num_samples_rounds_up():
    assert calc_num_samples(8192) == 8192
    assert calc_num_samples(10000) == 16384


def test_clip_ratio_empty_is_zero():
    assert clip_ratio(np.array([], dtype=np.complex128)) == 0.0


def test_clip_ratio_detects_clipping():
    no_clip = np.array([0.5 + 0.5j, 0.0 + 0.0j])
    assert clip_ratio(no_clip) == 0.0

    full_clip = np.array([1.0 + 0.0j, 0.0 + 1.0j])
    assert clip_ratio(full_clip) == 1.0


def test_sort_psd_fftshifts():
    f = np.array([-25.0, 0.0, 25.0])
    p = np.array([1.0, 2.0, 3.0])
    fo, po = sort_psd(f, p)
    # обе оси должны быть сдвинуты одинаково (fftshift), пара «частота–мощность» сохраняется
    assert np.array_equal(fo, np.fft.fftshift(f))
    assert np.array_equal(po, np.fft.fftshift(p))


def test_make_window_known():
    w = make_window('hann', 8)
    assert w.size == 8
    assert np.isclose(w.max(), 1.0)  # окно Ханна достигает 1 в середине


def test_make_window_falls_back_to_hann():
    # неизвестное имя окна не должно ронять программу
    w = make_window('definitely_not_a_window', 8)
    assert w.size == 8
    # None тоже должно давать окно Ханна
    w2 = make_window(None, 8)
    assert w2.size == 8


def _fake_scanner(keep=0.39, gap=30000.0):
    cfg = SimpleNamespace(tile_keep_ratio=keep, tile_center_gap_hz=gap)
    return SimpleNamespace(sampling_config=cfg)


def test_good_bins_mask_boundaries():
    ss = SampleSet(scanner=_fake_scanner())
    rs = 2400000.0
    # внутри: |offset| <= 0.39*2.4МГц = 936 кГц И |offset| > 30 кГц
    offsets = np.array([-1000000, -500000, 0, 10000, 40000, 500000, 900000])
    mask = ss.good_bins_mask(offsets, rs)
    assert mask.tolist() == [False, True, False, False, True, True, True]


def test_calc_expected_freqs_centered_on_tile():
    fake = SimpleNamespace(
        sample_rate=2400000.0,
        window_size=96,
        samples_per_sweep=2048,
        sweeps_per_scan=4,
        sampling_config=SimpleNamespace(sweep_overlap_ratio=0.65),
    )
    ss = SampleSet(scanner=fake, center_frequency=600.0e6)
    freqs = ss.calc_expected_freqs()

    assert freqs.size > 0
    assert np.all(np.diff(freqs) > 0)          # возрастают
    assert np.abs(freqs - 600.0).max() < 0.94  # внутри «удерживаемой» полосы тайла
    assert np.abs(freqs - 600.0).min() > 0.03  # в центре (DC) бины выброшены


# --------------------------------------------------------------------------
# 6. Экспорт в WWB: очистка дБ, сетка, CRC
# --------------------------------------------------------------------------

def test_clean_db_replaces_invalid():
    src = np.array([np.nan, np.inf, -np.inf, -2000.0, -95.5])
    out = clean_dB(src)
    # nan/inf и «нет сигнала» (ниже -1000 дБ) заменяются на -140
    assert np.allclose(out[:4], -140.0)
    assert np.isclose(out[4], -95.5)


def _spectrum(freqs_mhz, db):
    sa = SampleArray()
    sa.set_fields(frequency=np.asarray(freqs_mhz, float), dbFS=np.asarray(db, float))
    return SimpleNamespace(sample_data=sa)


def test_wwb_grid_whole_khz():
    grid, values = wwb_grid(_spectrum([470.0, 470.025, 470.05], [-90.0, -80.0, -70.0]))
    assert grid.tolist() == [470000, 470025, 470050]
    assert np.allclose(values, [-90.0, -80.0, -70.0])


def test_wwb_grid_clips_to_wwb_range():
    # ниже 30 МГц WWB не принимает -> обрезается
    grid, values = wwb_grid(_spectrum([20.0, 25.0, 30.0], [-100.0, -90.0, -80.0]))
    assert grid.tolist() == [30000]
    assert np.isclose(values[0], -80.0)


def test_wwb_points_regular_grid_passthrough():
    freqs, db, step = wwb_points(_spectrum([470.0, 470.025, 470.05], [-90.0, -80.0, -70.0]))
    assert freqs.tolist() == [470000, 470025, 470050]
    assert step == 25.0
    assert np.allclose(db, [-90.0, -80.0, -70.0])


def test_crc16_arc_known_vector():
    # эталонное значение CRC-16/ARC для строки "123456789"
    assert crc16_arc(b'123456789') == 0xBB3D


# --------------------------------------------------------------------------
# 7. WSM (Sennheiser) CSV format
# --------------------------------------------------------------------------

def test_dbFS_to_wsm_level_mapping():
    # relative to the noise floor (median), scaled x1.25
    assert np.allclose(dbFS_to_wsm_level(np.array([-100.0, -100.0, -100.0, -68.0])),
                       [0.0, 0.0, 0.0, 40.0])
    # a single point is its own noise floor -> 0; clipping at 48
    assert np.allclose(dbFS_to_wsm_level(np.array([-15.9])), [0.0])
    assert np.allclose(dbFS_to_wsm_level(np.array([-100.0, -100.0, -10.0])),
                       [0.0, 0.0, 48.0])


def _make_spectrum(freqs_mhz, db):
    from wwb_scanner.scan_objects import Spectrum
    sp = Spectrum()
    sp.add_sample_set(frequency=np.asarray(freqs_mhz, float), dbFS=np.asarray(db, float),
                      force_lower_freq=True)
    return sp


def test_wsm_export_format():
    sp = _make_spectrum([470.0, 470.025, 470.05], [-99.0, -16.0, -99.0])
    ex = CSVExporter(spectrum=sp, frequency_format='wsm')
    s = ex.build_wsm_data()
    assert '\r\n' in s                       # WSM uses CRLF line endings
    lines = s.split('\r\n')
    assert lines[0].startswith('Receiver;')
    assert lines[2] == 'RFUnit;dB;MinimumRFValue;0'
    assert lines[3] == 'Squelch;Supported'
    assert lines[4] == ''
    assert lines[5].startswith('Frequency Range [kHz];')
    assert lines[6] == 'Frequency;RF level (%);RF level;Memory (%);Memory;Squelch (%);Squelch'
    row = lines[7].split(';')
    assert row[0] == '470000'
    assert len(row) == 7
    assert row[2] == '0'                       # -99 dBFS (noise) -> 0
    assert row[5] == '12.5' and row[6] == '5'  # squelch: noise + 5 dB -> 5 dB / 12.5%
    assert 'Marker;1' in lines
    assert 'Scales;7' in lines
    assert '100;40' in lines


def test_wsm_export_import_roundtrip(tmpdir):
    from wwb_scanner.file_handlers.importers import BaseImporter
    sp = _make_spectrum([470.0, 470.025, 470.05], [-99.0, -16.0, -99.0])
    fn = str(tmpdir.join('wsm.csv'))
    sp.export_to_file(filename=fn, frequency_format='wsm')
    back = BaseImporter.import_file(fn)
    assert np.allclose(np.asarray(back.sample_data.frequency, float), [470.0, 470.025, 470.05])
    # the strong point stays the strongest after the 0..48 mapping round-trip
    assert int(np.argmax(np.asarray(back.sample_data['dbFS'], float))) == 1


# --------------------------------------------------------------------------
# 8. HDR merge
# --------------------------------------------------------------------------

def test_merge_hdr():
    from wwb_scanner.scan_objects.spectrum import merge_hdr
    from wwb_scanner.scan_objects import Spectrum
    # high gain: mic distorted, spurs present, weak TV visible
    high = Spectrum()
    high.add_sample_set(frequency=np.array([635.225, 640.55, 643.475, 646.15]),
                        dbFS=np.array([-55.0, -15.0, -40.0, -68.0]), force_lower_freq=True)
    # low gain: mic clean, spurs gone, weak TV lost in the noise
    low = Spectrum()
    low.add_sample_set(frequency=np.array([635.225, 640.55, 643.475, 646.15]),
                       dbFS=np.array([-105.0, -49.0, -105.0, -105.0]), force_lower_freq=True)
    merged = merge_hdr(high, low)
    y = np.asarray(merged.sample_data['dbFS'], float)
    # mic (640.55) clean from low gain; spurs below (-5.3 MHz) and above
    # (+2.9 MHz) suppressed from low gain; TV (+5.6 MHz) kept from high gain
    assert np.allclose(y, [-105.0, -49.0, -105.0, -68.0])
