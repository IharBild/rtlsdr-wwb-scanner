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
(The dithering stub mentioned above would now be patch 0007.)

Licenses: rtl-sdr GPL-2.0-or-later, libusb LGPL-2.1 (see `source/`).
