#!/bin/sh
# Cross-build rtlsdr.dll (x64) for Windows, scanner-v7.
# Needs: mingw-w64 (x86_64-w64-mingw32-gcc), libusb built as a static mingw
# library (see build_all.sh, which does every step from the sources).
#
#   ./build_win.sh <driver source dir> <libusb prefix> <output dir>
#
# Reproducible: the date in the build string comes from SOURCE_DATE_EPOCH
# (build_all.sh sets it to the date of the last patch), the PE timestamp is
# not written, debug information is stripped.
set -e
SRC=${1:-drv}
LIBUSB=${2:-libusb-win}
OUT=${3:-out}
HOST=${HOST:-x86_64-w64-mingw32}
CC=$HOST-gcc
WINDRES=$HOST-windres
mkdir -p "$OUT"

REV=$(git -C "$SRC" rev-parse --short HEAD)
if [ -n "$SOURCE_DATE_EPOCH" ]; then
	DATE=$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%d)
else
	DATE=$(date -u +%Y-%m-%d)
fi
LIBUSB_VER=${LIBUSB_VER:-1.0.29}
INFO="rtl-sdr-blog aed0ea1 + osmocom fast retune (f5978e8 0b64f07 2acb75c) + scanner-v4 + i2c retry (scanner-v5) + tuner restore, read timeout (scanner-v6) + dithering, GPIO API (scanner-v7) $REV, libusb $LIBUSB_VER, mingw-w64 $HOST, $DATE"

# Export exactly the public API declared with RTLSDR_API in rtl-sdr.h
{
	echo "LIBRARY rtlsdr.dll"
	echo "EXPORTS"
	grep -o 'RTLSDR_API[^(]*(' "$SRC/include/rtl-sdr.h" |
		sed -E 's/.*[ *]([a-z0-9_]+)\($/\1/'
} > "$OUT/rtlsdr.def"

cat > "$OUT/rtlsdr.rc" <<EOF
1 VERSIONINFO
FILEVERSION 2,0,3,2
PRODUCTVERSION 2,0,3,2
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904b0"
    BEGIN
      VALUE "CompanyName", "osmocom / RTL-SDR Blog / rtlsdr-wwb-scanner"
      VALUE "FileDescription", "librtlsdr for RTL-SDR Blog V4, scanner build (v7, for pyrtlsdr 0.3-0.5)"
      VALUE "FileVersion", "scanner-v7 $REV"
      VALUE "InternalName", "rtlsdr"
      VALUE "LegalCopyright", "GPL-2.0-or-later"
      VALUE "OriginalFilename", "rtlsdr.dll"
      VALUE "ProductName", "rtl-sdr (scanner-v7)"
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
LDREPRO="-s -Wl,--no-insert-timestamp"

# The DLL: libusb, winpthreads and libgcc linked statically -> no extra DLLs needed
$CC $CFLAGS -DRTLSDR_BUILD_INFO="\"$INFO\"" -shared -o "$OUT/rtlsdr.dll" \
	"$SRC/src/librtlsdr.c" "$SRC"/src/tuner_*.c "$OUT/rtlsdr_res.o" "$OUT/rtlsdr.def" \
	-Wl,--out-implib,"$OUT/librtlsdr.dll.a" $LDREPRO \
	"$LIBUSB/lib/libusb-1.0.a" -static-libgcc -Wl,-Bstatic -lpthread -Wl,-Bdynamic

# Command line tools linked against the DLL (same driver code)
for t in rtl_test rtl_sdr rtl_power rtl_biast rtl_eeprom; do
	extra=""
	[ -f "$SRC/src/getopt/getopt.c" ] && extra="$SRC/src/getopt/getopt.c"
	$CC $CFLAGS -o "$OUT/$t.exe" "$SRC/src/$t.c" "$SRC/src/convenience/convenience.c" $extra \
		"$OUT/librtlsdr.dll.a" $LDREPRO -static-libgcc -Wl,-Bstatic -lpthread -Wl,-Bdynamic -lm
done

rm -f "$OUT/rtlsdr_res.o"
echo "$INFO"
