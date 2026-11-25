#!/bin/sh
B=/bin/busybox
ROOT=/mnt/agl
for node in /sys/block/vd*/serial; do
 disk=${node%/serial};disk=/dev/${disk##*/}
 case "$($B cat "$node")" in
  bench-navi-core) core=$disk;;
  bench-navi-map) map=$disk;;
  bench-navi-data) data=$disk;;
 esac
done
[ -n "${core:-}" ] && [ -n "${map:-}" ] && [ -n "${data:-}" ] || exit 2
$B mkdir -p /mnt/navigation-map
$B mount -t ext4 -o ro,noload "$core" "$ROOT/lge/navi/naviengine" || exit 3
$B mount -t ext4 -o ro,noload "$map" /mnt/navigation-map || exit 4
$B mount -t ext4 -o noatime "$data" "$ROOT/navi_data" || exit 5
map_id=legacy
if [ -f /mnt/navigation-map/.bench-map-set-id ]; then
 map_id=$($B cat /mnt/navigation-map/.bench-map-set-id)
 case "$map_id" in *[!0-9a-f]*|'') exit 10;; esac
 [ "${#map_id}" -eq 64 ] || exit 10
fi
$B mkdir -p "$ROOT/navi_data/.bench/map-upper-$map_id" "$ROOT/navi_data/.bench/map-work-$map_id"
$B mount -t overlay overlay -o lowerdir=/mnt/navigation-map,upperdir="$ROOT/navi_data/.bench/map-upper-$map_id",workdir="$ROOT/navi_data/.bench/map-work-$map_id" "$ROOT/nav" || exit 6
$B mkdir -p "$ROOT/navi_data/.bench/alm-persist" "$ROOT/data/lge/navi/persist" "$ROOT/navi_data/MXNaviData/usrdata"
$B mount -o bind "$ROOT/navi_data/.bench/alm-persist" "$ROOT/data/lge/navi/persist" || exit 8
$B ln -s /navi_data/MXNaviData "$ROOT/var/MXNavi"
$B mkdir -p "$ROOT/var/lib"
$B ln -s /nav "$ROOT/var/lib/nav"
actual=$($B sha256sum "$ROOT/lge/app_ro/navi/navi-al/alm")
case "$actual" in bd1169e2a34f5f254b933c28efcc7e928571aa496373b20504a774b1cc7ecf41\ *) ;; *) echo ORIGINAL_ALM_IDENTITY_MISMATCH;exit 9;;esac
$B mkdir -p "$ROOT/tmp/bench-navigation"
$B cp /libnavigation-gnss.so "$ROOT/tmp/libnavigation-gnss.so"
$B cp /navigation-gnss-frame "$ROOT/tmp/bench-navigation/gnss-frame.bin"
if [ -f /navigation-motion-route.json ]; then
 $B cp /navigation-motion.py "$ROOT/tmp/bench-navigation/motion.py" || exit 13
 $B cp /navigation-motion-route.json "$ROOT/tmp/bench-navigation/motion.json" || exit 13
 $B chroot "$ROOT" /usr/bin/python3 /tmp/bench-navigation/motion.py /tmp/bench-navigation/motion.json /tmp/bench-navigation/gnss-frame.bin </dev/null >"$ROOT/tmp/bench-navigation/motion.log" 2>&1 3>&- &
 echo "VIRTUAL_ROAD_LOOP_LAUNCH_PID=$!"
fi
printf '0x31' >"$ROOT/tmp/bench-navigation/variant_id" || exit 11
printf '0x3' >"$ROOT/tmp/bench-navigation/display_id" || exit 11
$B sh /navigation-run
