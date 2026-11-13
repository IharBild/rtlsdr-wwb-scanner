#!/bin/sh
# scanner-v7: every build step from the sources, reproducible.
# Checked on Ubuntu 24.04 with the mingw-w64 package (gcc-mingw-w64 13.2.0,
# x86_64-w64-mingw32-gcc "13-win32"); needs git, autotools, network access
# to github.com.
#
#   sh build_all.sh [work dir]      -> <work dir>/out/rtlsdr.dll + tools
#
# Pinned sources:
#   rtl-sdr-blog  aed0ea19f3a273370a13c9009b96313c75d54c7b + patches/0001-0009
#   libusb        v1.0.29 = 15a7ebb4d426c5ce196684347d2b7cafad862626
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${1:-build-v7}
RTL_COMMIT=aed0ea19f3a273370a13c9009b96313c75d54c7b
LIBUSB_TAG=v1.0.29
LIBUSB_COMMIT=15a7ebb4d426c5ce196684347d2b7cafad862626

mkdir -p "$WORK"
cd "$WORK"
WORK=$(pwd)

# Fixed committer -> the same commit hashes (and build string) on every run
export GIT_COMMITTER_NAME=rtlsdr-wwb-scanner
export GIT_COMMITTER_EMAIL=noreply@example.invalid

[ -d drv ] || git clone -q https://github.com/rtlsdrblog/rtl-sdr-blog drv
git -C drv checkout -q "$RTL_COMMIT"
git -C drv am -q --committer-date-is-author-date "$HERE"/patches/*.patch

[ -d libusb-src ] || git clone -q --depth 1 --branch "$LIBUSB_TAG" https://github.com/libusb/libusb libusb-src
got=$(git -C libusb-src rev-parse HEAD)
if [ "$got" != "$LIBUSB_COMMIT" ]; then
	echo "libusb $LIBUSB_TAG is $got, expected $LIBUSB_COMMIT" >&2
	exit 1
fi
if [ ! -f libusb-win/lib/libusb-1.0.a ]; then
	(cd libusb-src && ./autogen.sh >/dev/null 2>&1 || autoreconf -fi >/dev/null
	 ./configure -q --host=x86_64-w64-mingw32 --enable-static --disable-shared \
		--prefix="$WORK/libusb-win" && make -s && make -s install) >/dev/null
fi

SOURCE_DATE_EPOCH=$(git -C drv log -1 --format=%ct)
export SOURCE_DATE_EPOCH
sh "$HERE/build_win.sh" drv libusb-win out

{
	echo "rtl-sdr-blog $RTL_COMMIT + patches 0001-0009 -> $(git -C drv rev-parse HEAD)"
	echo "libusb $LIBUSB_TAG $LIBUSB_COMMIT"
	echo "$(x86_64-w64-mingw32-gcc --version | head -1)"
	echo "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH"
	# libusb-1.0.a itself differs with the build path (debug info); the
	# DLL and tools do not: debug info is stripped from them
	(cd out && sha256sum rtlsdr.dll rtl_*.exe)
} > out/BUILD_INFO.txt
cat out/BUILD_INFO.txt
