"""V20.4: findings of the PC runs of 09.11.2026 (qa_pc 11:55, mic series,
scans without mic).

- B: dips of a DVB-T multiplex deeper than the plateau of its zone were
  still reported as lost sensitivity (643-654 MHz, "signal 646.850");
- C: tiles clipped even at the lowest gain are named "not measurable" and
  drawn as such (the data is kept: WWB keeps away from those frequencies);
- D: one tile hung 458 s in qa_pc H4 (every USB call just under its
  timeout), then LIBUSB_ERROR_IO and "dongle not found or busy" for the
  next 4 scans: a time budget per tile, the dongle is opened again after a
  USB error and a new scan waits for it;
- F: the overload line named only the first 6 zones.

The tests fail on V20.3 (checked). The safety ones (marked) check what
must stay as it was; on V20.3 they cannot run (they use the new names).
"""
import threading
import time
import types

import numpy as np
import pytest

import sim
from sim import Fault
from test_v202 import MIC, _SD, _scan
from wwb_scanner.scanner import sample_processing as sp


# --- B: dips of a wide signal --------------------------------------------

def _tv_collection(f, y, w, plateau):
    sets = {}
    for k in np.arange(640e6, 662e6, 0.775e6):
        sets[float(k)] = types.SimpleNamespace(powers=np.full(64, 1e-10), powers_low=None, powers_orig=None,
                                               carrier_hz=None, carrier_db=None, low_gain=None,
                                               neighbour=False, desense_db=0.)
    k0 = min(sets, key=lambda k: abs(k - 646.85e6))
    sets[k0].powers_low = np.full(64, 1e-10)
    sets[k0].carrier_hz, sets[k0].carrier_db, sets[k0].low_gain = 646.85e6, -60., 43.9
    c = object.__new__(sp.SampleCollection)
    c.sample_sets = sets
    c.scanner = types.SimpleNamespace(sample_rate=2.4e6, config=types.SimpleNamespace(scan_range=[640., 662.]),
                                      spectrum=types.SimpleNamespace(data_update_lock=threading.RLock(),
                                                                     sample_data=_SD(f, y, w)))
    zone = c.carrier_zones()[0]
    c.zone_floors = [(zone, plateau)]
    return c


def test_dips_under_the_plateau_inside_a_multiplex_are_not_lost():
    # like the user's scan without mic: dips of the multiplex at +7 dB over
    # the floor, under the plateau of the zone (V20.2 counted them)
    f = np.round(np.arange(640.0, 662.0, 0.025), 3)
    y = np.full(f.size, -92.)
    w = np.ones(f.size)
    tv = (f > 646) & (f < 654)
    y[tv] = -70.
    dips = tv & ((np.abs(f - 650.4) < 0.15) | (np.abs(f - 652.9) < 0.3))
    y[dips] = -85.
    w[dips] = 0.01
    lost = np.abs(f - 643.6) < 0.2            # outside the multiplex: really lost
    y[lost] = -83.
    w[lost] = 0.01
    c = _tv_collection(f, y, w, plateau=-83.)
    loss = c.sensitivity_loss()
    assert any(lo <= 643.6 <= hi for lo, hi, _, _ in loss), loss
    assert not any(lo <= 650.4 <= hi or lo <= 652.9 <= hi for lo, hi, _, _ in loss), loss


def test_plateau_next_to_a_mic_is_not_a_wide_signal():
    # safety (passes on V20.3 too): points known only as "not above the
    # plateau" (+35 dB) are not a wide signal, their loss stays reported
    f = np.round(np.arange(626.0, 640.0, 0.025), 3)
    y = np.full(f.size, -96.)
    w = np.ones(f.size)
    zone = (f > 630.5) & (f < 635.5)
    y[zone] = -61.
    w[zone] = 0.01
    y[np.abs(f - MIC) < 0.05] = -20.
    w[np.abs(f - MIC) < 0.05] = 1.
    assert not sp.wide_signal_mask(f, y, w, -96.)[zone].any()


# --- C: not measurable -----------------------------------------------------

def test_tiles_clipped_at_the_lowest_gain_are_not_measurable(monkeypatch, tmpdir):
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8, mic=300.)
    coll = sc.sample_collection
    assert coll.unmeasurable, coll.overload_note
    lo, hi, r = coll.unmeasurable[0]
    assert lo < MIC < hi and r >= sp.OVERLOAD_RATIO
    marks = sc.spectrum.unmeasurable_bands
    assert marks and marks[0]['lo'] == pytest.approx(lo) and marks[0]['hi'] == pytest.approx(hi)
    f, y = sim.spectrum_arrays(sc)
    assert y[np.abs(f - MIC) < 0.05].max() > -60.     # the data itself is kept


