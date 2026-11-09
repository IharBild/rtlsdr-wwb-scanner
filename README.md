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
- **Clean scan.** Removes the dongle's own spurs (multiples of 28.8 MHz and 480 MHz) and the
  tile ripple, levels the noise floor with a calibration made without an antenna, and
  re-measures overloaded tiles at a lower gain (auto gain).
- **Strong signal next to the antenna** (own mic / IEM, auto gain on). The gain of a clipped
  tile goes down as far as needed (to 0 dB), so the carrier level is right; tiles up to
  4 MHz around it are read again at that gain too (the dongle clips inside there without
  showing it); both readings are merged so the false peaks and the raised floor around the
  carrier go away where the normal reading is usable. The status line names the band that
  lost sensitivity and by how many dB. All tiles of such a zone use one gain; products seen
  by one tile only are taken from the others; tiles made deaf up to 10 MHz away are raised by
  the loss; images of the carrier at +-2.4 MHz and +-28.8 MHz are replaced by the floor around
  them and named in the status line. Points that no reading can measure show the flat
  floor of the zone instead of false spikes; the graph shades the bands that lost
  sensitivity ("-NN dB sens.") and marks the removed images. Images of a strong carrier
  are removed even when it does not clip (dips between TV channels are not counted as
  lost sensitivity). Both kinds of marks can be turned off in the View menu ("Lost
  sensitivity bands", "Removed image marks"); the choice is kept between runs.
  Where the dongle clips even at its lowest gain the graph marks the band "not
  measurable" (red; the data stays in the file, so WWB keeps away from it). Dips
  inside a TV multiplex or DAB block are not counted as lost sensitivity.
- **USB errors**: after a USB error the dongle is opened again and the scan goes on
  (once per scan); a new scan waits up to 10 s for a dongle that is "not found or
  busy"; a tile that takes over 15 s (normally 0.1 s) stops the scan with a message.
  File > Export asks first while the scan in the graph is still running.
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
