"""V18 "Bands": apply to a tree that contains wwb_scanner/ (user install copy or repo).

- wwb_scanner/bands.py: system bands (Shure, Sennheiser) and country rules
- several ranges in one scan (config.scan_ranges), gaps kept as gaps
- export: no invented values in the gaps
- window: Bands dialog, regulation strip on the graph
"""
import os
import shutil
import sys

ROOT = os.path.join(sys.argv[1], 'wwb_scanner')
HERE = os.path.dirname(os.path.abspath(__file__))


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding='utf-8').read()
    for a, b in pairs:
        assert s.count(a) == 1, (rel, s.count(a), a[:70])
        s = s.replace(a, b)
    open(p, 'w', encoding='utf-8', newline='\n').write(s)


shutil.copy(os.path.join(HERE, 'bands.py'), os.path.join(ROOT, 'bands.py'))
shutil.copy(os.path.join(HERE, 'BandsDialog.qml'), os.path.join(ROOT, 'ui', 'pyside', 'qml', 'BandsDialog.qml'))
shutil.copy(os.path.join(HERE, 'bands_qt.py'), os.path.join(ROOT, 'ui', 'pyside', 'bands_qt.py'))

# ------------------------------------------------------------------ config
patch('scanner/config.py', [
    ("""        scan_range=[400., 900.],
""", """        scan_range=[400., 900.],
        scan_ranges=None,         # V18: several ranges [[lo, hi], ...] in one scan
"""),
])

# ------------------------------------------------------------------ sample array / spectrum: keep only the scanned ranges
patch('scan_objects/samplearray.py', [
    ("""    def smooth(self, window_size):""", """    def keep_ranges(self, ranges):
        \"\"\"V18: keep only points inside any of ``ranges`` [(lo, hi)] MHz.\"\"\"
        keys = self._freq_keys(self.frequency)
        keep = np.zeros(keys.size, dtype=bool)
        for lo, hi in ranges:
            keep |= (keys >= int(round(lo * 1e6))) & (keys <= int(round(hi * 1e6)))
        ix = np.flatnonzero(keep)
        counts = self._get_counts()
        self.data = self.data[ix]
        self._counts = counts[ix]
    def smooth(self, window_size):"""),
])

patch('scan_objects/spectrum.py', [
    ("""    def crop(self, start_freq, end_freq):""", """    def keep_ranges(self, ranges):
        \"\"\"V18: remove points outside the scanned ranges [(lo, hi)] MHz.\"\"\"
        with self.data_update_lock:
            self.sample_data.keep_ranges(ranges)
            self.samples.clear()
            kwargs = {'spectrum':self, 'init_complete':True}
            for freq in self.sample_data.frequency:
                kwargs['frequency'] = freq
                self._build_sample(**kwargs)
        self.set_data_updated()
    def crop(self, start_freq, end_freq):"""),
])

