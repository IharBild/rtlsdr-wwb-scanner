import time
import logging
logger = logging.getLogger(__name__)
from bisect import bisect_left
import os
import sys

import numpy as np

from PySide2 import QtCore, QtQml, QtQuick, QtGui
from PySide2.QtCore import Signal, Property, Slot
from PySide2.QtCharts import QtCharts


from wwb_scanner.file_handlers import BaseImporter
from wwb_scanner.scan_objects.spectrum import Spectrum
from wwb_scanner.ui.pyside.utils import IntervalTimer, is_pathlike

GRAPH_DTYPE = np.dtype([
    ('x', np.float64),
    ('y', np.float64),
])

class GraphPoint(QtCore.QPointF):
    def __init__(self, *args):
        self._index = 0
        super().__init__(*args)
    def _g_index(self): return self._index
    def _s_index(self, value):
        self._index = value
    index = Property(int, _g_index, _s_index)

class GraphTableModel(QtCore.QAbstractTableModel):
    def __init__(self, *args):
        self._data = np.zeros((2,0), dtype=np.float64)
        super().__init__(*args)
    def columnCount(self, parent):
        return self._data.shape[1]
    def rowCount(self, parent):
        return self._data.shape[0]
    def flags(self, index):
        return QtCore.Qt.ItemFlags.ItemIsEnabled
    def data(self, index, role):
        if role == QtCore.Qt.DisplayRole or role == QtCore.Qt.EditRole:
            return float(self._data[index.row(), index.column()])
        return QtCore.QVariant()
    def _reshape_data(self, d_arr):
        """Grow the table at its end (a live scan adds points at the top)."""
        new_shape = (2, d_arr.size)
        cur_shape = self._data.shape
        start_col = cur_shape[-1]
        end_col = new_shape[-1] - 1
        if end_col < start_col:
            return
        self.beginInsertColumns(QtCore.QModelIndex(), start_col, end_col)
        data = np.zeros(new_shape, dtype=np.float64)
        data[:,:start_col] = self._data[:,:]
        data[0,start_col:] = d_arr['x'][start_col:]
        data[1,start_col:] = d_arr['y'][start_col:]
        self._data = data
        self.endInsertColumns()

    bulk_series = None   # chart series fed by this model (set by SpectrumGraphData)

    def _rebuild(self, d_arr):
        series = self.bulk_series
        if series is None:
            return self._rebuild_columns(d_arr)
        # The chart redraws after every point it is told about: 14 000 points
        # took 18 s. Keep the series quiet while the mapper refills it, then
        # tell the chart once that all points changed.
        try:
            series.blockSignals(True)
        except RuntimeError:          # series already deleted by QML
            self.bulk_series = None
            return self._rebuild_columns(d_arr)
        try:
            self._rebuild_columns(d_arr)
        finally:
            try:
                series.blockSignals(False)
                QtCore.QMetaObject.invokeMethod(series, 'pointsReplaced')
            except RuntimeError:
                self.bulk_series = None

    def _rebuild_columns(self, d_arr):
        """Replace the whole table: remove every column, then insert the
        new ones. Telling the chart "points 0..N changed" one by one makes
        it redraw after each point, which freezes the program for a very
        long time on a full-range scan (70 000 points)."""
        n_cur = self._data.shape[-1]
        if n_cur:
            self.beginRemoveColumns(QtCore.QModelIndex(), 0, n_cur - 1)
            self._data = np.zeros((2, 0), dtype=np.float64)
            self.endRemoveColumns()
        n_new = d_arr.size
        if n_new:
            data = np.zeros((2, n_new), dtype=np.float64)
            data[0] = d_arr['x']
            data[1] = d_arr['y']
            self.beginInsertColumns(QtCore.QModelIndex(), 0, n_new - 1)
            self._data = data
            self.endInsertColumns()

    def set_from_graph_dtype(self, d_arr):
        if d_arr.size < self._data.shape[-1]:
            self._rebuild(d_arr)
            return
        if d_arr.size > self._data.shape[-1]:
            self._reshape_data(d_arr)
        data = self._data
        changed = np.flatnonzero((data[0] != d_arr['x']) | (data[1] != d_arr['y']))
        if not changed.size:
            return
        if changed[-1] - changed[0] > REBUILD_THRESHOLD:
            self._rebuild(d_arr)
            return
        data[0] = d_arr['x']
        data[1] = d_arr['y']
        tl = self.index(0, int(changed[0]))
        br = self.index(data.shape[0]-1, int(changed[-1]))
        self.dataChanged.emit(tl, br)

