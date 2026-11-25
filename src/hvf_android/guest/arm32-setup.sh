#!/bin/sh
B=${BENCH_BUSYBOX:-/bin/busybox}
root=${1:-}
case "$root" in ''|/android) ;; *) exit 2;; esac
entry=/proc/sys/fs/binfmt_misc/bench-arm32
if [ ! -e /proc/sys/fs/binfmt_misc/register ]; then
 "$B" mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc || exit 3
fi
if [ ! -e "$entry" ]; then
 "$B" printf '%s' ':bench-arm32:M::\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x28\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:'"$root"'/bench/arm32-exec:FP' >/proc/sys/fs/binfmt_misc/register || exit 4
fi
"$B" cat "$entry"
