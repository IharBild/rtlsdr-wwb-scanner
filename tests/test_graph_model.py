"""GraphTableModel update logic, with a minimal fake of PySide2."""
import sys
import types
import numpy as np
import pytest


def _install_fake_pyside(put):
    class Sig(object):
        def __init__(self, *a, **k): self.calls = []
        def emit(self, *a): self.calls.append(a)
        def connect(self, *a): pass
    class Base(object):
        def __init__(self, *a, **k): pass
    class Model(Base):
        dataChanged = None
        def __init__(self, *a):
            self.log = []
            self.dataChanged = Sig()
        def index(self, r, c): return (r, c)
        def beginInsertColumns(self, parent, a, b): self.log.append(('ins', a, b))
        def endInsertColumns(self): pass
        def beginRemoveColumns(self, parent, a, b): self.log.append(('rem', a, b))
        def endRemoveColumns(self): pass
    mods = {}
    for name in ('PySide2', 'PySide2.QtCore', 'PySide2.QtQml', 'PySide2.QtQuick',
                 'PySide2.QtGui', 'PySide2.QtCharts', 'PySide2.QtWidgets'):   # V21.4: qt.py
        mods[name] = types.ModuleType(name)
        mods[name].__getattr__ = lambda attr, _B=Base: _B
    qc = mods['PySide2.QtCore']
    qc.QAbstractTableModel = Model
    qc.QModelIndex = lambda: None
    qc.QPointF = Base
    qc.QObject = Base
    qc.Qt = types.SimpleNamespace()
    qc.QVariant = Base
    qc.Signal = Sig
    qc.Slot = lambda *a, **k: (lambda f: f)
    qc.Property = lambda *a, **k: property(a[1], a[2] if len(a) > 2 else None)
    mods['PySide2.QtQuick'].QQuickItem = Base
    mods['PySide2.QtCharts'].QtCharts = Base
    mods['PySide2.QtGui'].QColor = Base
    qc.Signal = Sig
    for n, m in mods.items():
        put(n, m)
    mods['PySide2'].QtCore = qc
    for sub in ('QtQml', 'QtQuick', 'QtGui', 'QtCharts', 'QtWidgets'):
        setattr(mods['PySide2'], sub, mods['PySide2.' + sub])
    pk = types.ModuleType('pkg_resources')
    pk.resource_filename = lambda *a: ''
    if 'pkg_resources' not in sys.modules:
        put('pkg_resources', pk)
    utils = types.ModuleType('wwb_scanner.ui.pyside.utils')
    utils.IntervalTimer = Base
    utils.is_pathlike = lambda x: (False, None)
    put('wwb_scanner.ui.pyside.utils', utils)


@pytest.fixture
def model():
    """graph.py under the fake Qt (it records the model calls).
    V21.4: only for this test - the real Qt modules and the scanner UI modules
    are put back afterwards, so later tests in the same run see the real Qt or
    none (the fake used to stay in sys.modules for the rest of the run)."""
    pytest.importorskip('numpy')
    names = ('PySide2', 'pkg_resources', 'wwb_scanner.ui.pyside.')
    saved = {k: sys.modules.pop(k) for k in list(sys.modules)
             if k == 'PySide2' or k.startswith('PySide2.') or k.startswith(names[2])}
    added = []

    def put(name, mod):
        if name not in sys.modules:
            added.append(name)
        sys.modules[name] = mod
    _install_fake_pyside(put)
    import wwb_scanner.ui.pyside as pkg
    attrs = {a: vars(pkg)[a] for a in ('graph', 'qt', 'utils') if a in vars(pkg)}
    for a in attrs:
        delattr(pkg, a)
    from wwb_scanner.ui.pyside import graph
    try:
        yield graph.GraphTableModel(), graph
    finally:
        for k in list(sys.modules):
            if k in added or k == 'PySide2' or k.startswith('PySide2.') or k.startswith(names[2]):
                sys.modules.pop(k, None)
        sys.modules.update(saved)
        for a in ('graph', 'qt', 'utils'):
            if a in vars(pkg):
                delattr(pkg, a)
        for a, v in attrs.items():
            setattr(pkg, a, v)


def arr(graph, n, start=0.0, level=-90.):
    a = np.zeros(n, dtype=graph.GRAPH_DTYPE)
    a['x'] = start + np.arange(n) * 0.025
    a['y'] = level
    return a