# ------------------------------------------------------------------ scanner: several ranges
patch('scanner/main.py', [
    ("""    def build_sample_sets(self):
        freq,  end_freq = self.config.scan_range
        sample_collection = self.sample_collection""",
     """    @property
    def segments(self):
        \"\"\"V18: the ranges of this scan, [(lo, hi)] MHz. Several when
        config.scan_ranges lists them (bands of the chosen systems),
        otherwise the one scan_range.\"\"\"
        from wwb_scanner import bands
        rs = bands.parse_ranges(self.config.get('scan_ranges'))
        if rs:
            segs, outside = bands.scan_segments(rs)
            if len(segs) > 1:
                return segs
            if len(segs) == 1:
                return [segs[0]]
        lo, hi = self.config.scan_range
        return [(float(lo), float(hi))]
    @property
    def multi_range(self):
        return len(self.segments) > 1
    def range_text(self):
        segs = self.segments
        if len(segs) == 1:
            return '%.1f-%.1f MHz' % segs[0]
        return '%d ranges (%s MHz)' % (len(segs), ', '.join('%g-%g' % s for s in segs))
    def build_sample_sets(self):
        try:
            segs = self.segments
        except AttributeError:      # a bare object in old tests
            segs = []
        if len(segs) > 1:
            return self._build_multi_range(segs)
        freq,  end_freq = self.config.scan_range
        sample_collection = self.sample_collection"""),
    ("""            sample_set = sample_collection.build_sample_set(float(freq_hz))
            freq_hz += step_hz
""", """            sample_set = sample_collection.build_sample_set(float(freq_hz))
            freq_hz += step_hz
    def _build_multi_range(self, segs):
        \"\"\"V18: tiles for several ranges. All tile centres lie on one grid
        that starts at the first range, so the tile ripple has the same
        phase everywhere (clean scan works as for one range). Tiles in
        the gaps between the ranges are not measured.\"\"\"
        self.config.scan_range = [segs[0][0], segs[-1][1]]
        step_hz = int(round(mhz_to_hz(self.step_size)))
        if step_hz <= 0:
            raise ValueError('tile step must be above 0 (check Overlap Ratio)')
        g0 = int(round(mhz_to_hz(segs[0][0])))
        centres = set()
        for lo, hi in segs:
            k0 = (int(round(mhz_to_hz(lo))) - g0) // step_hz
            k1 = (int(round(mhz_to_hz(hi))) - g0) // step_hz
            for k in range(k0, k1 + 1):
                centres.add(g0 + k * step_hz)
        for c in sorted(centres):
            if c > MAX_TUNER_FREQ_HZ:
                logger.warning('The dongle reaches only %.0f MHz: scan ends there',
                               MAX_TUNER_FREQ_HZ / 1e6)
                break
            self.sample_collection.build_sample_set(float(c))
        logger.info('scan of %s: %d tiles', self.range_text(), len(centres))
"""),
    ("""            lo, hi = self.config.scan_range
            coll = self.sample_collection
            if getattr(coll, 'aborted', False):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %.1f-%.1f MHz stopped after %.0f s (dongle error), measured part kept',
                    lo, hi, time.time() - t_start)
            elif getattr(coll, 'missing_tiles', None):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %.1f-%.1f MHz done in %.0f s, %d tile(s) without data',
                    lo, hi, time.time() - t_start, len(coll.missing_tiles))
            else:
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %.1f-%.1f MHz done in %.0f s%s', lo, hi, time.time() - t_start, clean_note)""",
     """            what = self.range_text()
            coll = self.sample_collection
            if getattr(coll, 'aborted', False):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %s stopped after %.0f s (dongle error), measured part kept',
                    what, time.time() - t_start)
            elif getattr(coll, 'missing_tiles', None):
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %s done in %.0f s, %d tile(s) without data',
                    what, time.time() - t_start, len(coll.missing_tiles))
            else:
                logging.getLogger('wwb_scanner.status').info(
                    'Scan %s done in %.0f s%s', what, time.time() - t_start, clean_note)"""),
    ("""        f_min, f_max = self.config.scan_range
        self.spectrum.crop(f_min, f_max)""", """        f_min, f_max = self.config.scan_range
        self.spectrum.crop(f_min, f_max)
        segs = self.segments
        if len(segs) > 1:
            # V18: nothing between the ranges (tile edges reach into the gaps)
            self.spectrum.keep_ranges(segs)"""),
])

