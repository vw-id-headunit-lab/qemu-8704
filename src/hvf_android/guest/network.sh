#!/bench/busybox sh
B=/bench/busybox
printf '0\n' > /proc/sys/net/ipv6/auto_flowlabels
$B mkdir -p /dev/net
[ -e /dev/net/tun ] || $B mknod /dev/net/tun c 10 200
while :; do
 for port in /sys/class/virtio-ports/*/name; do
  if [ "$($B cat "$port")" = bench.network ]; then node=${port%/name};serial=/dev/${node##*/};break;fi
 done
 [ -n "${serial:-}" ] && break
 $B sleep 1
done
/bench/domain-tap "$serial" >/dev/console 2>&1 &
tap_pid=$!
trap 'kill "$tap_pid" 2>/dev/null;wait "$tap_pid"' EXIT TERM INT
while kill -0 "$tap_pid" 2>/dev/null; do
 for node in /sys/class/net/*/address; do
  [ "$($B cat "$node")" = '52:54:00:87:04:02' ] || continue
  interface=${node%/address};interface=${interface##*/}
  $B ip link set "$interface" up
  $B ip addr replace 10.87.4.2/24 dev "$interface"
  $B ip addr replace 192.168.0.99/24 dev "$interface"
  /system/bin/ip route replace 192.168.0.0/24 dev "$interface" src 192.168.0.99 table main
  if ! /system/bin/ip rule show | $B grep -q "9001:.*192.168.0.0/24"; then
   /system/bin/ip rule add priority 9001 to 192.168.0.0/24 lookup main
  fi
  $B ip -6 addr replace fd53:7cb8:383:3::99/64 dev "$interface"
  /system/bin/ip -6 route replace fd53:7cb8:383:3::/64 dev "$interface" table main
  /system/bin/ip route replace 10.87.4.0/24 dev "$interface" table main
  if ! /system/bin/ip -6 rule show | $B grep -q '9000:.*fd53:7cb8:383:3::/64'; then
   /system/bin/ip -6 rule add priority 9000 to fd53:7cb8:383:3::/64 lookup main
  fi
  if ! /system/bin/ip rule show | $B grep -q '9000:.*10.87.4.0/24'; then
   /system/bin/ip rule add priority 9000 to 10.87.4.0/24 lookup main
  fi
 done
 $B sleep 5
done
wait "$tap_pid"
