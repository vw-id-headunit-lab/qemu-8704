#!/bin/sh
B=/bin/busybox
printf '0\n' > /proc/sys/net/ipv6/auto_flowlabels
while [ -e /sys/class/net/eth0.3/address ] && [ "$($B cat /sys/class/net/eth0.3/address)" = '52:54:00:87:04:01' ]; do
 $B ip link set eth0.3 up
 $B ip addr replace 10.87.4.1/24 dev eth0.3
 $B ip addr replace 192.168.0.108/24 dev eth0.3
 $B ip -6 addr replace fd53:7cb8:383:3::108/64 dev eth0.3
 if [ -f /navigation-uplink.conf ]; then
  if ! $B awk 'NR>1 && $2=="00000000" && $8=="00000000" {found=1} END {exit !found}' /proc/net/route; then
   if ! $B sh /navigation-network; then
    echo NAVIGATION_UPLINK_RECOVERY_FAILED
    exit 2
   fi
  fi
 fi
 $B sleep 2
done
