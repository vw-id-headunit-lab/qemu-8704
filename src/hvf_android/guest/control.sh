#!/bench/busybox sh
B=/bench/busybox
export PATH=/system/bin:/system/xbin:/vendor/bin:/bench
$B mount -o bind /bench/passwd /system/etc/passwd || exit 1
for p in /sys/class/virtio-ports/*/name; do
 if [ "$($B cat "$p")" = bench.control ]; then
  d=${p%/name};d=/dev/${d##*/}
  echo ANDROID_CONTROL_READY=$d >/dev/console
  exec 3<>"$d"
  while :; do $B sh <&3 >&3 2>&1; $B sleep 1; done
 fi
done
