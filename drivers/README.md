# Drivers

`rtlsdr-scanner-v4/` - rtlsdr.dll for RTL-SDR Blog V4, scanner build (x64 and x86),
with command line tools, sources (patches 0001-0005 on rtl-sdr-blog aed0ea1), USB model,
A/B test script `dll_ab_test.py` and SHA256SUMS. Read `README_RU.txt` first.

Status (02.11.2026): built and checked on the USB model and in Wine; not yet run on a real
dongle. Install only after a good A/B test.

Known: pyrtlsdr 0.4.0 and newer needs `rtlsdr_set_dithering`, which neither the stock
RTL-SDR Blog DLL nor this build exports -> keep `pyrtlsdr<0.4`, or add a stub (patch 0006).

Licenses: rtl-sdr GPL-2.0-or-later, libusb LGPL-2.1 (see `source/`).
