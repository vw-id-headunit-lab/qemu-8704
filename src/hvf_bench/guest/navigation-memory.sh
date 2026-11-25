#!/bin/sh
B=/bin/busybox
if $B awk '$1 == "/dev/zram0" { found=1 } END { exit !found }' /proc/swaps; then
 echo NAVIGATION_SWAP_REUSED
 exit 0
fi
$B modprobe zram num_devices=1 || exit 2
[ -b /dev/zram0 ] || $B mdev -s
[ -b /dev/zram0 ] || exit 2
size=$($B cat /sys/block/zram0/disksize) || exit 2
case "$size" in
 0) printf '2147483648\n' >/sys/block/zram0/disksize || exit 2 ;;
 2147483648) ;;
 *) echo NAVIGATION_SWAP_SIZE_CONFLICT; exit 2 ;;
esac
$B mkswap /dev/zram0 || exit 2
$B swapon -p 100 /dev/zram0 || exit 2
$B awk '$1 == "/dev/zram0" && $3 == 2097148 { found=1; print "NAVIGATION_SWAP_ACTIVE size_kib=" $3 " used_kib=" $4 } END { exit !found }' /proc/swaps
