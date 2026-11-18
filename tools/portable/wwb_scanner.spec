# -*- mode: python -*-
# V22.5: the portable build (plan of 18.11.2026, stage 4). Run through
# tools/portable/build.py, which also adds the dongle library, the rtl tools,
# the README and makes the zip.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

HERE = os.path.abspath(SPECPATH)

datas = collect_data_files('wwb_scanner')            # QML, band lists, gain profile
datas += copy_metadata('rtlsdr-wwb-scanner')         # --version, selftest
hidden = collect_submodules('wwb_scanner')           # file handlers etc. are imported lazily
hidden += ['rtlsdr', 'PySide6.QtCharts', 'PySide6.QtQuickControls2', 'PySide6.QtQuickDialogs2']

a = Analysis(
    [os.path.join(HERE, 'launcher.py')],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'pytest', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'PySide6.Qt3DCore',
              'PySide6.QtMultimedia', 'PySide6.QtPdf', 'PySide6.QtDesigner'],
    noarchive=False,
)
# PyInstaller takes Qt's whole QML folder and, with it, the libraries of
# modules the program never uses (3D, Graphs, Location, Multimedia, Web...):
# ~450 MB more. The window imports only QtQuick (Controls with the Fusion
# style, Layouts, Dialogs), QtCharts and its own modules; --selftest compiles
# every QML file of the program, so a missing module shows in the build.
import re
UNUSED_QML = ('Qt3D', 'Qt5Compat', 'QtDataVisualization', 'QtGraphs', 'QtLocation', 'QtMultimedia',
              'QtPositioning', 'QtQuick3D', 'QtRemoteObjects', 'QtScxml', 'QtSensors', 'QtTest',
              'QtTextToSpeech', 'QtWebChannel', 'QtWebSockets', 'QtWebView', 'QtWebEngine',
              'QtQuick/Particles', 'QtQuick/Scene2D', 'QtQuick/Scene3D', 'QtQuick/Timeline',
              'QtQuick/VirtualKeyboard', 'QtQuick/LocalStorage', 'QtQuick/VectorImage',
              'QtQuick/Controls/Imagine', 'QtQuick/Controls/Material', 'QtQuick/Controls/Universal',
              'QtQuick/Controls/FluentWinUI3', 'QtQuick/Controls/designer')
UNUSED_LIB = re.compile(r'Qt6(Quick3D|3D|Graphs|DataVisualization|Location|Multimedia|Positioning|'
                        r'RemoteObjects|Scxml|Sensors|Test|TextToSpeech|WebChannel|WebSockets|WebView|'
                        r'WebEngine|QuickParticles|QuickTimeline|VirtualKeyboard|ShaderTools|Pdf|'
                        r'Core5Compat|LabsStyleKit|SpatialAudio|QuickControls2(Imagine|Material|'
                        r'Universal|FluentWinUI3))', re.I)
UNUSED_QML_RE = re.compile(r'(^|/)qml/(%s)(/|$)' % '|'.join(re.escape(m) for m in UNUSED_QML))


def used(entry):
    dest = entry[0].replace('\\', '/')
    name = dest.rsplit('/', 1)[-1]
    if UNUSED_QML_RE.search(dest):
        return False
    if UNUSED_LIB.search(name):
        return False
    if '/translations/' in '/' + dest and dest.endswith('.qm'):
        return False
    return True


dropped = len(a.binaries) + len(a.datas)
a.binaries = [b for b in a.binaries if used(b)]
a.datas = [d for d in a.datas if used(d)]
print('wwb_scanner.spec: %d unused Qt files left out' % (dropped - len(a.binaries) - len(a.datas)))

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='wwb_scanner',
    console=False,              # a window program; console output goes to data/logs/console.txt
    disable_windowed_traceback=False,
    upx=False,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='WWB-Scanner')
