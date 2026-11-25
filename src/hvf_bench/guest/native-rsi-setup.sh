#!/bin/sh
B=/bin/busybox
$B chroot /mnt/agl /bin/sh -c 'export LD_LIBRARY_PATH=/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib;cd /lge/app_ro/rsi-gateway;exec ./rsi-gateway -rsi-server.keepAlive.enabled=true -rsi-server.thread.max=256 --log-level=0' </dev/null >/mnt/agl/tmp/rsi-gateway-native.log 2>&1 3>&- &
echo "ORIGINAL_RSI_GATEWAY_PID=$!"
touch_pid=
for process in /proc/[0-9]*/exe; do
 if [ "$($B readlink "$process")" = /mnt/agl/lge/app_ro/hmi/touch-app ]; then touch_pid=${process%/exe};touch_pid=${touch_pid##*/};break;fi
done
if [ -z "$touch_pid" ]; then
 identity=$($B sha256sum /mnt/agl/lge/app_ro/hmi/touch-app)
 case "$identity" in e1bb4059e85b0ffc1108f66b0a9e23f5a1ebec590804602ebf00ab67c47256c9\ *) ;; *) echo ORIGINAL_TOUCH_IDENTITY_MISMATCH;exit 11;;esac
 $B cp /libunified-touch.so /mnt/agl/tmp/libunified-touch.so
 [ -p /mnt/agl/tmp/bench-touch-events ] || $B mkfifo /mnt/agl/tmp/bench-touch-events
 $B mkdir -p /mnt/agl/data/lge/touch-app
 $B chroot /mnt/agl /bin/sh -c 'export LD_LIBRARY_PATH=/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib;export LD_PRELOAD=/tmp/libunified-touch.so;exec /lge/app_ro/hmi/touch-app -rsi-log_level=error' </dev/null >/mnt/agl/tmp/touch-app-native.log 2>&1 3>&- &
 echo "ORIGINAL_TOUCH_APP_PID=$!"
 $B timeout 10 $B sh -c 'printf "geometry\n" > /mnt/agl/tmp/bench-touch-events' || exit 12
else
 echo "ORIGINAL_TOUCH_REUSED_PID=$touch_pid"
fi