def test_a_mic_that_auto_gain_can_handle_is_measurable(monkeypatch, tmpdir):
    # safety (passes on V20.3 too)
    sc, sdr, dt = _scan(monkeypatch, tmpdir, 33.8, mic=20.)
    assert sc.sample_collection.unmeasurable == []
    assert sc.spectrum.unmeasurable_bands == []


# --- F: all zones named ------------------------------------------------------

def test_overload_line_says_how_many_zones_more():
    spans = [(100. + i, 100.5 + i, 0.1) for i in range(9)]
    text = sp.SampleCollection._mhz_list(spans)
    assert text.endswith('and 3 more') and text.count('-') == 6
    assert sp.SampleCollection._mhz_list(spans[:2]) == '100.0-100.5, 101.0-101.5'


# --- D: USB ----------------------------------------------------------------

def test_a_tile_that_takes_too_long_stops_the_scan(monkeypatch, tmpdir):
    # every read answers, but slowly (the 458 s tile of qa_pc H4)
    monkeypatch.setattr(sp, 'TILE_BUDGET_S', 0.3)
    sc, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 480,
                               sdr_kwargs={'air': sim.Air(), 'read_delay': 0.2})
    assert sc.sample_collection.aborted
    assert dt < 5.
    assert sc.error is None


def test_dongle_opened_again_after_a_usb_error(monkeypatch, tmpdir):
    monkeypatch.setattr(sp, 'REOPEN_PAUSE_S', 0.05)
    fault = Fault('set_center_freq', 'close', None)
    fault.when = lambda n, f: n == 7 and fault.hits == 0     # one error, like qa_pc H4
    made = []

    class Counting(sim.FakeRtlSdr):
        def __init__(self, *a, **kw):
            super(Counting, self).__init__(*a, **kw)
            made.append(self)
    monkeypatch.setattr(sim, 'FakeRtlSdr', Counting)
    sc, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 480,
                               sdr_kwargs={'air': sim.Air(), 'faults': [fault]})
    coll = sc.sample_collection
    assert fault.hits == 1 and len(made) == 2
    assert not coll.aborted and not coll.missing_tiles
    assert sum(s.closed_calls for s in made) == 0      # the closed handle was never used again
    f, y = sim.spectrum_arrays(sc)
    assert f.min() <= 470.0 and f.max() >= 480.0


def test_second_usb_error_still_stops_the_scan(monkeypatch, tmpdir):
    # safety: at most one re-open per scan, then it stops as before
    monkeypatch.setattr(sp, 'REOPEN_PAUSE_S', 0.05)
    fault = Fault('set_center_freq', 'close', lambda n, f: n == 3)
    sc, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 480,
                               sdr_kwargs={'air': sim.Air(), 'faults': [fault]})
    assert sc.sample_collection.aborted and fault.hits == 2
    assert sdr.closed_calls == 0


def test_new_scan_waits_for_the_dongle(monkeypatch, tmpdir):
    # "dongle not found or busy" for a few seconds after the USB error
    monkeypatch.setattr(sp, 'REOPEN_PAUSE_S', 0.1)
    tries = []

    class Late(sim.FakeRtlSdr):
        def __init__(self, *a, **kw):
            tries.append(time.time())
            if len(tries) <= 3:
                raise IOError('No devices found')
            super(Late, self).__init__(*a, **kw)
    monkeypatch.setattr(sim, 'FakeRtlSdr', Late)
    sc, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 472, sdr_kwargs={'air': sim.Air()})
    assert sc.error is None and len(tries) == 4
    f, y = sim.spectrum_arrays(sc)
    assert f.size > 0


def test_no_dongle_still_ends_with_an_error(monkeypatch, tmpdir):
    # safety: without a dongle the scan ends with the message, after the wait
    monkeypatch.setattr(sp, 'REOPEN_WAIT_S', 0.3)
    monkeypatch.setattr(sp, 'REOPEN_PAUSE_S', 0.1)
    sc, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 472, sdr_kwargs={'fail_open': True})
    assert sc.error is not None and 'not found' in str(sc.error)
    assert dt < 5.
