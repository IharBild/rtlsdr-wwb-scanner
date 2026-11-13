# Drivers

`rtlsdr-scanner-v4/` - rtlsdr.dll for RTL-SDR Blog V4, scanner build (x64 and x86),
with command line tools, sources (patches 0001-0005 on rtl-sdr-blog aed0ea1), USB model,
A/B test script `dll_ab_test.py` and SHA256SUMS. Read `README_RU.txt` first.

Status: built 02.11.2026, checked on the USB model and in Wine, A/B-tested on a real
RTL-SDR Blog V4 on 04.11.2026 (retune 58 -> 23 ms, PLL always locked) and in use since then.

Known: pyrtlsdr 0.4.0 and newer needs `rtlsdr_set_dithering`, which neither the stock
RTL-SDR Blog DLL nor this build exports -> keep `pyrtlsdr<0.4`, or add a stub (patch 0006).

`rtlsdr-scanner-v5/` (V20.6) - the v4 build plus patch 0006 "i2c retry": a failed tuner
I2C transfer (`i2c wr failed`, LIBUSB_ERROR_PIPE) is repeated up to 3 times with the I2C
repeater switched on again, and `rtlsdr_get_i2c_stats()` reports retries/failures (the
scanner prints them). x64 only. `python install_dll.py` finds the DLL that pyrtlsdr loads,
keeps a copy `.before-v5` and installs; `python install_dll.py rollback` puts it back.
Status: built 10.11.2026, checked on the USB model (forced -9 errors recover, repeater
restored, retune still 9 USB transfers) and in Wine; NOT yet tested on the real dongle.
Tested on the real dongle 11-12.11.2026 (scans, gain survey: 20-23 retries, 0 failures).

`rtlsdr-scanner-v6/` - the v5 build plus the fixes of the driver audit of 12.11.2026
(patches 0007, 0008), only on failure paths: after a tuner transfer that needed a retry
(the tuner may have been reset) all tuner registers are written again from the driver's
copy and the PLL lock is checked again (`rtlsdr_get_tuner_restores()`, console line
"r82xx: tuner registers restored"); after a failed write the next write restores all
registers and skipping redundant writes works again; no retries once the dongle is gone
(NO_DEVICE); `rtlsdr_read_sync()` returns LIBUSB_ERROR_TIMEOUT after 2 s + twice the
reading time instead of blocking for ever (`rtlsdr_set_sync_read_timeout()`). Normal
retunes unchanged (9 USB transfers). `source/build_all.sh` builds everything from pinned
sources; two builds gave bit-identical binaries. x64 only.
Status: built 12.11.2026, checked on the USB model (`source/model`, results for v5 and v6
in `model_results.txt`) and in Wine; NOT yet tested on the real dongle.
Tested on the real dongle 13.11.2026 (qa_pc standard, no-antenna, window: 0 FAIL).

`rtlsdr-scanner-v7/` - the v6 build plus patch 0009: `rtlsdr_set_dithering()` and the GPIO
API of the librtlsdr fork (`rtlsdr_set_gpio_output/_input/_bit`, `rtlsdr_get_gpio_bit`,
`rtlsdr_set_gpio_byte`, `rtlsdr_get_gpio_byte`, `rtlsdr_set_gpio_status`), which pyrtlsdr
0.4+ binds when it is imported (0.5.1 runs on Python 3.10+). Dithering stays on by default;
all v6 scenarios of the USB model give the same results. `install_dll.py` also installs into
a new environment (`<venv>\Scripts\rtlsdr.dll`, found through `wwb_scanner/dllpath.py`).
Status: built 13.11.2026, checked on the USB model and in Wine with Windows Python 3.13 and
pyrtlsdr 0.5.1; NOT yet tested on the real dongle.

Licenses: rtl-sdr GPL-2.0-or-later, libusb LGPL-2.1 (see `source/`).