# ------------------------------------------------------------------ export: no invented values in gaps
patch('file_handlers/exporters.py', [
    ("""def wwb_grid(spectrum, step=WWB_STEP_KHZ, lo=WWB_MIN_KHZ, hi=WWB_MAX_KHZ):
    \"\"\"Scan values on a whole-kHz grid (``step`` kHz) inside ``lo``..``hi`` kHz,
    as WWB expects. Returns (grid_khz int64 array, dB array). The spectrum is
    not changed.\"\"\"""",
     """def gap_mask(grid_khz, freqs_khz):
    \"\"\"V18: True for grid points inside a gap of the scan (a scan of several
    ranges): between two measured points more than ``max(200 kHz, 4 x point
    step)`` apart and more than one point step from both of them.\"\"\"
    grid_khz = np.asarray(grid_khz, dtype=np.float64)
    f = np.asarray(freqs_khz, dtype=np.float64)
    if f.size < 2 or grid_khz.size == 0:
        return np.zeros(grid_khz.size, dtype=bool)
    step = float(np.median(np.diff(f)))
    limit = max(200.0, 4.0 * step)
    i = np.clip(np.searchsorted(f, grid_khz), 1, f.size - 1)
    wide = (f[i] - f[i - 1]) > limit
    dist = np.minimum(np.abs(grid_khz - f[i - 1]), np.abs(f[i] - grid_khz))
    return wide & (dist > step)


def wwb_grid(spectrum, step=WWB_STEP_KHZ, lo=WWB_MIN_KHZ, hi=WWB_MAX_KHZ, gaps=None):
    \"\"\"Scan values on a whole-kHz grid (``step`` kHz) inside ``lo``..``hi`` kHz,
    as WWB expects. Returns (grid_khz int64 array, dB array). The spectrum is
    not changed. Grid points in a gap of the scan (V18, several ranges) get
    NO_DATA_DB; with ``gaps='drop'`` they are left out.\"\"\""""),
    ("""    grid = np.arange(first, last + 1, step, dtype=np.int64)
    values = np.around(np.interp(grid, freqs_khz, dB), decimals=1)
    return grid, values""", """    grid = np.arange(first, last + 1, step, dtype=np.int64)
    values = np.around(np.interp(grid, freqs_khz, dB), decimals=1)
    gap = gap_mask(grid, freqs_khz)
    if gap.any():
        if gaps == 'drop':
            grid, values = grid[~gap], values[~gap]
        else:
            values[gap] = NO_DATA_DB
    return grid, values"""),
    ("""        grid, values = wwb_grid(self.spectrum, step=self.khz_step)
        lines = ['%d%s%.1f' % (f, self.delimiter_char, v) for f, v in zip(grid, values)]""",
     """        grid, values = wwb_grid(self.spectrum, step=self.khz_step, gaps='drop')
        lines = ['%d%s%.1f' % (f, self.delimiter_char, v) for f, v in zip(grid, values)]"""),
])

# ------------------------------------------------------------------ graph: gaps drawn as gaps
patch('ui/pyside/graph.py', [
    ("""# "khz" = Shure WWB style CSV, None = old MHz CSV""", """GAP_Y = -300.     # below any axis: the line drops out of the plot in a gap

def with_gaps(pts):
    \"\"\"V18: a scan of several ranges has holes between them. A line chart
    would join the ends with a straight line (a "measurement" where there
    was none); two points far below the plot make the hole visible.\"\"\"
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

# "khz" = Shure WWB style CSV, None = old MHz CSV"""),
    ("""        pts = decimate_max(self.xy_data, self._decim_k)
""", """        pts = with_gaps(decimate_max(self.xy_data, self._decim_k))
"""),
])

# ------------------------------------------------------------------ scanner interface (window side)
patch('ui/pyside/scanner.py', [
    ("""    _n_autoGain = Signal()
    configUpdate = Signal()""", """    _n_autoGain = Signal()
    _n_scanRanges = Signal()
    configUpdate = Signal()"""),
    ("""        self._autoGain = True
        super().__init__(*args)""", """        self._autoGain = True
        self._scanRanges = ''
        super().__init__(*args)"""),
    ("""    autoGain = Property(bool, _g_autoGain, _s_autoGain, notify=_n_autoGain)
""", """    autoGain = Property(bool, _g_autoGain, _s_autoGain, notify=_n_autoGain)

    # V18: several ranges as JSON '[[470, 534], [823, 832]]'; '' = one range
    def _g_scanRanges(self): return self._scanRanges
    def _s_scanRanges(self, value): self._generic_setter('_scanRanges', value or '')
    scanRanges = Property(str, _g_scanRanges, _s_scanRanges, notify=_n_scanRanges)
"""),
    ("""        conf.processing.clean_floor = bool(getattr(sc, 'cleanFloor', False))
        if self.calibrating:""", """        conf.processing.clean_floor = bool(getattr(sc, 'cleanFloor', False))
        segs = self.scan_segments()
        if len(segs) > 1:
            conf.scan_ranges = [list(s) for s in segs]
            conf.scan_range = [segs[0][0], segs[-1][1]]
        if self.calibrating:"""),
    ("""            conf.scan_range = list(cleanup.CALIBRATION_RANGE_MHZ)
""", """            conf.scan_range = list(cleanup.CALIBRATION_RANGE_MHZ)
            conf.scan_ranges = None
"""),
    ("""    @Slot(result=str)
    def calibrationStatus(self):""", """    def scan_segments(self):
        \"\"\"V18: [(lo, hi)] of the next scan (several when Bands chose them).\"\"\"
        from wwb_scanner import bands
        rs = bands.parse_ranges(getattr(self.scanConfig, 'scanRanges', ''))
        segs = bands.scan_segments(rs)[0] if rs else []
        return segs if len(segs) > 1 else []

    @Slot(result=str)
    def calibrationStatus(self):"""),
    ("""            self.spectrum.name = f'{self.startFreq} - {self.endFreq} (live)'""",
     """            segs = self.scan_segments()
            if segs:
                self.spectrum.name = 'Bands %s (live)' % ', '.join('%g-%g' % s for s in segs)
            else:
                self.spectrum.name = f'{self.startFreq} - {self.endFreq} (live)'"""),
])

