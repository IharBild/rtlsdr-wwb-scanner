"""V22.4: calibrations of each dongle (wwb_scanner/dongles.py).

Before V22.4 one set of calibrations was used for every dongle; a second
dongle got the floor shape and the gain steps of the first one."""
import json
import logging
import os

import numpy as np
import pytest

from wwb_scanner import dongles, paths
from wwb_scanner.scanner import cleanup, gain_cal, gain_profile

import sim

LEGACY = ('dongle_gain33.8_fs2400k.npz', 'dongle_gain33.8_fs2400k.prev.npz',
          'gain_steps_fs2400k.json', 'gain_profile.json')


@pytest.fixture
def data(tmpdir):
    paths.set_data_dir(str(tmpdir.join('data')))
    yield tmpdir.join('data')
    paths.set_data_dir(None)


def _root():
    return paths.calibration_root(create=True)


def _profile(floor=-101.):
    f = np.linspace(31., 1766., 4000)
    y = np.full(f.size, floor)
    prof = cleanup.build_profile(f, y, 31., int(round(2.4 * 0.35 / np.median(np.diff(f)))),
                                 meta={'gain': 33.8, 'sample_rate': 2.4e6, 'created': '2026-11-18 13:52:24'})
    return prof


def _make_legacy():
    root = _root()
    cleanup.save_profile(_profile(), os.path.join(root, LEGACY[0]))
    cleanup.save_profile(_profile(-100.), os.path.join(root, LEGACY[1]))
    for n in LEGACY[2:]:
        with open(os.path.join(root, n), 'w') as fh:
            json.dump({'old': n}, fh)
    with open(os.path.join(root, 'notes.txt'), 'w') as fh:
        fh.write('not a calibration')


def test_folder_names_are_safe():
    assert dongles.key('00000001') == 'sn-00000001'
    assert dongles.key(' 0000 0001/..\\x ') == 'sn-0000_0001_.._x'
    assert dongles.key('') == dongles.key(None) == 'no-serial'
    assert len(dongles.key('9' * 200)) == 3 + 64


def test_no_dongle_known_uses_the_old_folder(data):
    """As before V22.4, until a dongle is seen (also the files of the core tests)."""
    assert dongles.active() is None
    assert paths.calibration_dir() == paths.calibration_root() == str(data.join('calibration'))
    assert os.path.dirname(cleanup.profile_path(33.8, 2.4e6)) == paths.calibration_root()


def test_each_dongle_has_its_own_folder(data):
    dongles.use('00000001')
    a = paths.calibration_dir()
    assert a == str(data.join('calibration', 'sn-00000001'))
    for p in (cleanup.profile_path(33.8, 2.4e6), gain_cal.table_path(2.4e6), gain_profile.profile_path()):
        assert os.path.dirname(p) == a
    dongles.use('A1B2')
    assert paths.calibration_dir() == str(data.join('calibration', 'sn-A1B2'))


def test_first_dongle_after_the_update_gets_the_old_calibrations(data, caplog):
    _make_legacy()
    with caplog.at_level(logging.INFO, logger='wwb_scanner.status'):
        n = dongles.use('00000001')
    assert n == len(LEGACY)
    d = dongles.folder('00000001')
    assert sorted(os.listdir(d)) == sorted(LEGACY)              # notes.txt is not a calibration
    # copied, not moved: V22.3 (rollback) still finds them
    assert all(os.path.exists(os.path.join(paths.calibration_root(), n)) for n in LEGACY)
    assert 'now belong to the dongle with serial number 00000001' in caplog.text
    prof, why = cleanup.find_profile(33.8, 2.4e6)
    assert prof is not None and not why and prof['floor_ref_db'] == pytest.approx(-101., abs=0.5)
    reg = dongles.load_registry()
    assert reg['legacy']['state'] == 'bound' and reg['legacy']['to'] == 'sn-00000001'
    # once only: a later start copies nothing
    dongles.reset()
    assert dongles.use('00000001') == 0


def test_a_second_dongle_does_not_get_the_first_ones_calibration(data):
    _make_legacy()
    dongles.use('00000001')
    assert cleanup.find_profile(33.8, 2.4e6)[0] is not None
    assert dongles.use('00000002') == 0
    prof, why = cleanup.find_profile(33.8, 2.4e6)
    assert prof is None and 'no calibration for gain 33.8 dB' in why
    assert os.listdir(dongles.folder('00000002', create=True)) == []
    # back to the first one: its calibration again
    dongles.use('00000001')
    assert cleanup.find_profile(33.8, 2.4e6)[0] is not None


def test_two_dongles_known_before_the_old_files_are_bound(data, caplog):
    """Two dongles seen before (another data folder copied in): it is not clear
    whose the old files were - none of them gets them."""
    _make_legacy()
    reg = dongles.load_registry()
    reg['dongles'] = {'sn-X': {'serial': 'X'}}
    dongles._save_registry(reg)
    with caplog.at_level(logging.WARNING, logger='wwb_scanner.status'):
        assert dongles.use('Y') == 0
    assert 'are not used' in caplog.text
    assert dongles.load_registry()['legacy']['state'] == 'unused'
    assert cleanup.find_profile(33.8, 2.4e6)[0] is None


def test_the_last_dongle_is_remembered(data):
    dongles.use('00000007')
    dongles.reset()                     # a new start of the program
    assert dongles.active() == '00000007'
    assert paths.calibration_dir().endswith('sn-00000007')
    assert [s for s, _ in dongles.known()] == ['00000007']


