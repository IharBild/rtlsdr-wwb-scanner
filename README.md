# rtlsdr-wwb-scanner — RTL-SDR Blog V4 fork

RF scanner for wireless microphones and in-ear monitors. It scans the spectrum with an
inexpensive [RTL-SDR][osmosdr-wiki] dongle and exports the scan for
**Shure Wireless Workbench (WWB)** frequency coordination.

This is a fork of [nocarryr/rtlsdr-wwb-scanner](https://github.com/nocarryr/rtlsdr-wwb-scanner)
by Matthew Reid. It is maintained for the **RTL-SDR Blog V4** on **Windows**, for a rental
company in Poland and for sound engineers across Europe. Most of the code in this fork was
written with Claude (Anthropic); every release is checked on a real V4 dongle by the
maintainer.

Current release: **[v18.1](https://github.com/IharBild/rtlsdr-wwb-scanner/releases/tag/v18.1)**
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
  stay gaps: no invented values.
- **Sennheiser WSM.** *File > Export > Sennheiser WSM CSV* writes the layout of WSM's own
  receiver scans (EM 300-500 G3); WSM receiver scans can be imported and laid over an
  RTL-SDR scan. The level is dB above the floor x 1.3 (0..48), set from side-by-side scans
  with an EM 300 G3.
- **Window.** Dark theme, status line and error dialog, range presets, time left,
  spur marks, EU TV channel grid, regulation strip under the graph.
- **Safety and reliability.** Scan history stored without pickle, XML files with a DTD are
  refused, every uncaught error is shown in the window and written to the log, and the
  window never stays stuck in "scanning".
- **Tests.** A dongle simulator and about 350 automatic tests (no dongle needed), a window
  check and a test kit for a PC with a real dongle (`qa-kit` in the releases).

## Tested with

| | |
|---|---|
| Hardware | RTL-SDR Blog V4 (R828D tuner) |
| System | Windows 11, Python 3.7.8 64-bit |
| Libraries | numpy 1.21.6, scipy 1.7.3, pyrtlsdr 0.3.0, PySide2 5.15.2.1, tinydb 4.8.0 |
| Automatic tests | GitHub Actions: Linux and Windows, Python 3.8, simulator instead of the dongle |

PySide2 5.15 has no packages for Python 3.11 or newer: use Python 3.7–3.10, 64-bit.
Keep `pyrtlsdr` below 0.4 (0.4 needs a function that the V4 drivers do not export).

## Installation (Windows)

1. **Dongle driver.** Plug in the V4 and install the WinUSB driver with
   [Zadig](https://zadig.akeo.ie/) (standard RTL-SDR step).
2. **Python.** Install Python 3.8–3.10, 64-bit, then create a virtual environment:

   ```bat
   py -3.10 -m venv C:\wwb-scanner
   C:\wwb-scanner\Scripts\activate
   ```

3. **The scanner** from the source archive of this branch (no git needed):

   ```bat
   python -m pip install "pyrtlsdr<0.4" "numpy<2" ^
     https://github.com/IharBild/rtlsdr-wwb-scanner/archive/refs/heads/v4-fixes.zip
   ```

   For a fixed release use `.../archive/refs/tags/v18.1.zip` instead.

4. **rtlsdr.dll.** Copy `drivers\rtlsdr-scanner-v4\x64\rtlsdr.dll` from the source
   archive into `C:\wwb-scanner\Scripts` (the folder of `python.exe`).
   `python drivers\rtlsdr-scanner-v4\dll_ab_test.py where` shows which DLL is loaded.
5. **Start:** `C:\wwb-scanner\Scripts\wwb_scanner-ui`. You can make a desktop shortcut to it.

A one-click Windows installer is planned.

### Updating

The same command with `--force-reinstall --no-deps` replaces the program files and keeps
your libraries, scans, calibrations and settings (they live in `%USERPROFILE%\wwb_scanner_data`):

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
rem 3. install this fork without touching numpy / scipy / PySide2 / pyrtlsdr
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
python -m pytest tests          # about 350 tests, dongle simulator
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

[osmosdr-wiki]: https://osmocom.org/projects/rtl-sdr/wiki