patch('ui/pyside/main.py', [
    ("""    scanner.register_qml_types()
""", """    scanner.register_qml_types()
    from wwb_scanner.ui.pyside import bands_qt
    bands_qt.register_qml_types()
"""),
])

# ------------------------------------------------------------------ QML
patch('ui/pyside/qml/ScanConfig.qml', [
    ("""    property alias autoGain: model.autoGain

    signal configUpdate()""", """    property alias autoGain: model.autoGain
    property alias scanRanges: model.scanRanges

    signal configUpdate()"""),
    ("""        autoGain: true

        onConfigUpdate: {""", """        autoGain: true
        scanRanges: ''

        onConfigUpdate: {"""),
    ("""        property alias autoGain: model.autoGain
    }""", """        property alias autoGain: model.autoGain
        property alias scanRanges: model.scanRanges
    }"""),
])

patch('ui/pyside/qml/ScanControls.qml', [
    ("""    signal scannerState(bool state)
""", """    signal scannerState(bool state)
    signal openBands()
    // V18: several ranges chosen in Bands (JSON), '' = one range
    property string scanRanges: config ? config.scanRanges : ''
    property int rangeCount: {
        try { var r = JSON.parse(scanRanges); return r.length || 0; } catch (e) { return 0; }
    }
"""),
    ("""        onSubmit: {
            root.config.startFreq = startFreqInput.value;
        }""", """        onSubmit: {
            root.config.scanRanges = '';
            root.config.startFreq = startFreqInput.value;
        }"""),
    ("""        onSubmit: {
            root.config.endFreq = endFreqInput.value;
        }""", """        onSubmit: {
            root.config.scanRanges = '';
            root.config.endFreq = endFreqInput.value;
        }"""),
    ("""            if (it.hi > it.lo && root.config) {
                root.config.startFreq = it.lo;""", """            if (it.hi > it.lo && root.config) {
                root.config.scanRanges = '';
                root.config.startFreq = it.lo;"""),
    ("""    ToolSeparator { }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true
    }""", """    ToolSeparator { }

    // V18: bands of the systems + country rules
    ToolButton {
        id: bandsBtn
        text: root.rangeCount > 1 ? 'Bands: ' + root.rangeCount + ' ranges' : 'Bands...'
        enabled: !root.scanRunning
        highlighted: root.rangeCount > 1
        ToolTip.visible: hovered
        ToolTip.text: root.rangeCount > 1
                      ? 'Next scan covers only the chosen system bands. Type a range or pick a preset to scan one range again.'
                      : 'Choose your systems (Shure, Sennheiser) and the country: scan only their bands'
        onClicked: root.openBands()
    }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true
    }"""),
])

