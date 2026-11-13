#!/bin/sh
# USB model of the driver (fake libusb, RTL2832U + R828D "Blog V4"), native gcc,
# with AddressSanitizer and UndefinedBehaviorSanitizer.
#
#   sh run_model.sh <driver source dir after git am> <libusb source dir> [v5]
#
# "v5" / "v6" build the same scenarios without the API calls of the later
# versions, to run them against older sources (git checkout of patch 0006 /
# 0008) for comparison.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
D=$(cd "$1" && pwd)
L=$(cd "$2" && pwd)/libusb
F="-DSCANNER_V6 -DSCANNER_V7"
[ "$3" = v6 ] && F="-DSCANNER_V6"
[ "$3" = v5 ] && F=""
T=$(mktemp -d)
sed "s#../VARIANT/src/librtlsdr.c#$D/src/librtlsdr.c#" "$HERE/test_retune.c" > "$T/test_retune.c"
gcc -g -O1 -fsanitize=address,undefined -fno-sanitize-recover=undefined -fno-omit-frame-pointer \
	-DSCANNER_V4 -DSCANNER_V5 $F -I"$D/include" -I"$D/src" -I"$L" \
	"$T/test_retune.c" "$D"/src/tuner_*.c "$HERE/mock_libusb.c" -lm -o "$T/test_retune"
"$T/test_retune"
rm -rf "$T"