def test_growth_only_inserts_new_columns(model):
    m, g = model
    m.set_from_graph_dtype(arr(g, 100))
    m.log.clear()
    m.set_from_graph_dtype(arr(g, 130))
    assert m.log == [('ins', 100, 129)]


def test_small_change_emits_one_data_changed(model):
    m, g = model
    m.set_from_graph_dtype(arr(g, 5000))
    m.log.clear()
    a = arr(g, 5000)
    a['y'][4000:4010] = -50
    m.set_from_graph_dtype(a)
    assert m.log == [] and m.dataChanged.calls[-1] == ((0, 4000), (1, 4009))


def test_shrink_rebuilds_with_valid_ranges(model):
    m, g = model
    m.set_from_graph_dtype(arr(g, 70000))
    m.log.clear()
    m.set_from_graph_dtype(arr(g, 69900, start=2.5))
    assert m.log == [('rem', 0, 69999), ('ins', 0, 69899)]
    assert m._data.shape == (2, 69900)
    assert m._data[0, 0] == pytest.approx(2.5)


def test_shift_of_whole_scan_rebuilds_not_per_point(model):
    m, g = model
    m.set_from_graph_dtype(arr(g, 70000))
    m.log.clear()
    n_emits = len(m.dataChanged.calls)
    m.set_from_graph_dtype(arr(g, 70000, start=1.0))
    assert [e[0] for e in m.log] == ['rem', 'ins']
    assert len(m.dataChanged.calls) == n_emits


def test_decimate_keeps_peaks_and_limits_points(model):
    m, g = model
    a = arr(g, 70640)
    a['y'] = -100.
    a['y'][12345] = -50.
    a['y'][70639] = -60.
    d = g.decimate_max(a)
    assert d.size <= g.MAX_GRAPH_POINTS + 1
    assert g.group_size_for(70640) == 5
    assert d['y'].max() == -50.
    assert -60. in d['y']
    assert np.all(np.diff(d['x']) > 0)


def test_small_scan_is_not_decimated(model):
    m, g = model
    a = arr(g, 13000)
    assert g.decimate_max(a) is a


def test_group_size_stays_fixed_while_scan_grows(model):
    m, g = model
    k = g.group_size_for(70640)
    sizes = [g.decimate_max(arr(g, n), k).size for n in (5000, 20000, 40000, 70640)]
    assert sizes == sorted(sizes)
    a = arr(g, 20000); b = arr(g, 20009)
    da, db = g.decimate_max(a, k), g.decimate_max(b, k)
    assert np.array_equal(da['x'][:-1], db['x'][:da.size - 1])


# Export dialog: extension from the chosen filter (sdb2 offered since V11)
@pytest.mark.parametrize('name,filt,expect', [
    ('C:/scans/torwar', 'Shure WWB native (*.sdb2)', 'C:/scans/torwar.sdb2'),
    ('C:/scans/torwar', 'Shure WWB CSV (*.csv)', 'C:/scans/torwar.csv'),
    ('C:/scans/torwar', 'Numpy Files (*.npz)', 'C:/scans/torwar.npz'),
    ('C:/scans/torwar', '', 'C:/scans/torwar.csv'),
    ('C:/scans/torwar.csv', 'Shure WWB native (*.sdb2)', 'C:/scans/torwar.csv'),
    ('C:/scans/torwar.SDB2', 'Shure WWB CSV (*.csv)', 'C:/scans/torwar.SDB2'),
    ('C:/scans/v1.2 test', 'Shure WWB native (*.sdb2)', 'C:/scans/v1.2 test.sdb2'),
])
def test_export_extension(model, name, filt, expect):
    graph = model[1]
    assert graph.add_export_extension(name, filt) == expect


def test_export_sdb2_via_dialog_slot(model, tmpdir):
    import xml.etree.ElementTree as ET
    graph = model[1]
    from wwb_scanner.scan_objects import Spectrum
    f = np.round(np.arange(470.0, 471.0001, 0.025), 6)
    sp = Spectrum()
    sp.add_sample_set(frequency=f, dbFS=-100 + np.zeros(f.size))
    gd = graph.SpectrumGraphData()
    gd._spectrum = sp
    url = types.SimpleNamespace(toLocalFile=lambda: str(tmpdir.join('scan')))
    gd.save_to_file_as(url, 'Shure WWB native (*.sdb2)')
    p = str(tmpdir.join('scan.sdb2'))
    fs = [int(t.text) for t in ET.parse(p).getroot().find('*/freq_set')]
    assert fs[0] == 470000 and fs[-1] == 471000
