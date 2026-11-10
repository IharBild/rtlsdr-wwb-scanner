#!/bin/sh
# Cross-build rtlsdr.dll (x64) for Windows from the scanner-v4 branch.
# Needs: mingw-w64 (x86_64-w64-mingw32-gcc), libusb built as a static mingw
# library (./configure --host=x86_64-w64-mingw32 --enable-static --disable-shared).
#
#   ./build_win.sh <driver source dir> <libusb prefix> <output dir>
set -e
SRC=${1:-drv}
LIBUSB=${2:-libusb-win}
OUT=${3:-out}
HOST=${HOST:-x86_64-w64-mingw32}
CC=$HOST-gcc
WINDRES=$HOST-windres
mkdir -p "$OUT"

REV=$(git -C "$SRC" rev-parse --short HEAD)
DATE=$(date -u +%Y-%m-%d)
INFO="rtl-sdr-blog aed0ea1 + osmocom fast retune (f5978e8 0b64f07 2acb75c) + scanner-v4 + i2c retry (scanner-v5) $REV, libusb 1.0.29, mingw-w64 $HOST, $DATE"

# Export exactly the public API declared with RTLSDR_API in rtl-sdr.h
{
	echo "LIBRARY rtlsdr.dll"
	echo "EXPORTS"
	grep -o 'RTLSDR_API[^(]*(' "$SRC/include/rtl-sdr.h" |
		sed -E 's/.*[ *]([a-z0-9_]+)\($/\1/'
} > "$OUT/rtlsdr.def"

cat > "$OUT/rtlsdr.rc" <<EOF
1 VERSIONINFO
FILEVERSION 2,0,3,1
PRODUCTVERSION 2,0,3,1
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904b0"
    BEGIN
      VALUE "CompanyName", "osmocom / RTL-SDR Blog / rtlsdr-wwb-scanner"
      VALUE "FileDescription", "librtlsdr for RTL-SDR Blog V4, scanner build (v5, I2C retry)"
      VALUE "FileVersion", "scanner-v5 $REV"
      VALUE "InternalName", "rtlsdr"
      VALUE "LegalCopyright", "GPL-2.0-or-later"
      VALUE "OriginalFilename", "rtlsdr.dll"
      VALUE "ProductName", "rtl-sdr (scanner-v5)"
      VALUE "ProductVersion", "$REV"
      VALUE "Comments", "$INFO"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
EOF
$WINDRES "$OUT/rtlsdr.rc" -O coff -o "$OUT/rtlsdr_res.o"

CFLAGS="-O2 -Wall -Wno-unused -Wno-pointer-sign -Drtlsdr_EXPORTS -I$SRC/include -I$LIBUSB/include/libusb-1.0"

# The DLL: libusb, winpthreads and libgcc linked statically -> no extra DLLs needed
$CC $CFLAGS -DRTLSDR_BUILD_INFO="\"$INFO\"" -shared -o "$OUT/rtlsdr.dll" \
	"$SRC/src/librtlsdr.c" "$SRC"/src/tuner_*.c "$OUT/rtlsdr_res.o" "$OUT/rtlsdr.def" \
	-Wl,--out-implib,"$OUT/librtlsdr.dll.a" \
	"$LIBUSB/lib/libusb-1.0.a" -static-libgcc -Wl,-Bstatic -lpthread -Wl,-Bdynamic

# Command line tools linked against the DLL (same driver code)
for t in rtl_test rtl_sdr rtl_power rtl_biast rtl_eeprom; do
	extra=""
	[ -f "$SRC/src/getopt/getopt.c" ] && extra="$SRC/src/getopt/getopt.c"
	$CC $CFLAGS -o "$OUT/$t.exe" "$SRC/src/$t.c" "$SRC/src/convenience/convenience.c" $extra \
		"$OUT/librtlsdr.dll.a" -static-libgcc -Wl,-Bstatic -lpthread -Wl,-Bdynamic -lm
done

rm -f "$OUT/rtlsdr_res.o"
echo "$INFO"
