#!/bin/sh
B=/bin/busybox
ROOT=/mnt/agl
seed_car_coding() {
 coding=/bench-ric-id3-coding.bin
 [ -f "$coding" ] || coding="$ROOT/tmp/bench-ric-id3-coding.bin"
 [ -f "$coding" ] || { echo NAVIGATION_CAR_CODING_MISSING;return 12; }
 [ "$($B wc -c <"$coding" | $B tr -d ' ')" = 25 ] || return 12
 hex=$($B od -An -v -tx1 "$coding" | $B tr -d ' \n' | $B tr a-f A-F)
 [ "${#hex}" = 50 ] || return 12
 db=/data/lge/navi/persist/nav.db
 sql="BEGIN IMMEDIATE; CREATE TABLE IF NOT EXISTS Persistence (ns INTEGER NOT NULL,key INTEGER NOT NULL,slot INTEGER NOT NULL,data BLOB NOT NULL,CONSTRAINT PK_Persistence PRIMARY KEY (ns,key,slot)); CREATE TABLE IF NOT EXISTS SecureFS (path TEXT NOT NULL,offset INTEGER NOT NULL,size INTEGER NOT NULL,data BLOB NOT NULL,tag TEXT NOT NULL,CONSTRAINT PK_SecureFS PRIMARY KEY (path,offset)); INSERT OR IGNORE INTO Persistence(ns,key,slot,data) VALUES(33554432,1536,1,X'$hex'); COMMIT; SELECT hex(data) FROM Persistence WHERE ns=33554432 AND key=1536 AND slot=1;"
 actual=$($B chroot "$ROOT" /usr/bin/sqlite3 "$db" "$sql") || return 12
 [ "$actual" = "$hex" ] || { echo NAVIGATION_CAR_CODING_CONFLICT;return 12; }
 echo ORIGINAL_NAVIGATION_CAR_CODING_IMPORTED=33554432:1536:1:25
}
seed_car_coding || exit $?
$B cp /navigation-log.py "$ROOT/tmp/navigation-log.py" || exit 14
someip=
for process in /proc/[0-9]*/exe; do
 if [ "$($B readlink "$process")" = "$ROOT/lge/app_ro/someip-daemon/bin/someip-daemon" ]; then someip=${process%/exe};someip=${someip##*/};break;fi
done
if [ -z "$someip" ]; then
 identity=$($B sha256sum "$ROOT/lge/app_ro/someip-daemon/bin/someip-daemon")
 case "$identity" in cd05e28aac53cae16eb8c0a62a7e6bec7a8da8b7b5345a85b3a8a924965352a9\ *) ;; *) echo ORIGINAL_SOMEIP_IDENTITY_MISMATCH;exit 10;;esac
 $B chroot "$ROOT" /bin/sh -c '
  export LD_LIBRARY_PATH=/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib
  fifo=/tmp/bench-navigation/someip-log-$$.fifo
  /bin/busybox mkfifo "$fifo" || exit 14
  /usr/bin/python3 /tmp/navigation-log.py /tmp/someip-daemon.log "$fifo" <"$fifo" &
  exec /bin/bash /lge/app_ro/someip-daemon/run/launch_someipd.sh >"$fifo" 2>&1
 ' </dev/null >"$ROOT/tmp/someip-launch.log" 2>&1 3>&- &
 echo "ORIGINAL_SOMEIP_LAUNCH_PID=$!"
else
 echo "ORIGINAL_SOMEIP_REUSED_PID=$someip"
fi
$B rm -f "$ROOT/tmp/navigation-dbus.env" "$ROOT/tmp/navigation-dbus.env.next"
$B chroot "$ROOT" /bin/sh -c '
 export LD_LIBRARY_PATH=/lge/app_ro/lib64:/lge/app_ro/navi/navi-al/lib64:/usr/lib64:/usr/lib:/lib
 cd /lge/app_ro/navi/navi-al
 export XDG_RUNTIME_DIR=/run/platform/weston
 export COMMONAPI_CONFIG=/lge/app_ro/navi/navi-al/commonapi.ini
 export COMMONAPI_DBUS_CONFIG=/lge/app_ro/navi/navi-al/commonapi-dbus.ini
 eval "$(dbus-launch --sh-syntax)"
 export DBUS_SESSION_BUS_ADDRESS DBUS_SESSION_BUS_PID
 printf "DBUS_SESSION_BUS_ADDRESS=%s\nDBUS_SESSION_BUS_PID=%s\n" "$DBUS_SESSION_BUS_ADDRESS" "$DBUS_SESSION_BUS_PID" >/tmp/navigation-dbus.env.next
 mv /tmp/navigation-dbus.env.next /tmp/navigation-dbus.env || exit 8
 export LD_PRELOAD=/tmp/libnavigation-gnss.so
 exec ./alm
' </dev/null >"$ROOT/tmp/navigation-al.log" 2>&1 3>&- &
echo "ORIGINAL_NAVI_AL_PID=$!"
$B chroot "$ROOT" /bin/sh -c '
 export BRAND_TYPE=1 CAR_TYPE=1
 export LD_PRELOAD=/tmp/libnavigation-gnss.so
 export LD_LIBRARY_PATH=/lge/navi/naviengine/package/MXNavi:/lge/app_ro/navi/navi-al/lib64:/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib
 export XDG_RUNTIME_DIR=/run/platform/weston
 export WAYLAND_DISPLAY=wayland-0
 if [ -d /tmp/mesa ]; then
  export LD_PRELOAD=/usr/lib/libwayland-client.so.0:/tmp/libnavigation-gnss.so
  export LD_LIBRARY_PATH=/tmp/mesa/usr/lib/aarch64-linux-gnu:/tmp/mesa/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH
  export LIBGL_DRIVERS_PATH=/tmp/mesa/usr/lib/aarch64-linux-gnu/dri
  export __EGL_VENDOR_LIBRARY_FILENAMES=/tmp/mesa/usr/share/glvnd/egl_vendor.d/50_mesa.json
  if [ -f /tmp/bench-gpu-virgl ]; then
   unset LIBGL_ALWAYS_SOFTWARE GALLIUM_DRIVER
   export MESA_LOADER_DRIVER_OVERRIDE=virtio_gpu
  else
   export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
  fi
  export LP_NUM_THREADS=2 EGL_PLATFORM=wayland
 fi
 export COMMONAPI_CONFIG=/lge/app_ro/navi/navi-al/commonapi.ini
 export COMMONAPI_DBUS_CONFIG=/lge/app_ro/navi/navi-al/commonapi-dbus.ini
 navigation_preload=$LD_PRELOAD
 unset LD_PRELOAD
 for attempt in 1 2 3 4 5; do [ -f /tmp/navigation-dbus.env ] && break;sleep 1;done
 [ -s /tmp/navigation-dbus.env ] || exit 8
 . /tmp/navigation-dbus.env
 export DBUS_SESSION_BUS_ADDRESS DBUS_SESSION_BUS_PID
 cd /lge/navi/naviengine/package/MXNavi || exit 7
 fifo=/tmp/bench-navigation/engine-log-$$.fifo
 /bin/busybox mkfifo "$fifo" || exit 14
 /usr/bin/python3 /tmp/navigation-log.py /tmp/navigation-engine.log "$fifo" <"$fifo" &
 export LD_PRELOAD=$navigation_preload
 exec ./MXNavi /lge/navi/naviengine/package/MXNavi >"$fifo" 2>&1
' </dev/null >"$ROOT/tmp/navigation-launch.log" 2>&1 3>&- &
echo "ORIGINAL_MXNAVI_PID=$!"
