BB=/bin/busybox
ROOT=/mnt/agl
$BB mkdir -p "$ROOT/tmp/bench-update"
if $BB pidof swup >/dev/null; then
 echo ORIGINAL_SWUP_ALREADY_RUNNING
 exit 0
fi
(
 $BB unshare -pf $BB chroot "$ROOT" /bin/sh -c 'export LD_LIBRARY_PATH=/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib; cd /lge/app_ro/update/swup; exec ./swup' </dev/null >"$ROOT/tmp/bench-update/swup.log" 2>&1
 echo "$?" >"$ROOT/tmp/bench-update/swup.exit"
) </dev/null >"$ROOT/tmp/bench-update/launcher.log" 2>&1 3>&- &
echo "$!" >"$ROOT/tmp/bench-update/launcher.pid"
echo ORIGINAL_SWUP_LAUNCHER=$!
