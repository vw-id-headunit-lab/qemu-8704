#!/bin/sh
B=/bin/busybox
[ -f /navigation-uplink.conf ] || exit 2
. /navigation-uplink.conf
link=
for n in /sys/class/net/*; do
 [ "$($B cat "$n/address")" != "$NAV_UPLINK_MAC" ] || link=${n##*/}
done
[ -n "$link" ] || { echo NAVIGATION_UPLINK_DEVICE_MISSING; exit 2; }
$B ip link set "$link" up || exit 2
if ! $B ip -4 addr show dev "$link" | $B grep -q "inet $NAV_UPLINK_ADDRESS/24 "; then
 $B ip addr add "$NAV_UPLINK_ADDRESS/24" dev "$link" || exit 2
fi
route=$($B ip -4 route show default)
case "$route" in
 "") $B ip route add default via "$NAV_UPLINK_GATEWAY" dev "$link" || exit 2 ;;
 "default via $NAV_UPLINK_GATEWAY dev $link"*) ;;
 *) echo NAVIGATION_UPLINK_DEFAULT_ROUTE_CONFLICT;exit 2 ;;
esac
[ "$($B readlink /mnt/agl/etc/resolv.conf)" = /var/run/resolv.conf ] || exit 2
$B mkdir -p /mnt/agl/var/run || exit 2
resolver=/mnt/agl/var/run/resolv.conf
if [ -e "$resolver" ] && ! $B grep -qx "nameserver $NAV_UPLINK_DNS" "$resolver"; then
 echo NAVIGATION_UPLINK_RESOLVER_CONFLICT;exit 2
fi
printf 'nameserver %s\n' "$NAV_UPLINK_DNS" >"$resolver" || exit 2
$B ip -4 route show default
$B cat "$resolver"
echo "NAVIGATION_UPLINK_CONFIGURED interface=$link mac=$NAV_UPLINK_MAC"
