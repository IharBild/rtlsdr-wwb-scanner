# rtlsdr-wwb-scanner — RTL-SDR Blog V4 fork

RF scanner for wireless microphones and in-ear monitors. It scans the spectrum with an
inexpensive [RTL-SDR][osmosdr-wiki] dongle and exports the scan for
**Shure Wireless Workbench (WWB)** frequency coordination.

This is a fork of [nocarryr/rtlsdr-wwb-scanner](https://github.com/nocarryr/rtlsdr-wwb-scanner)
by Matthew Reid. It is maintained for the **RTL-SDR Blog V4** on **Windows**, for a rental
company in Poland and for sound engineers across Europe. Most of the code in this fork was
written with Claude (Anthropic); every release is checked on a real V4 dongle by the
maintainer.

Current version: **V22.8** in the branch `v4-fixes` (installed by the command below). Last tagged
release: [v22.3](https://github.com/IharBild/rtlsdr-wwb-scanner/releases/tag/v22.3)
(branch `v4-fixes`). The `master` branch is the original upstream code and is not used here.

Русская часть — [в конце страницы](#по-русски).

## What is new compared to upstream

- **RTL-SDR Blog V4 support.** A scanner build of `rtlsdr.dll` with faster retuning and a
  PLL-lock check on every retune (`drivers/rtlsdr-scanner-v4`, sources and patches included).
  Stuck USB calls time out instead of freezing the program.
- **Speed.** About 78 ms per 2.4 MHz tile: 470–800 MHz in about 35 s, 31–1766 MHz in
  about 3 minutes (V4, 2.4 MS/s).
- **Clean scan.** Hides the dongle's own spurs (multiples of 14.4 MHz and 480 MHz, up to
  20 dB above the floor; stronger narrow carriers there are kept and marked) and the
  tile ripple, levels the noise floor with a calibration made without an antenna, and
  re-measures overloaded tiles at a lower gain (auto gain).
- **Strong signal next to the antenna** (own mic / IEM, auto gain on). The gain of a clipped
  tile goes down as far as needed (to 0 dB), so the carrier level is right; tiles up to
  4 MHz around it are read again at that gain too (the dongle clips inside there without
  showing it); both readings are merged so the false peaks and the raised floor around the
  carrier go away where the normal reading is usable. The status line names the band that
  lost sensitivity and by how many dB. All tiles of such a zone use one gain; products seen
  by one tile only are taken from the others; tiles made deaf up to 10 MHz away are raised by
  the loss; images of the carrier at +-2.4 MHz and +-28.8 MHz are marked on the graph and
  named in the status line (V20.9: left in the scan unless "Hide images of strong signals"
  is on). Points that no reading can measure show the flat
  floor of the zone instead of false spikes; the graph shades the bands that lost
  sensitivity ("-NN dB sens.") and marks the removed images. Images of a strong carrier
  are removed even when it does not clip (dips between TV channels are not counted as
  lost sensitivity). Both kinds of marks can be turned off in the View menu ("Lost
  sensitivity bands", "Removed image marks"); the choice is kept between runs.
  Where the dongle clips even at its lowest gain the graph marks the band "not
  measurable" (red; the data stays in the file, so WWB keeps away from it). Dips
  inside a TV multiplex or DAB block are not counted as lost sensitivity.
- **Real gain steps** (Scan settings > *Calibrate gain steps...*, antenna connected,
  about 2 minutes; survey 30-1200 MHz at the top gain, every tile read up and down, a USB error re-opens the dongle and
  skips a tile): the steps of the tuner's gain table are not the nominal ones and
  differ by frequency (on one V4: 49.6 -> 33.8 dB gave ~8 dB at 200 MHz, ~18 dB at
  900 MHz). The calibration measures them on FM / DAB / TV / mobile signals; auto gain
  then scales re-measured tiles by the real step. Without it the nominal steps are used.
- **Gain survey** (qa-kit `gain_survey.py --antenna` / `--no-antenna`, ~25 min each): every
  4 MHz the tile is read at all 29 gains, up and down; the data shows per band where the
  air noise rises over the dongle's own noise, where it overloads and where VHF compresses.
- **Suggest gain** (Scan settings, V20.6): sets the gain of Device Settings for the scan
  range and the overload switch from a gain profile - per band the sensitivity lost against
  the best step and the share of tiles that clip. With auto gain on it takes the most
  sensitive step (clipped tiles are re-measured); with it off, the most sensitive step where
  at most 5 % of the tiles clip. `gain_survey.py --analyze` makes the profile of your own site
  from the two surveys; without it a built-in one is used (one survey in Warsaw, 10.11.2026).
- **V20.7**: Scan settings > Clean scan shows each calibration on one line with a state dot
  (green / amber / grey) and an (i) with the details; (i) help next to the main switches and
  buttons. Scans in the list on the right can be renamed (double-click or the ⋮ menu) and
  deleted; a new scan is named with its range and start time. Gain step calibration no longer
  uses readings of a strong signal from two steps below the gain where it first clipped
  (compression made the 400-550 MHz steps 13.6 dB short on 10.11).
- **V20.7.3**: a calibration made while the dongle input is overloaded (floor far above
  the dongle's own -96 dB, or more than 10 % of the tiles clipping) is not saved and the
  previous one stays; every saved floor calibration keeps the previous one as `.prev.npz`.
  A scan where 80 % of the tiles clip even at the lowest gain says THE RESULT IS NOT VALID.
- **V20.8** (first fixes after the code audit of 11.11.2026):
  - Start or Calibrate without a dongle, or with the dongle busy in another program, says so
    ("No RTL-SDR dongle found", "dongle not found or busy") instead of a Python error; the
    error no longer comes back after every later scan, "Calibrate" pressed without a dongle
    no longer turns the next Start into a calibration scan, and the program closes cleanly.
    A dongle plugged in after the program started is found by the next Start (it needed a
    restart).
  - Start Freq at or above End Freq, or an empty field: a clear message, no scan.
  - sdb2 files are written as UTF-8 (their header always said so): exporting into a folder
    with Cyrillic, Polish or German letters gave a broken file or none. WSM CSV: a scan name
    with such letters no longer stops the export; UTF-8 files (with or without BOM) open.
  - A first start now has the settings the fork is tested with: 2400 kHz, gain 33.8 dB,
    window 96 (one point per 25 kHz, the grid of WWB), 2048 x 40 samples, overlap 0.65
    (tile step 0.775 MHz). Before: 2048 kHz, gain 0 dB (30-37 dB less sensitive in
    470-700 MHz), window 128, 8192 x 20, overlap 0.5. Settings that are still exactly the
    old six values are replaced once, with a line in the status bar; anything you changed
    stays. All of them remain adjustable in Device Settings and Scan settings.
  - Scan settings is never taller than the window, scrolls, and opens in the middle: on a
    1366x768 laptop the calibration buttons and "Suggest gain" were cut off.
- **V20.9** (second part of the audit, and the user's scans of 11-12.11.2026):
  - Own spurs: the dongle also makes carriers at the odd multiples of 14.4 MHz (475.2, 504.0,
    532.8, 561.6 ... MHz: +6.8 dB without antenna at 49.6 dB gain, up to +16.5 dB with the
    antenna). They are now in the list; before, they stayed in every scan.
  - "Hide dongle's own spurs" (Scan settings, was "Remove ...") hides a narrow carrier on that
    grid only while it is not more than 20 dB above the floor. A stronger one is kept and
    marked "on spur grid +NN dB: kept" on the graph and in the status line: it may be a
    transmitter. Before, any narrow carrier exactly on a multiple of 28.8 MHz was erased, up
    to +47 dB - a microphone there disappeared from the scan and from the files for WWB.
    A weak far transmitter exactly on the grid (below +20 dB) is still hidden; switch the
    option off to see everything.
  - Images of a strong signal (2.4 MHz and 28.8 MHz from it) are now only marked "image?" and
    stay in the scan and in the files: a real weak signal there looks the same. The new
    switch "Hide images of strong signals" (off by default) replaces them by the floor as
    before.
  - Floor calibration: transmitters that reach the dongle even without antenna (TV, GSM,
    423 MHz) are left out of the floor shape. The calibration of 12.11.2026 at 49.6 dB had
    TV channel 27 in it (+5.6 dB) and 423-424 MHz (+11 dB): every scan was lowered by that
    much there. Calibrations made before are corrected when they are used; no need to
    calibrate again.
  - A scan opened from the history shows what was hidden in it.
- **V21.0** (third part of the audit):
  - **Smoothing** and **Scaling** are gone from Scan settings. Both changed the levels after
    the scan had been saved, so the graph and the exported files differed from the history:
    Scaling stretched the dB axis (19.8 dB between two carriers became 36.7 dB), Smoothing
    averaged over a share of the scan (2.3 MHz for 470-700 MHz). Settings saved by an older
    version are ignored. `Spectrum.smooth()` / `Spectrum.scale()` stay for scripts.
  - **The level has its name: dBFS/Hz** - on the level axis, at the cursor, and in an
    explanation (i) next to it. It is the spectral density relative to the dongle's full
    scale at the current gain, not dBm: the same microphone reads about 34 dB higher at
    33.8 dB gain than at 0 dB, and a narrow carrier reads about 46 dB below its power in
    dBFS (25 kHz points, Hann window). The numbers and the files do not change; WWB still
    shows them as "dBm". Compare a level only with the floor of the same scan.
  - An export is confirmed in the status line, with what the numbers in the file are.
  - Level axis zoomed in below 1 dB per tick: labels with a decimal (they read
    "-98, -98, -99, -99").
- **V21.2** - **Check peaks with less gain** (Scan settings, on by default). Two strong
  carriers (own mics / IEMs near the antenna) make false peaks in the dongle's tuner at
  2 f1 - f2 and 2 f2 - f1, even when nothing clips. After the scan, narrow peaks that are
  at least 10 dB above the floor and 6 dB weaker than the second strongest carrier are
  read again: twice with about 10 dB less gain and once more with the scan gain. Scaled
  back by the real gain difference, a real signal keeps its level; a product of the
  dongle falls by about twice the gain drop. Such peaks get a pink mark "dongle product"
  (View > Dongle product marks) and are named in the status line. They are **not**
  removed: the scan and the exported files stay as measured. A peak whose level changed
  between the readings, or that is too weak to tell, is left unmarked. Nothing is read
  when there are fewer than two strong narrow carriers (30 dB above the floor); at most
  30 tiles are read again (about 0.2 s each, up to about 6 s per scan). Checked on the
  dongle simulator with a tuner nonlinearity model; not yet on the real V4.
- **V21.3** - **New Python and pip.** The scanner installs on Python 3.7 to 3.14 with
  current pip (`pyproject.toml`); pip picks the libraries by the Python version:
  - Python 3.7-3.9: as before (pyrtlsdr 0.3, PySide2, numpy 1.x). Nothing changes on a
    working PC.
  - Python 3.10: the window with PySide2 5.15, pyrtlsdr 0.5 and numpy 1.26 (numpy stays
    below 2 wherever PySide2 is installed: PySide2 warns that it may crash with numpy 2).
  - Python 3.11-3.14: the scanner core, the tests and the file export with numpy 2 and
    pyrtlsdr 0.5. **No window yet**: PySide2 has no packages there, the window moves to
    PySide6 in the next version. `wwb_scanner-ui` says so instead of a traceback.
  - pyrtlsdr 0.4+ needs `rtlsdr_set_dithering`, which no V4 driver exported. New driver
    **scanner-v7** (`drivers/rtlsdr-scanner-v7`) = v6 + that function and the GPIO
    functions of pyrtlsdr 0.4+; with pyrtlsdr 0.3 it works like v6. Its `install_dll.py`
    also sets it up in a new environment.
  - On Windows with Python 3.8+ a DLL in the `Scripts` folder of a virtual environment is
    not found by pyrtlsdr 0.4+ (Python no longer searches PATH for DLLs). The scanner now
    adds that folder itself.
  - Checked: all tests on Python 3.8, 3.10 and 3.13; the window check on 3.8 and 3.10;
    the real pyrtlsdr 0.5 with the v7 driver built for Linux against a USB model (a full
    scan); DLL loading with Windows Python 3.13 in Wine. **Not yet on the real V4.**
- **V21.3.1** - found on the real V4 (15.11.2026):
  - **Speed on Python 3.13.** scipy 1.16+ computes `welch` with one FFT call per
    segment from Python: one tile took 88 ms instead of 11-14 ms on the PC (a full scan
    would take twice as long). The scanner now has its own Welch with one batched FFT,
    the same result as scipy (tested to 1e-12). In the simulator: 21.5 -> 2.3 ms per tile
    on Python 3.13, 2.9 -> 2.6 ms on the old stack.
  - `install_dll.py` (v7) no longer replaces the DLL of another Python: in a venv of
    Python 3.13 pyrtlsdr 0.5 had found the `librtlsdr.dll` of Python 3.7 through PATH.
    The DLL now goes into the venv's `Scripts` folder.
  - On the V4 with V21.3 + v7 on Python 3.7: full scan 175 s (v6: 177 s), unplugging and
    plugging back during a scan: the scan goes on and loses nothing. On Python 3.13
    (pyrtlsdr 0.5): a 470-480 MHz scan with export works.
- **V21.4** - **The window on PySide6 (Qt 6).** On Python 3.11-3.15 pip now installs
  PySide6 (only PySide6-Essentials and PySide6-Addons, about 125 MB for Windows; the
  `PySide6` package would also pull WebEngine) and the window works there. Python 3.7-3.10
  keep PySide2 5.15 as before.
  - One code for both: `wwb_scanner/ui/pyside/qt.py` picks PySide2 or PySide6
    (`WWB_QT=pyside6` prefers PySide6 when both are installed).
  - File and colour dialogs and the saved settings differ between Qt 5 and Qt 6; the QML
    uses a small module `WwbCompat` with one version per Qt (`qml/compat/qt5|qt6`).
  - Signal handlers name their parameters (Qt 6 warned about every one).
  - The export/import dialogs keep their folder setting in a property: the Qt 5 dialog on
    Linux did not load main.qml before (the test tool had rewritten a copy for that).
  - Checked: the window check U1-U41 passes on PySide2 5.15 (Python 3.8) and PySide6 6.12
    (Python 3.13, also from a clean install with only Essentials + Addons); all tests on
    both. **Not yet on Windows with the dongle.**
- **V22.8** - **Reliability** (external audit of 22.11.2026). A dongle whose set-up fails (for
  example an unsupported sample rate) is closed again and the scan ends with an error; the next
  scan no longer waits for ever. Stop pressed while the dongle is being opened (~1.6 s) ends the
  scan before its first tile. Closing the window during a scan stops it and waits for it (the
  program crashed). A watchdog writes the state of every thread to the log file when a scan has
  not moved for 60 s. A dongle with a USB call that hangs is not closed under that call. The gain
  that does not come back after the auto gain stops the scan with the reason (the next tiles were
  quietly lower). The peak check is limited by time (15 % of the scan, 10-60 s) instead of 30
  tiles. Scan ranges are checked against what the V4 tunes, 0.5-1766 MHz; a scan without a
  single point is an error; tiles without data are a warning. sdb3: gaps are written as
  NoDataValue (WWB read -140 as a level). The history keeps the marks of a scan (images,
  products, less sensitive and not measurable bands) and reads its raw values back; the points
  file is written before the index record.
- **V22.7** - **Own gain per band** (Scan settings > "Own gain per band", off until checked on
  the V4). A short probe before the scan (a few seconds) reads every band of the scan at the
  gain of the scan and raises the gain where the band is quiet enough (headroom of the strongest
  part minus 6 dB, the new gain checked once more), e.g. 560-700 MHz at 49.6 dB instead of
  33.8 dB. The gain never goes below the gain of the scan (Overload still re-measures clipped
  tiles). Only gains with a floor calibration are used. Each group of bands of one gain is
  scanned with its own gain and cleaned with its own calibration; levels are brought to the gain
  of the scan with the real gain steps, so carriers keep their level across band edges. The gains
  are named in the status line and saved with the scan (`gain_plan`).
- **V22.6** - **Three FFT windows** (Hann, Blackman-Harris, Flat-top), computed by the program
  itself - the same arrays as scipy to the last bit, so scans with Hann are unchanged. scipy is
  no longer needed (the portable version is ~70 MB smaller; the tests still use it). A window of
  an older version in the settings becomes Hann (said once). Portable build without software
  OpenGL and Qt's own OpenSSL.
- **V22.5** - **Portable Windows version** (no Python to install): a folder with
  `wwb_scanner.exe`, built by GitHub Actions (workflow *Portable Windows build*, PyInstaller,
  `tools/portable`) and attached to each published release as
  `WWB-Scanner-<version>-win64-portable.zip`. Everything the program writes stays in `data`
  next to the exe, so a USB stick keeps its scans, calibrations and settings from PC to PC;
  the settings of the PC are not taken over. `rtl-tools` holds `rtl_test.exe` and
  `rtl_eeprom.exe` (own serial number for a second dongle). The dongle still needs the WinUSB
  driver once per PC (Zadig, see `README_PORTABLE.txt`). New for both versions:
  `--version` and `--selftest` (window, every QML file, dongle library, data folder; report in
  `logs/selftest.txt`).
- **V22.4** - **Calibrations of each dongle.** Floor calibrations, the table of the real gain
  steps and the gain profile are kept per dongle, in `calibration\sn-<serial number>`; the
  calibrations of the dongle that is opened (or chosen in Device Settings) are used, and
  Scan settings names it. The calibrations made before V22.4 go to the first dongle seen after
  the update (copied: V22.3 still finds them). A second dongle starts uncalibrated instead of
  using the first one's floor and gain steps. Many dongles, the V4 too, leave the factory with
  the serial number 00000001: two such dongles can not be told apart - the program says so;
  give each its own serial number (`rtl_eeprom -s`). The serial number of the dongle is saved
  with each scan.
- **V22.0-V22.3** - **New foundation** (audit of the architecture of 16.11.2026). Nothing
  changes in what a scan measures; the code underneath is ready for the precise mode, the
  web interface and other receivers.
  - **Python 3.11 or newer, PySide6 only.** Python 3.7-3.10 and PySide2 are no longer
    supported (use V21.4.1 there). On Python 3.11 PySide6 stays below 6.12: 6.12.0 aborts
    there after a few hundred Qt messages (Python 3.12+ is not affected).
  - **One data folder** `%USERPROFILE%\wwb_scanner_data` (or the variable
    `WWB_SCANNER_DATA`): history of scans, calibrations, one log file (`logs\`), the time
    report and now also the **window settings** (an INI file instead of the Windows
    registry; the first start copies them from the registry, which is left as it is).
  - **One set of default settings in the core** = the tested configuration (2400 kHz,
    33.8 dB, Hann 96, 2048 x 40, overlap 0.65); a misspelt setting is an error.
  - No tinydb and no json-object-factory any more; the history files keep their layout.
  - Removed: Sample objects per point (13 MB per full scan), the empty `iq` field of the
    points, the matplotlib plot, the rtl_power scanner, the TCP dongle, the async reader,
    scaling / smoothing / interpolation.
  - **Scan service and events** (`wwb_scanner/service.py`, `wwb_scanner/events.py`): what
    a scan of each kind is (ordinary, floor calibration, gain step calibration), why it
    can not start, and the result in one object; the floor calibration is saved by the
    core. Status lines, console lines, progress and every tile are events.
  - **Several passes** (`scan_objects/passes.py`): a scan can keep N passes on one grid
    with views max-hold / average / last; the history keeps every pass. The base of the
    "Precise" mode; not yet in the window.
  - **Receiver interface** (`wwb_scanner/receivers`): the core reaches the dongle only
    through it; the RTL-SDR is one implementation.
  - Checked on the V4 18.11.2026 (Python 3.13.17, PySide6 6.12.0, driver v7): PC checks
    0 FAIL, 41/41 window checks; by hand: settings moved from the registry to the INI
    file and kept after a restart, scan 470-700 MHz in 24 s, Stop, export in 4 formats
    (WSM opens our file), floor calibration without antenna saved by the new core.
- **V21.4.1** - checked on the V4 (16-18.11.2026): the window on PySide6 (Python 3.13,
  41/41 window checks with the dongle, 12 manual checks, export in 4 formats; WSM opens our
  file) and on PySide2 (Python 3.7). Full scan 31-1766 MHz: 154 s on Python 3.13.
  - The console now gives the time of the peak check and every checked peak in one line
    (`peaks checked: 599.600 +19 dB product (+12); 601.500 +16 dB real (-2)`).
  - Tuner I2C counters are printed once more just before the dongle is closed, when
    there were retries (calibrations and the peak check are after the scan's report).
- **V21.1** (the small items left from the audit and from the tests on the PC):
  - **Stop** only asks the scan to end: the window no longer freezes while a USB read
    hangs (11 s measured). A stopped scan is finished like any other - cropped to the
    range, cleaned, saved to the history - and the status line says how far it got.
  - Pulling the dongle out no longer opens the "Problem" dialog with the raw text
    `set_center_freq(...) failed (attempt 1)` before the message that says what to do.
  - The status line shows two lines (long messages were cut); the full text is in its
    tooltip.
  - **Rename** reaches the scan itself, the history and the "Receiver;" line of a WSM file.
  - **Device Settings**: the sample rate field is kHz (the title said Msps) and also takes
    MHz ("2.4" was read as 2 kHz); the device list is wide enough; Cancel puts gain and
    sample rate back. Cancel in Scan settings puts back a suggested gain.
  - Empty check boxes are visible in the dark theme; the cursor reads the nearest point.
  - The window starts without setuptools (`pkg_resources` is gone); the package has a
    version (21.1.0).
  - Bands: Germany 470-608 and 614-698 MHz until 31.12.2030, professional productions only
    (BNetzA Vfg. 37/2026); Shure SLX-D S50 with 863-865 MHz, SLX-D P53 added.
- **Tuner I2C errors** (`i2c wr failed`, LIBUSB_ERROR_PIPE): the driver build scanner-v5
  (`drivers/rtlsdr-scanner-v5`, `python install_dll.py`) repeats a failed tuner transfer up
  to 3 times with the I2C repeater switched on again; the console prints how many were
  retried. The gain step calibration accepts steps up to 4 dB off nominal (real V4 steps
  measured 3.1-3.9 dB off).
- **USB errors**: after a USB error the dongle is opened again and the scan goes on
  (once per scan); a new scan waits up to 10 s for a dongle that is "not found or
  busy"; a tile that takes over 15 s (normally 0.1 s) stops the scan with a message.
  File > Export asks first while the scan in the graph is still running. On Windows the
  computer is kept from going to sleep while a scan runs.
- **Bands.** Band presets of Shure systems (PSM 200/300/900/1000, QLX-D, ULX-D, SLX-D,
  Axient Digital, UHF-R, BLX, SLX, PGX, GLX-D) and Sennheiser systems (ew 100 G3/G4,
  ew 300 G4). Scans only the bands of the chosen systems in one run. Each band is marked
  with what the chosen country allows for PMSE: 27 EU countries, Norway, Iceland,
  Switzerland, Liechtenstein, United Kingdom, Ukraine, Serbia (partial data) and Belarus
  (no reliable public data).
- **WWB export.** CSV in the WWB layout (kHz), `.sdb2` and `.sdb3` (WWB 7). Gaps in a scan
  stay gaps: no invented values. The levels are dBFS/Hz at the gain of the scan (WWB shows
  them as dBm): set exclusion thresholds relative to the floor of the scan.
- **Sennheiser WSM.** *File > Export > Sennheiser WSM CSV* writes the layout of WSM's own
  receiver scans (EM 300-500 G3); WSM receiver scans can be imported and laid over an
  RTL-SDR scan. The level is dB above the floor x 1.3 (0..48), set from side-by-side scans
  with an EM 300 G3.
- **Window.** Dark theme, status line and error dialog, range presets, time left,
  spur marks, EU TV channel grid, regulation strip under the graph.
- **Safety and reliability.** Scan history stored without pickle, XML files with a DTD are
  refused, every uncaught error is shown in the window and written to the log, and the
  window never stays stuck in "scanning".
- **Tests.** A dongle simulator and about 750 automatic tests (no dongle needed), a window
  check and a test kit for a PC with a real dongle (`qa-kit` in the releases).

## Tested with

| | |
|---|---|
| Hardware | RTL-SDR Blog V4 (R828D tuner) |
| System | Windows 11, Python 3.13.17 64-bit |
| Libraries | numpy 2.5.4, scipy 1.18.1, pyrtlsdr 0.5.1, PySide6 6.12.0 |
| Driver | `rtlsdr-scanner-v7` (checked on the V4 15.11.2026) |
| Automatic tests | GitHub Actions: Linux, Python 3.11, 3.13 and 3.14; Windows, Python 3.13; simulator instead of the dongle |

Python 3.11 or newer; the window runs on PySide6. pyrtlsdr 0.4 and newer need the
`rtlsdr-scanner-v7` driver. V22.3 is checked on the V4 (18.11.2026, see the version list
above).

## Installation (Windows)

1. **Dongle driver.** Plug in the V4 and install the WinUSB driver with
   [Zadig](https://zadig.akeo.ie/) (standard RTL-SDR step).
2. **Python.** Install Python 3.13, 64-bit (or any of 3.11–3.15), then create a virtual
   environment:

   ```bat
   py -3.13 -m venv C:\wwb-scanner
   C:\wwb-scanner\Scripts\activate
   ```

   The last 3.13 with a Windows installer is 3.13.17 (later 3.13 releases are source only).

3. **The scanner** from the source archive of this branch (no git needed):

   ```bat
   python -m pip install --upgrade pip
   python -m pip install ^
     https://github.com/IharBild/rtlsdr-wwb-scanner/archive/refs/heads/v4-fixes.zip
   ```

   pip installs numpy, scipy, pyrtlsdr and PySide6 (Essentials + Addons) with it.

   For a fixed release use `.../archive/refs/tags/v22.3.zip` instead.

4. **rtlsdr.dll.** Unpack the same source archive, then with the environment active:
   `python drivers\rtlsdr-scanner-v7\install_dll.py` (puts the DLL into
   `C:\wwb-scanner\Scripts`; `... install_dll.py where` shows which DLL is loaded,
   `... install_dll.py rollback` restores the previous one).
5. **Start:** `C:\wwb-scanner\Scripts\wwb_scanner-ui`. You can make a desktop shortcut to it.

A one-click Windows installer is planned.

### Updating

The same command with `--force-reinstall --no-deps` replaces the program files and keeps
your libraries, scans, calibrations and settings (they live in `%USERPROFILE%\wwb_scanner_data`,
or in the folder named by the variable `WWB_SCANNER_DATA`):

```bat
python -m pip install --force-reinstall --no-deps ^
  https://github.com/IharBild/rtlsdr-wwb-scanner/archive/refs/heads/v4-fixes.zip
```

### Switching from the original `pip install rtlsdr-wwb-scanner`

If the original package from PyPI is installed (maybe with files replaced by hand),
make a clean switch so no old or hand-made files stay behind:

```bat
rem 1. close the scanner; keep a copy of the old program folder
python -c "import wwb_scanner, os; print(os.path.dirname(wwb_scanner.__file__))"
xcopy /E /I /Y "<that folder>" "C:\wwb_backup\wwb_scanner"
python -m pip freeze > C:\wwb_backup\pip_freeze.txt
rem 2. remove the old program, then the files pip did not know about
python -m pip uninstall -y rtlsdr-wwb-scanner
rmdir /S /Q "<that folder>"
rem 3. install this fork without touching numpy / scipy / PySide6 / pyrtlsdr
python -m pip install --no-deps ^
  https://github.com/IharBild/rtlsdr-wwb-scanner/archive/refs/heads/v4-fixes.zip
```

Your `rtlsdr.dll` and the data folder are not touched. Going back: uninstall again and
copy the folder from `C:\wwb_backup` back.

## Usage in short

1. Antenna on, close other SDR programs, start the scanner.
2. Pick a range preset or press **Bands...**: choose the country and tick the bands of the
   systems you bring. The next scan covers only those bands.
3. Once per gain setting: **Scan settings > Calibrate dongle...** with the antenna removed
   (levels the noise floor).
4. Scan, then **File > Export**: `.sdb3` (WWB 7) or *Shure WWB CSV* for WWB,
   *Sennheiser WSM CSV* for WSM.

The country rules are a guide for planning, **not a legal document**. Rules change;
check with the national regulator before a show (Poland: UKE, Germany: BNetzA, UK: Ofcom).
Sources: CEPT ERC/REC 25-10 Annex 4 (EFIS, November 2026), BNetzA general allocation
(until 31.12.2032), ANCOM RO-IR MICRO 2025, NKEK resolution No 361 of 03.07.2024.

## Tests

No dongle is needed:

```bash
python -m pytest tests          # about 750 tests, dongle simulator
python tools/qa_ui.py           # opens the window offscreen, scans with the simulator
python tools/qa_sandbox.py      # all test types, report in Markdown
```

On a PC with a real dongle use `qa_pc.py` from the `qa-kit` archive of the release.

## License and credits

GNU General Public License v2 (see `LICENSE`). Original program by Matthew Reid
([nocarryr](https://github.com/nocarryr)). The V4 driver build is based on
[rtl-sdr-blog](https://github.com/rtlsdrblog/rtl-sdr-blog) and osmocom rtl-sdr
(GPL-2.0-or-later, libusb LGPL-2.1; sources in `drivers/rtlsdr-scanner-v4/source`).
Shure, Wireless Workbench and Sennheiser are trademarks of their owners; this project is
not affiliated with them.

## По-русски

Сканер радиоэфира для радиомикрофонов и in-ear-систем на недорогом донгле RTL-SDR Blog V4.
Скан экспортируется в Shure Wireless Workbench (`.sdb3`, `.sdb2`, `.csv`) для подбора частот.
Это форк программы nocarryr/rtlsdr-wwb-scanner, доработанный для RTL-SDR Blog V4 под
Windows: быстрее, без собственных помех донгла в скане, с автоусилением при перегрузке,
с диапазонами систем Shure/Sennheiser и правилами стран Европы.

- Установка — шаги 1–5 выше (раздел «Installation»).
- Обновление — команда из раздела «Updating»; переход со старой версии из PyPI — раздел
  «Switching from the original…». Сканы, калибровки и настройки при этом сохраняются.
- Проверка на своём ПК с донглом — архив `qa-kit` из релиза, `python qa_pc.py --smoke`.
- Правила стран — подсказка для планирования, а не юридический документ: перед шоу
  сверяйтесь с регулятором (в Польше — UKE).
- V21.2: «Check peaks with less gain» — пики рядом с двумя сильными несущими (свои
  микрофоны у антенны) перечитываются с усилением на 10 дБ ниже. Продукт самого донгла
  при этом падает примерно вдвое сильнее, настоящий сигнал остаётся на месте. Продукты
  отмечаются розовой меткой «dongle product», из скана и файлов не удаляются.
- V21.3: установка на Python 3.7–3.14 современным pip; pip сам подбирает библиотеки под
  версию Python. Окно пока работает только на Python 3.7–3.10 (PySide2); на 3.11–3.14 —
  ядро, тесты и экспорт, окно появится с переходом на PySide6. Для pyrtlsdr 0.4+
  (Python 3.10+) нужен драйвер `rtlsdr-scanner-v7` — его ставит
  `drivers\rtlsdr-scanner-v7\install_dll.py`.
- V21.3.1: на Python 3.13 обработка плитки была в 6–8 раз медленнее (scipy 1.16+ считает
  welch по одному сегменту за раз); теперь своя функция Welch с тем же результатом.
  install_dll.py больше не трогает DLL другого Python, а ставит v7 в это окружение.
  v7 проверен на V4 15.11.2026: скорость как у v6, выдёргивание донгла посреди скана —
  скан продолжается без потерь.
- V21.4: окно работает и на PySide6 (Qt 6): на Python 3.11–3.15 pip ставит PySide6
  (только Essentials + Addons, около 125 МБ), на 3.7–3.10 остаётся PySide2. Один код для
  обоих; диалоги файлов и цвета и сохранённые настройки — через модуль WwbCompat.
  Проверено на V4 16–18.11.2026 (Python 3.13 и 3.7, ручные проверки, экспорт; WSM открыл наш файл).
- V21.4.1: в консоли — время проверки пиков и список проверенных пиков; счётчики I2C тюнера
  ещё раз перед закрытием донгла, если были повторы.
- V22.0–V22.3: новый фундамент по аудиту архитектуры 16.11.2026; что и как измеряется, не
  меняется. Нужен Python 3.11+ и PySide6 (для Python 3.7–3.10 — V21.4.1). Одна папка данных
  `%USERPROFILE%\wwb_scanner_data` (или переменная `WWB_SCANNER_DATA`): история, калибровки,
  один журнал, а теперь и настройки окна — INI-файл вместо реестра (при первом запуске они
  копируются из реестра). Настройки по умолчанию — в ядре; без tinydb. В ядре появились
  служба скана и события, сканы из нескольких проходов (основа режима «Точный») и
  интерфейс приёмника (основа для других приёмников). Проверено на V4 18.11.2026.
- V22.8: надёжность (внешний аудит 22.11.2026). Донгл, который не принял настройки, закрывается,
  скан кончается ошибкой, следующий скан не зависает. Stop во время открытия донгла останавливает
  скан до первой плитки. Закрытие окна во время скана останавливает его и ждёт (программа падала).
  Сторож: скан стоит 60 с — стеки всех потоков в журнал. Донгл с зависшим вызовом USB не
  закрывается. Усиление не вернулось после автоусиления — скан останавливается с причиной.
  Проверка пиков ограничена временем, а не 30 плитками. Диапазон 0,5–1766 МГц; скан без точек —
  ошибка, плитки без данных — предупреждение. sdb3: разрывы = NoDataValue. В истории — метки
  скана и сырые уровни.
- V22.7: «Own gain per band» в Scan settings (пока выключено, до проверки на V4). Короткая проба
  перед сканом поднимает усиление в тихих полосах (например, 560–700 МГц на 49,6 дБ вместо 33,8),
  уровни приводятся к усилению скана по реальным шагам — несущие не «прыгают» на краях полос.
  Используются только ступени с калибровкой пола.
- V22.6: три окна БПФ (Hann, Blackman-Harris, Flat-top), считаются самой программой — те же
  значения, что у scipy; scipy больше не нужна, портативная версия меньше.
- V22.5: портативная версия для Windows без установки Python — папка с `wwb_scanner.exe`
  (zip в каждом релизе). Все данные — в папке `data` рядом с exe, флешка переносит их между ПК.
  Драйвер WinUSB (Zadig) нужен один раз на каждом ПК. Проверка без скана: `wwb_scanner.exe --selftest`.
- V22.4: калибровки у каждого донгла свои — в папке `calibration\sn-<серийный номер>`.
  Используются калибровки открытого (или выбранного в Device Settings) донгла, Scan settings
  показывает, чьи. Калибровки, сделанные до V22.4, достаются первому донглу после обновления
  (копируются: V22.3 их по-прежнему видит). У многих донглов, и у V4, заводской серийный номер
  00000001: два таких донгла не различить — программа предупредит; дайте каждому свой номер
  (`rtl_eeprom -s`).

[osmosdr-wiki]: https://osmocom.org/projects/rtl-sdr/wiki
