#!/bin/sh
# usage: build.sh <srcdir> <outdir>
set -e
SRC=$1; OUT=$2; LU=/home/claude/work/dll/libusb-win
mkdir -p $OUT
x86_64-w64-mingw32-gcc -O2 -shared -o $OUT/rtlsdr.dll \
  -DBUILD_SHARED_LIBS -Drtlsdr_EXPORTS -I$SRC/include -I$LU/include \
  $SRC/src/librtlsdr.c $SRC/src/tuner_e4k.c $SRC/src/tuner_fc0012.c $SRC/src/tuner_fc0013.c \
  $SRC/src/tuner_fc2580.c $SRC/src/tuner_r82xx.c \
  $LU/MinGW64/static/libusb-1.0.dll.a -Wl,--out-implib,$OUT/librtlsdr.dll.a \
  -static-libgcc 2>&1 | grep -v "^$" | head -30
cp $LU/MinGW64/dll/libusb-1.0.dll $OUT/
ls -la $OUT