patch('ui/pyside/qml/Graph.qml', [
    ("""    property alias theme: chart.theme""", """    // V18: what the regulator allows (strip at the bottom of the plot)
    property var regRegions: []
    property bool showRegulation: true
    property alias theme: chart.theme"""),
    ("""            // V16: marks of the dongle's own spurs (not in the data, only drawn)""",
     """            // V18: regulation of the chosen country: faint colour over the
            // plot and a labelled strip at its bottom
            Item {
                id: regStrip
                x: chart.plotArea.x
                y: chart.plotArea.y
                width: chart.plotArea.width
                height: chart.plotArea.height
                visible: root.showRegulation && root.regRegions.length > 0
                clip: true
                Repeater {
                    model: root.regRegions
                    Item {
                        property real span: axisX.max - axisX.min
                        property real x0: span > 0 ? (modelData.lo - axisX.min) / span * regStrip.width : 0
                        property real x1: span > 0 ? (modelData.hi - axisX.min) / span * regStrip.width : 0
                        visible: x1 > 0 && x0 < regStrip.width && modelData.status != 'unknown'
                        x: x0
                        width: Math.max(1, x1 - x0)
                        height: regStrip.height
                        Rectangle {
                            anchors.fill: parent
                            color: modelData.color
                            opacity: modelData.status == 'no' ? 0.10 : 0.05
                        }
                        Rectangle {
                            id: regBar
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 14
                            color: modelData.color
                            opacity: 0.75
                            border.color: root.darkUi ? '#0d0f11' : '#ffffff'
                            border.width: 1
                        }
                        Text {
                            anchors.centerIn: regBar
                            visible: parent.width > implicitWidth + 8
                            text: modelData.label
                            color: '#ffffff'
                            font.pointSize: 7
                        }
                    }
                }
            }

            // V16: marks of the dongle's own spurs (not in the data, only drawn)"""),
])

patch('ui/pyside/qml/main.qml', [
    ("""            Action {
                text: qsTr("&Theme")
                onTriggered: themeSelect.open()
            }""", """            Action {
                text: qsTr("&Theme")
                onTriggered: themeSelect.open()
            }
            Action {
                text: qsTr("&Regulation strip") + (bandSettings.country ? ' (' + bandSettings.country + ')' : '')
                checkable: true
                checked: bandSettings.showRegulation
                onTriggered: bandSettings.showRegulation = checked
            }"""),
    ("""        Menu {
            title: qsTr("&View")""", """        Menu {
            title: qsTr("&Bands")
            Action {
                text: qsTr("&Systems and country...")
                onTriggered: bandsDialog.open()
            }
            Action {
                text: qsTr("Scan &one range again")
                enabled: scanConfig.scanRanges !== ''
                onTriggered: scanConfig.scanRanges = ''
            }
        }
        Menu {
            title: qsTr("&View")"""),
    ("""            config: scanConfig
            progress: scanner.progress""", """            config: scanConfig
            progress: scanner.progress
            onOpenBands: bandsDialog.open()"""),
    ("""            showSpurs: themeSelect.showSpurs
        }""", """            showSpurs: themeSelect.showSpurs
            showRegulation: bandSettings.showRegulation
            regRegions: bandsData.regions(bandSettings.country)
        }"""),
    ("""            Item { Layout.preferredWidth: 24 }
""", """            Item { Layout.preferredWidth: 12 }

            // V18: rule of the chosen country at the cursor
            Label {
                id: regLabel
                property var r: {
                    var rr = chartWrapper.regRegions, f = chartWrapper.mouseDataPoint.x;
                    if (!(f > 0) || !rr) return ({});
                    for (var i = 0; i < rr.length; i++)
                        if (f >= rr[i].lo && f < rr[i].hi) return rr[i];
                    return ({});
                }
                visible: bandSettings.showRegulation && r.label !== undefined
                text: visible ? bandSettings.country + ': ' + r.label : ''
                color: visible ? r.color : palette.windowText
                font.pointSize: 9
                ToolTip.visible: regMouse.containsMouse && visible
                ToolTip.text: visible ? r.lo.toFixed(1) + '-' + r.hi.toFixed(1) + ' MHz: ' + r.label
                                        + (r.note ? '\\n' + r.note : '') : ''
                MouseArea { id: regMouse; anchors.fill: parent; hoverEnabled: true }
            }
            Item { Layout.preferredWidth: 12 }
"""),
    ("""    ScanConfig {
        id: scanConfig
    }""", """    ScanConfig {
        id: scanConfig
    }

    // V18: systems and country rules
    Settings {
        id: bandSettings
        category: 'Bands'
        property string country: 'PL'
        property string selected: '[]'
        property bool showRegulation: true
    }
    BandsData {
        id: bandsData
    }
    BandsDialog {
        id: bandsDialog
        bands: bandsData
        config: scanConfig
        settings: bandSettings
    }"""),
])

print('V18 applied to', ROOT)