# If more columns than this changed in one go, rebuild the model instead
REBUILD_THRESHOLD = 2000

# The chart redraws once per point it is given, so its cost grows with the
# square of the number of points. A full-range scan has 70 000 points and
# froze the window. Above this many points the chart gets one point per
# small group (the strongest one, so narrow peaks stay visible); the scan
# data and the saved CSV always keep every point.
MAX_GRAPH_POINTS = 15000

def group_size_for(num_points):
    """Points per chart point for a scan of ``num_points`` points. It is
    worked out once from the planned scan size, so it does not change
    while the scan is running (a change would rebuild the whole chart)."""
    return max(1, -(-int(num_points) // MAX_GRAPH_POINTS))

def decimate_max(d_arr, k=None):
    n = d_arr.size
    if k is None:
        k = group_size_for(n)
    if k <= 1 or n < k:
        return d_arr
    m = n // k * k
    ix = np.argmax(d_arr['y'][:m].reshape(-1, k), axis=1) + np.arange(0, m, k)
    if m < n:
        ix = np.append(ix, m + np.argmax(d_arr['y'][m:]))
    return d_arr[ix]

GAP_Y = -300.     # below any axis: the line drops out of the plot in a gap

def with_gaps(pts):
    """V18: a scan of several ranges has holes between them. A line chart
    would join the ends with a straight line (a "measurement" where there
    was none); two points far below the plot make the hole visible."""
    x = pts['x']
    if x.size < 3:
        return pts
    d = np.diff(x)
    med = float(np.median(d))
    big = np.flatnonzero(d > max(1.0, 20 * med))
    if not big.size:
        return pts
    ins = np.zeros(2 * big.size, dtype=pts.dtype)
    ins['x'][0::2] = x[big] + med
    ins['x'][1::2] = x[big + 1] - med
    ins['y'] = GAP_Y
    return np.insert(pts, np.repeat(big + 1, 2), ins)

# "khz" = Shure WWB style CSV, None = old MHz CSV
CSV_FREQUENCY_FORMAT = 'khz'

class SpectrumGraphData(QtQuick.QQuickItem):
    _n_model = Signal()
    _n_name = Signal()
    _n_min_value = Signal()
    _n_max_value = Signal()
    _n_spectrum = Signal()
    _n_color = Signal()
    _n_graphVisible = Signal()
    _n_marks = Signal()
    def __init__(self, *args):
        self.xy_data = np.zeros(0, dtype=GRAPH_DTYPE)
        self._loss_bands = []
        self._image_marks = []
        self._product_marks = []      # V21.2: products of the dongle (second-gain check)
        self._unmeasurable = []
        self._spur_marks = []
        self._spurs_cleaned = False
        self._min_value = QtCore.QPointF(0., 0.)
        self._max_value = QtCore.QPointF(0., 0.)
        self._model = None
        self._spectrum = None
        self._name = None
        self._color = None
        self._graphVisible = True
        self._decim_k = None
        self._series = None
        self._mapper = None
        self._mapper_detached = False
        super().__init__(*args)

    def _g_model(self): return self._model
    def _s_model(self, value):
        self._model = value
        if value is not None:
            value.bulk_series = self._series
        self._n_model.emit()
        self._on_model_changed()
    model = Property(QtCore.QObject, _g_model, _s_model, notify=_n_model)

    def _replace_series(self, pts):
        """All points to the chart in one call. The model mapper is
        disconnected the first time (it would add the points again, one by
        one through Python)."""
        series = self._series
        if series is None or not hasattr(series, 'replace'):
            return False
        try:
            if not self._mapper_detached:
                mapper = self._mapper
                if mapper is not None:
                    mapper.setProperty('model', None)
                self._mapper_detached = True
            P = QtCore.QPointF
            series.replace([P(x, y) for x, y in zip(pts['x'].tolist(), pts['y'].tolist())])
            return True
        except (RuntimeError, TypeError) as exc:
            logger.warning('direct chart update failed (%s), using the model', exc)
            self._series = None
            return False

    _n_mapper = Signal()
    def _g_mapper(self): return self._mapper
    def _s_mapper(self, value):
        self._mapper = value
        self._n_mapper.emit()
    mapper = Property(QtCore.QObject, _g_mapper, _s_mapper, notify=_n_mapper)

    _n_series = Signal()
    def _g_series(self): return self._series
    def _s_series(self, value):
        self._series = value
        if self._model is not None:
            self._model.bulk_series = value
        self._n_series.emit()
    series = Property(QtCore.QObject, _g_series, _s_series, notify=_n_series)

    def _g_name(self):
        name = self._name
        if name is not None:
            is_p, p = is_pathlike(name)
            if is_p:
                return p.name
        return name
    def _s_name(self, value):
        if value == self._name:
            return
        self._name = value
        self._n_name.emit()
        self._rename_spectrum(value)
    name = Property(str, _g_name, _s_name, notify=_n_name)

    def _rename_spectrum(self, value):
        """V21.1 (audit C3): Rename changed the name in the list only. The
        scan itself, its entry in the history (scan_index.json) and so the
        "Receiver;" line of a WSM export kept the old name."""
        sp = getattr(self, '_spectrum', None)
        if sp is None or not value or getattr(sp, 'name', None) == value:
            return
        try:
            sp.name = value
            eid = getattr(sp, 'eid', None)
            if eid is not None:
                from wwb_scanner.utils.dbstore import db_store
                db_store.update_scan(eid, name=value)
        except Exception:
            logger.exception('rename of the scan')

    def _g_color(self): return self._color
    def _s_color(self, value):
        if value == self._color:
            return
        self._color = value
        self._n_color.emit()
        if self.spectrum is not None:
            rgba = value.getRgbF()
            if rgba != self.spectrum.color:
                self.spectrum.color.from_list(rgba)
    color = Property(QtGui.QColor, _g_color, _s_color, notify=_n_color)

    def _g_graphVisible(self): return self._graphVisible
    def _s_graphVisible(self, value):
        if value == self._graphVisible:
            return
        self._graphVisible = value
        self._n_graphVisible.emit()
    graphVisible = Property(bool, _g_graphVisible, _s_graphVisible, notify=_n_graphVisible)

    def _g_min_value(self): return self._min_value
    def _s_min_value(self, value):
        self._min_value = value
        self._n_min_value.emit()
    minValue = Property(QtCore.QPointF, _g_min_value, _s_min_value, notify=_n_min_value)

    def _g_max_value(self): return self._max_value
    def _s_max_value(self, value):
        self._max_value = value
        self._n_max_value.emit()
    maxValue = Property(QtCore.QPointF, _g_max_value, _s_max_value, notify=_n_max_value)

    def _g_spectrum(self): return self._spectrum
    def _s_spectrum(self, value):
        if value == self._spectrum:
            return
        self._spectrum = value
        self._n_spectrum.emit()
        if value is not None:
            if self.name is None:
                self.name = value.name
            else:
                value.name = self.name
            if value.color != value.DEFAULT_COLOR:
                self.color = QtGui.QColor.fromRgbF(*value.color.to_list())
        self.update_spectrum_data()
    spectrum = Property(object, _g_spectrum, _s_spectrum, notify=_n_spectrum)

    # V20.2: bands where a strong signal took sensitivity away, and removed
    # images of strong signals (drawn on the graph, not in the data)
    def _g_loss_bands(self): return self._loss_bands
    lossBands = Property('QVariantList', _g_loss_bands, notify=_n_marks)
    def _g_image_marks(self): return self._image_marks
    imageMarks = Property('QVariantList', _g_image_marks, notify=_n_marks)
    def _g_product_marks(self): return self._product_marks
    productMarks = Property('QVariantList', _g_product_marks, notify=_n_marks)
    def _g_unmeasurable(self): return self._unmeasurable
    unmeasurableBands = Property('QVariantList', _g_unmeasurable, notify=_n_marks)
    # V20.9: own spurs hidden in this scan and strong narrow carriers on the
    # spur grid that were kept: [{'f': MHz, 'db': dB over the floor, 'kept': bool}]
    def _g_spur_marks(self): return self._spur_marks
    spurMarks = Property('QVariantList', _g_spur_marks, notify=_n_marks)
    # true when the spur cleaning ran on this scan (then spurMarks is the
    # whole truth; otherwise the graph draws the known spur frequencies)
    def _g_spurs_cleaned(self): return self._spurs_cleaned
    spursCleaned = Property(bool, _g_spurs_cleaned, notify=_n_marks)
    @staticmethod
    def spur_marks_of(spectrum):
        info = getattr(spectrum, 'cleanup_info', None) if spectrum is not None else None
        if not isinstance(info, dict) or not info.get('spurs'):
            return []
        out = []
        try:
            for fr in info.get('spurs_removed') or []:
                out.append({'f': float(fr), 'db': 0., 'kept': False})
            for item in info.get('spurs_kept') or []:
                out.append({'f': float(item[0]), 'db': float(item[1]), 'kept': True})
        except (TypeError, ValueError, IndexError):
            return []
        return sorted(out, key=lambda m: m['f'])
    def _update_marks(self):
        sp = self.spectrum
        lb = list(getattr(sp, 'loss_bands', None) or []) if sp is not None else []
        um = list(getattr(sp, 'unmeasurable_bands', None) or []) if sp is not None else []
        im = list(getattr(sp, 'image_marks', None) or []) if sp is not None else []
        pm = list(getattr(sp, 'product_marks', None) or []) if sp is not None else []
        sm = self.spur_marks_of(sp)
        info = getattr(sp, 'cleanup_info', None) if sp is not None else None
        cleaned = bool(isinstance(info, dict) and info.get('spurs'))
        if lb != self._loss_bands or im != self._image_marks or um != self._unmeasurable \
                or sm != self._spur_marks or cleaned != self._spurs_cleaned or pm != self._product_marks:
            self._loss_bands, self._image_marks, self._unmeasurable = lb, im, um
            self._product_marks = pm
            self._spur_marks, self._spurs_cleaned = sm, cleaned
            self._n_marks.emit()

    def _set_from_graph_dtype(self, d_arr):
        if self.model is None:
            return
        self.model.set_from_graph_dtype(d_arr)
    def _on_model_changed(self):
        self._set_series_from_data()

    @Slot(float, result=QtCore.QPointF)
    def get_nearest_by_x(self, value):
        xy_data = self.xy_data
        if not xy_data.size:
            return QtCore.QPointF(-1, -1)
        ix = np.searchsorted(xy_data['x'], value)
        if ix >= xy_data.size:
            ix = xy_data.size - 1
        elif ix > 0 and value - xy_data['x'][ix - 1] < xy_data['x'][ix] - value:
            ix -= 1     # V21.1 (audit D5): the nearest point, not the next one to the right
        x = xy_data['x'][ix]
        y = xy_data['y'][ix]
        return QtCore.QPointF(x, y)

    @Slot()
    def update_spectrum_data(self):
        if self.spectrum is None:
            return
        if not self.spectrum.data_updated.is_set():
            return
        self._update_data_from_spectrum()
        self._set_series_from_data()

    def _update_data_from_spectrum(self):
        spectrum = self.spectrum
        dtype = np.dtype(float)
        with spectrum.data_update_lock:
            xy_data = np.zeros(spectrum.sample_data.size, dtype=GRAPH_DTYPE)
            data = spectrum.sample_data.data
            xy_data['x'] = data['frequency']
            xy_data['y'] = spectrum.sample_data.data['dbFS']
            nan_ix = np.flatnonzero(np.isnan(xy_data['y']))
            xy_data['y'][nan_ix] = -110
            self.xy_data = xy_data
            spectrum.data_updated.clear()
        self._update_extents()
        try:
            self._update_marks()
        except Exception:
            pass

    def _update_extents(self):
        if not self.xy_data.size:
            # V20.8: a scan without points (it did not start, dongle busy) used
            # to raise 'zero-size array to reduction operation minimum' here
            return
        self.minValue = QtCore.QPointF(self.xy_data['x'].min(), self.xy_data['y'].min())
        self.maxValue = QtCore.QPointF(self.xy_data['x'].max(), self.xy_data['y'].max())

    def _set_series_from_data(self):
        if self.model is None:
            return
        t0 = time.time()
        pts = with_gaps(decimate_max(self.xy_data, self._decim_k))
        if not self._replace_series(pts):
            self.model.set_from_graph_dtype(pts)
        dt = time.time() - t0
        if dt > 0.5:
            print('chart update took %.1f s (%d points)' % (dt, self.xy_data.size), flush=True)

    @Slot(QtCore.QUrl)
    def load_from_file(self, uri):
        filename = uri.toLocalFile()
        try:
            spectrum = BaseImporter.import_file(filename)
        except Exception as exc:
            # V18.1: shown in the window (status line + dialog), not only the console
            logger.error('Could not open %s: %s', os.path.basename(filename), exc,
                         exc_info=not isinstance(exc, (ValueError, OSError)))
            return
        spectrum.set_data_updated()
        self.spectrum = spectrum
        self.update_spectrum_data()

    @Slot(str)
    def set_export_format(self, fmt):
        """V19: layout of the next .csv export: 'wsm' (Sennheiser WSM) or
        'khz' (Shure WWB). One-argument slot: a two-argument slot is not
        visible from QML on the user's PySide2 (V11.1)."""
        self._csv_format = str(fmt or '') or None

    @Slot(QtCore.QUrl)
    def save_to_file(self, uri):
        filename = uri.toLocalFile()
        # CSV is written in the Shure WWB layout (kHz, 25 kHz step, "\\n"),
        # or in the Sennheiser WSM layout when the address ends in "#wsm"
        # (V19.1, from the export dialog) or after set_export_format('wsm').
        fmt = getattr(self, '_csv_format', None) or CSV_FREQUENCY_FORMAT
        if uri.fragment().lower() == 'wsm':
            fmt = 'wsm'
        self._csv_format = None
        print('export to %s (%s)' % (filename, fmt), flush=True)
        try:
            self.spectrum.export_to_file(filename=filename, frequency_format=fmt)
        except Exception as exc:
            logger.error('Could not save %s: %s', os.path.basename(filename), exc,
                         exc_info=not isinstance(exc, (ValueError, OSError)))
            return
        print('export done: %s' % filename, flush=True)
        # V21.0 (audit A5, step 1): the export is confirmed in the window,
        # with what the numbers in the file are
        note = EXPORT_LEVEL_NOTE_WSM if fmt == 'wsm' else EXPORT_LEVEL_NOTE
        logging.getLogger('wwb_scanner.status').info('Exported %s. %s', os.path.basename(filename), note)

    @Slot(QtCore.QUrl, str)
    def save_to_file_as(self, uri, name_filter):
        """Export from the dialog: add the extension of the chosen filter
        when the typed name has none of ours (.csv, .sdb2, .npz)."""
        try:
            path = uri.toLocalFile() if hasattr(uri, 'toLocalFile') else str(uri)
            if not path and hasattr(uri, 'toString'):
                path = uri.toString()
            filename = add_export_extension(path, name_filter)
            print('export to %s (format %r)' % (filename, name_filter), flush=True)
            if self.spectrum is None:
                print('export FAILED: no scan in the graph', flush=True)
                return
            fmt = 'wsm' if 'wsm' in (name_filter or '').lower() else CSV_FREQUENCY_FORMAT
            self.spectrum.export_to_file(filename=filename, frequency_format=fmt)
            print('export done: %s, %d bytes' % (filename, os.path.getsize(filename)), flush=True)
        except Exception:
            import traceback
            print('export FAILED:', flush=True)
            traceback.print_exc()
            sys.stdout.flush()

EXPORT_EXTENSIONS = ('.csv', '.sdb2', '.sdb3', '.npz')
LEVEL_UNIT = 'dBFS/Hz'
EXPORT_LEVEL_NOTE = ('Levels in the file are %s at the gain of the scan (relative, not dBm; '
                     'WWB shows them as dBm)' % LEVEL_UNIT)
EXPORT_LEVEL_NOTE_WSM = 'Levels in the file are WSM units above the floor of the scan (0-48)'


def add_export_extension(filename, name_filter):
    """'scan' + 'Shure WWB native (*.sdb2)' -> 'scan.sdb2'. A name that
    already ends with one of our extensions is kept as typed."""
    if os.path.splitext(filename)[1].lower() in EXPORT_EXTENSIONS:
        return filename
    nf = (name_filter or '').lower()
    for ext in ('.sdb2', '.sdb3', '.npz', '.csv'):
        if '*' + ext in nf:
            return filename + ext
    return filename + '.csv'


class LiveSpectrumGraphData(SpectrumGraphData):
    _n_update_interval = Signal()
    _n_update_timer = Signal()
    _n_scanner = Signal()
    updateSpectrumData = Signal()
    def __init__(self, *args):
        self._update_interval = None
        self._update_timer = None
        self._scanner = None
        super().__init__(*args)
        self.updateSpectrumData.connect(self.update_spectrum_data)

    def _g_scanner(self): return self._scanner
    def _s_scanner(self, value):
        if value == self._scanner:
            return
        if self._scanner is not None:
            self._scanner.disconnect(self)
        self._scanner = value
        self._n_scanner.emit()
        if self._scanner is not None:
            self.spectrum = self._scanner.spectrum
            total = (self._scanner.endFreq - self._scanner.startFreq) / 0.025
            self._decim_k = group_size_for(total)
            self._scanner.scannerRunState.connect(self.on_scanner_run_state)
            self.update_interval = .5 if total > 20000 else .1
        else:
            self.update_interval = -1
    scanner = Property(QtCore.QObject, _g_scanner, _s_scanner, notify=_n_scanner)

    def on_scanner_run_state(self, state):
        if self.scanner is None:
            return
        if not self.scanner.running:
            # V20.8: whatever the last update does, this graph lets the scanner
            # go; a graph left connected repeated its error after every later scan
            try:
                self.update_interval = -1
                if self.spectrum is not None:
                    with self.spectrum.data_update_lock:
                        self.spectrum.set_data_updated()
                        self.update_spectrum_data()
            finally:
                self.scanner = None

    def _g_update_interval(self): return self._update_interval
    def _s_update_interval(self, value):
        if value == self._update_interval:
            return
        self._update_interval = value
        if self.update_timer is not None:
            self.update_timer.stop.emit()
        if value is not None and value > 0:
            ms = int(round(value * 1000))
            if self.update_timer is None:
                self.update_timer = IntervalTimer(interval_ms=ms)
                self.update_timer.trigger.connect(self.on_update_timer_trigger)
            else:
                self.update_timer.interval_ms = ms
            self.update_timer.start.emit()
        self._n_update_interval.emit()
    update_interval = Property(float, _g_update_interval, _s_update_interval, notify=_n_update_interval)

    def _g_update_timer(self): return self._update_timer
    def _s_update_timer(self, value):
        self._update_timer = value
        self._n_update_timer.emit()
    update_timer = Property(QtCore.QObject, _g_update_timer, _s_update_timer, notify=_n_update_timer)

    def on_update_timer_trigger(self, *args):
        self.updateSpectrumData.emit()

    def _update_extents(self):
        if not self.xy_data.size:
            return      # V20.8: nothing measured (yet): keep the axes as they are
        if self.scanner is not None:
            min_x = self.scanner.startFreq
            if self.xy_data['x'].min() < min_x:
                min_x = self.xy_data['x'].min()
            max_x = self.scanner.endFreq
            if self.xy_data['x'].max() > max_x:
                max_x = self.xy_data['x'].max()
        else:
            min_x = self.xy_data['x'].min()
            max_x = self.xy_data['x'].max()     # V20.8: was the level axis ('y')
        self.minValue = QtCore.QPointF(min_x, self.xy_data['y'].min())
        self.maxValue = QtCore.QPointF(max_x, self.xy_data['y'].max())

def register_qml_types():
    QtQml.qmlRegisterType(GraphTableModel, 'GraphUtils', 1, 0, 'GraphTableModel')
    QtQml.qmlRegisterType(SpectrumGraphData, 'GraphUtils', 1, 0, 'SpectrumGraphData')
    QtQml.qmlRegisterType(LiveSpectrumGraphData, 'GraphUtils', 1, 0, 'LiveSpectrumGraphData')
