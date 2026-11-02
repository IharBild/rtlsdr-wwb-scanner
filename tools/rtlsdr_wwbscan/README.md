# rtlsdr.dll "wwbscan" (scan edition)

Base: https://github.com/rtlsdrblog/rtl-sdr-blog at commit aed0ea19f3a273370a13c9009b96313c75d54c7b (GPL-2.0).
libusb: 1.0.27 Windows binaries (MinGW64) from https://github.com/libusb/libusb/releases (LGPL-2.1).
Changes: `wwbscan.patch` (apply with `patch -p1` inside the rtl-sdr-blog checkout).

What changed
- Fast retune (`rtlsdr_set_fast_tune`, off by default): after init, tuner register writes whose value
  is already in the shadow copy are skipped; PLL registers 0x14-0x16 go out in one I2C transfer;
  up to 6 lock checks instead of 2; the I2C repeater is not rewritten when already on.
  Harness (470-1000 MHz, 684 retunes, fake I2C): 19 -> 7 register writes, 23 -> 11 USB control
  transfers per retune (+2 for the repeater in stock), identical final tuner register state.
- `rtlsdr_get_tuner_lock`: 1 / 0 / -1 after the last retune. Stock library returns success from
  `rtlsdr_set_center_freq` even when the R82xx PLL did not lock.
- `rtlsdr_set_read_timeout`: timeout for `rtlsdr_read_sync` (stock: wait forever).
- `rtlsdr_wwbscan_version`: "wwbscan-1".

Build (Linux, cross-compile): apt install gcc-mingw-w64-x86-64; unpack libusb-1.0.27.7z; edit LU in build.sh;
`./build.sh <patched source dir> <out dir>`. Smoke test under wine: smoke.c.
