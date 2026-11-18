#!/bin/sh
# Test environments of the Linux sandbox (V22.0: Python 3.11+ and PySide6).
# Idempotent: what is already there is kept. Log: /tmp/logs/setup.log
#
#   sh tools/sandbox_setup.sh            venvs + driver sources + USB model
#   sh tools/sandbox_setup.sh venv       only the venvs
#
# Result:
#   /tmp/venv313qt  Python 3.13, PySide6 6.12, numpy 2, scipy, pyrtlsdr 0.5
#                   (the stack of the maintainer's PC since 18.11.2026)
#   /tmp/venv311    Python 3.11 (oldest supported), PySide6 6.11.3
#   /tmp/drvsrc/drv, /tmp/drvsrc/libusb-src
#                   rtl-sdr-blog + patches 0001-0009, libusb v1.0.29
#   /tmp/mocklib/librtlsdr.so
#                   our driver on the USB model (drivers/.../model/mock_libusb.c)
# Then, for the end-to-end tests and the qa-kit check on the model:
#   export DRV_V7_SRC=/tmp/drvsrc/drv LIBUSB_SRC=/tmp/drvsrc/libusb-src
#   LD_LIBRARY_PATH=/tmp/mocklib python ...
# Needs uv, gcc, git and network access to PyPI and github.com.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p /tmp/logs
LOG=/tmp/logs/setup.log
exec >>"$LOG" 2>&1
echo "=== $(date) sandbox_setup $*"

for spec in 313qt:3.13 311:3.11; do
	name=${spec%%:*}; py=${spec##*:}
	# PySide6 6.12.0 aborts on Python 3.11 (message handler, see setup.cfg)
	qtv='==6.12.0'; [ "$py" = 3.11 ] && qtv='==6.11.3'
	PKGS="numpy scipy pyrtlsdr>=0.4 PySide6-Essentials$qtv PySide6-Addons$qtv pytest pyflakes vermin coverage"
	v=/tmp/venv$name
	if [ ! -x "$v/bin/python" ]; then
		uv venv -q -p "$py" "$v"
	fi
	want=${qtv#==}
	if ! "$v/bin/python" -c "import PySide6, numpy, scipy, pytest; assert PySide6.__version__ == '$want'" 2>/dev/null; then
		# shellcheck disable=SC2086
		uv pip install -q -p "$v/bin/python" $PKGS
	fi
	echo "venv$name: $("$v/bin/python" -c 'import sys, PySide6, numpy, scipy; print(sys.version.split()[0], "PySide6", PySide6.__version__, "numpy", numpy.__version__, "scipy", scipy.__version__)')"
done
[ "$1" = venv ] && exit 0

SRC=/tmp/drvsrc
mkdir -p "$SRC"
cd "$SRC"
export GIT_COMMITTER_NAME=rtlsdr-wwb-scanner GIT_COMMITTER_EMAIL=noreply@example.invalid
if [ ! -f drv/.patched ]; then
	rm -rf drv
	git clone -q https://github.com/rtlsdrblog/rtl-sdr-blog drv
	git -C drv checkout -q aed0ea19f3a273370a13c9009b96313c75d54c7b
	git -C drv am -q --committer-date-is-author-date "$HERE"/drivers/rtlsdr-scanner-v7/source/patches/*.patch
	touch drv/.patched
fi
[ -d libusb-src ] || git clone -q --depth 1 --branch v1.0.29 https://github.com/libusb/libusb libusb-src

mkdir -p /tmp/mocklib
MODEL="$HERE/drivers/rtlsdr-scanner-v7/source/model"
if [ ! -f /tmp/mocklib/librtlsdr.so ] || [ "$MODEL/mock_libusb.c" -nt /tmp/mocklib/librtlsdr.so ]; then
	gcc -O1 -shared -fPIC -o /tmp/mocklib/librtlsdr.so \
		-I drv/include -I drv/src -I libusb-src/libusb \
		drv/src/librtlsdr.c drv/src/tuner_*.c "$MODEL/mock_libusb.c" -lm
fi
echo "mocklib: $(ls -l /tmp/mocklib/librtlsdr.so)"
echo "=== done"