def test_another_data_folder_forgets_the_active_dongle(tmpdir):
    paths.set_data_dir(str(tmpdir.join('one')))
    try:
        dongles.use('00000001')
        paths.set_data_dir(str(tmpdir.join('two')))
        assert dongles.active() is None
    finally:
        paths.set_data_dir(None)


class _Cls(object):
    def __init__(self, serials):
        self.serials = serials
    def get_device_serial_addresses(self):
        if isinstance(self.serials, Exception):
            raise self.serials
        return list(self.serials)


def test_resolve_which_dongle_opens(caplog):
    one = _Cls(['00000001'])
    assert dongles.resolve(one, None, None) == ('00000001', {'serial_number': None})
    assert dongles.resolve(one, '00000001', 0) == ('00000001', {'serial_number': '00000001'})
    two = _Cls(['AAA', 'BBB'])
    assert dongles.resolve(two, None, 1) == ('BBB', {'device_index': 1})
    assert dongles.resolve(two, 'BBB', None) == ('BBB', {'serial_number': 'BBB'})
    # serial numbers can not be listed: as before V22.4
    assert dongles.resolve(_Cls(IOError('busy')), 'Q', None) == ('Q', {'serial_number': 'Q'})
    assert dongles.resolve(object(), None, None) == (None, {'serial_number': None})


def test_two_dongles_with_one_serial(caplog):
    """Opening by serial would always give the first one: the chosen index is
    used, and the user is told once."""
    same = _Cls(['00000001', '00000001'])
    with caplog.at_level(logging.WARNING, logger='wwb_scanner.status'):
        assert dongles.resolve(same, '00000001', 1) == ('00000001', {'device_index': 1})
        assert dongles.resolve(same, '00000001', 0) == ('00000001', {'device_index': 0})
    assert caplog.text.count('2 dongles have the same serial number 00000001') == 1
    assert 'rtl_eeprom -s' in caplog.text
    assert dongles.duplicates(['a', 'b', 'a', 'a', 'b', 'c']) == ['a', 'b']


def test_a_scan_uses_and_records_the_dongle_it_opened(monkeypatch, tmpdir):
    """Through the real scanner: the dongle opened is the active one, its
    serial number goes with the scan (cleanup info, saved in the history)."""
    paths.set_data_dir(str(tmpdir.join('data')))
    try:
        scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 480., serials=['SIM-7'],
                                       clean={'clean_ripple': True})
        assert scanner.error is None if hasattr(scanner, 'error') else True
        assert dongles.active() == 'SIM-7'
        assert scanner.sdr_wrapper.serial == 'SIM-7'
        assert scanner.receiver is None or scanner.receiver.serial == 'SIM-7'
        assert scanner.spectrum.cleanup_info.get('dongle') == 'SIM-7'
        assert sdr.opened_with == {'serial_number': None, 'device_index': None}
        assert paths.calibration_dir().endswith('sn-SIM-7')
    finally:
        paths.set_data_dir(None)


def test_the_chosen_one_of_two_same_serial_dongles_opens(monkeypatch, tmpdir):
    paths.set_data_dir(str(tmpdir.join('data')))
    try:
        from wwb_scanner.scanner import config as cfgmod
        orig = sim.make_config
        def make_config(*a, **k):
            c = orig(*a, **k)
            c['device'].update(serial_number='00000001', device_index=1)
            return c
        monkeypatch.setattr(sim, 'make_config', make_config)
        scanner, sdr, _ = sim.run_scan(monkeypatch, tmpdir, 470., 475., serials=['00000001', '00000001'])
        assert sdr.opened_with == {'serial_number': None, 'device_index': 1}
        assert cfgmod.DeviceConfig().device_index is None          # default
    finally:
        paths.set_data_dir(None)


def test_floor_calibration_is_saved_for_the_dongle(monkeypatch, tmpdir):
    """service.finish_floor_calibration takes the serial number of the open
    dongle when the window does not give one; the file goes to its folder."""
    from wwb_scanner import service
    paths.set_data_dir(str(tmpdir.join('data')))
    try:
        dongles.use('CAL-1')
        saved = {}
        def fake_calibrate(f, y, start, step, meta, clipped_share=None):
            saved['meta'] = meta
            saved['path'] = cleanup.profile_path(meta['gain'], meta['sample_rate'])
            return True, 'Calibration saved (test)'
        monkeypatch.setattr(cleanup, 'calibrate', fake_calibrate)

        class _Wrap(object):
            serial = 'CAL-1'
        class _Sc(object):
            error = None
            stop_requested = False
            sdr_wrapper = _Wrap()
            gain, sample_rate, step_size, window_size = 33.8, 2.4e6, 0.84, 96
            class sampling_config:
                window_type = 'hann'
            class sample_collection:
                aborted = False
                sample_sets = {}
            class config:
                scan_range = [31., 1766.]
            class spectrum:
                sample_data = np.zeros(10, dtype=[('frequency', float), ('dbFS', float)])
        ok, text = service.finish_floor_calibration(_Sc())
        assert ok and text.endswith(' - dongle CAL-1')
        assert saved['meta']['serial'] == 'CAL-1'
        assert os.path.dirname(saved['path']).endswith('sn-CAL-1')
    finally:
        paths.set_data_dir(None)
