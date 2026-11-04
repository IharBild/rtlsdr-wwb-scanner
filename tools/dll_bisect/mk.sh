#!/bin/sh
# mk.sh <src> <out> <label> [extra CFLAGS]
set -e
SRC=$1; OUT=$2; LABEL=$3; shift 3
LU=${LIBUSB_WIN:-libusb-win}
CC=x86_64-w64-mingw32-gcc
mkdir -p $OUT
INFO="bisect-$LABEL: rtl-sdr-blog aed0ea1 $LABEL, libusb 1.0.29 static, mingw-w64, 2026-11-04"
{ echo "LIBRARY rtlsdr.dll"; echo "EXPORTS"; grep -o 'RTLSDR_API[^(]*(' "$SRC/include/rtl-sdr.h" | sed -E 's/.*[ *]([a-z0-9_]+)\($/\1/'; } > $OUT/rtlsdr.def
$CC -O2 -w -Drtlsdr_EXPORTS -I$SRC/include -I$LU/include "$@" -DRTLSDR_BUILD_INFO="\"$INFO\"" -shared -o $OUT/rtlsdr.dll \
  $SRC/src/librtlsdr.c $SRC/src/tuner_*.c $OUT/rtlsdr.def $LU/MinGW64/static/libusb-1.0.a -static-libgcc -Wl,-Bstatic -lpthread -Wl,-Bdynamic
rm $OUT/rtlsdr.def
x86_64-w64-mingw32-objdump -p $OUT/rtlsdr.dll | grep "DLL Name" | tr '\n' ' '; echo " $LABEL $(stat -c %s $OUT/rtlsdr.dll)"
