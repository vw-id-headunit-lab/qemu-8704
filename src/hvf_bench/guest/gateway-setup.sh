# Opt-in RAM-only Go HTTP gateways. The QEMU link has restrict=on and only
# explicit guestfwd endpoints; no general Internet forwarding or hostfwd ports.
GUEST_GATEWAY_READY=false
echo 'GUEST_GATEWAY_SETUP_BEGIN'
$BB cp /http-gateway-go /mnt/agl/tmp/http-gateway-go || return 1
$BB cp /bench-tcp-relay /mnt/agl/tmp/bench-tcp-relay || return 1
$BB cp /gateway-probe.py /mnt/agl/tmp/gateway-probe.py || return 1
$BB chmod 700 /mnt/agl/tmp/http-gateway-go /mnt/agl/tmp/bench-tcp-relay || return 1
$BB modprobe virtio_net || return 1
$BB mdev -s
GATEWAY_LINK=
for net in /sys/class/net/*; do
    if [ "$($BB cat "$net/address")" = '52:54:00:42:00:15' ]; then GATEWAY_LINK=${net##*/}; fi
done
if [ -z "$GATEWAY_LINK" ]; then echo 'GUEST_GATEWAY_NO_VIRTIO_LINK'; return 1; fi
$BB ip link set "$GATEWAY_LINK" up || return 1
$BB ip addr add 10.0.2.15/24 dev "$GATEWAY_LINK" || return 1
# This /24 connected route is sufficient. Deliberately no default route or DNS.
echo "GUEST_GATEWAY_LOCAL_LINK=$GATEWAY_LINK endpoint=10.0.2.100 restrict=on"
$BB chroot /mnt/agl /tmp/bench-tcp-relay --target 10.0.2.100:30001 \
    --listen 127.0.0.1:30001 --listen 10.0.2.15:30001 --listen '[::1]:30001' \
    --listen '[fd53:7cb8:383:3::108]:30001' --ready-file /tmp/api-relay.ready \
    >/mnt/agl/tmp/api-relay.log 2>&1 &
API_RELAY_PID=$!
if [ -f /gateway-sdk-enabled ]; then
    $BB chroot /mnt/agl /tmp/bench-tcp-relay --target 10.0.2.100:50005 \
        --listen 127.0.0.1:50005 --listen '[::1]:50005' \
        --listen '[fd53:7cb8:383:3::108]:50005' --ready-file /tmp/sdk-relay.ready \
        >/mnt/agl/tmp/sdk-relay.log 2>&1 &
    SDK_RELAY_PID=$!
fi
for port in 50006 50007; do
    instance=b2c
    if [ "$port" = 50006 ]; then instance=b2b; fi
    $BB cat > "/mnt/agl/tmp/gateway-$instance.conf" <<CONF
proxyAddress="::1"
proxyPort=$port
allowedClients="::1,127.0.0.1,fd53:7cb8:383:3::108"
viConnected=false
validUrlForWifi=false
maxHttpRequestPerSecond=100
maxHostRequestPerSecond=30
serverSlots=5
logFile="/tmp/gateway-$instance.requests.log"
CONF
    $BB chroot /mnt/agl /tmp/http-gateway-go serve --config "/tmp/gateway-$instance.conf" \
        --instance "$instance" --listen "[::1]:$port" --listen "127.0.0.1:$port" \
        --listen "[fd53:7cb8:383:3::108]:$port" \
        --mode-url 'http://[::1]:30001/onlineengineeringmode/networkmode/' \
        --services-url 'http://[::1]:30001/colorservicemanagement/services/' \
        --catalog-events-url 'ws://[::1]:30001/' --ready-file "/tmp/gateway-$instance.ready" \
        >"/mnt/agl/tmp/gateway-$instance.log" 2>&1 &
    if [ "$instance" = b2b ]; then GATEWAY_B2B_PID=$!; else GATEWAY_B2C_PID=$!; fi
done
check=0
while [ "$check" -lt 100 ]; do
    if ! $BB kill -0 "$API_RELAY_PID" "$GATEWAY_B2B_PID" "$GATEWAY_B2C_PID" 2>/dev/null; then
        echo 'GUEST_GATEWAY_PROCESS_EXITED'; return 1
    fi
    if [ -f /mnt/agl/tmp/api-relay.ready ] && [ -f /mnt/agl/tmp/gateway-b2b.ready ] && [ -f /mnt/agl/tmp/gateway-b2c.ready ]; then
        if [ ! -f /gateway-sdk-enabled ] || [ -f /mnt/agl/tmp/sdk-relay.ready ]; then break; fi
    fi
    check=$((check+1)); $BB usleep 100000
done
if [ "$check" = 100 ]; then echo 'GUEST_GATEWAY_LISTENER_TIMEOUT'; return 1; fi
$BB chroot /mnt/agl /usr/bin/python3 /tmp/gateway-probe.py || return 1
GATEWAY_HMI_PORT=$($BB cat /gateway-hmi-port)
case "$GATEWAY_HMI_PORT" in 50006|50007) ;; *) echo 'GUEST_GATEWAY_INVALID_HMI_PORT';return 1;; esac
$BB cat > /mnt/agl/tmp/hmi-http-gateway.options <<OPTIONS
-Dhttp.proxyHost=::1
-Dhttp.proxyPort=$GATEWAY_HMI_PORT
-Dhttps.proxyHost=::1
-Dhttps.proxyPort=$GATEWAY_HMI_PORT
-Dhttp.nonProxyHosts=localhost|127.*|[::1]|::1|fd53:7cb8:383:3::108|[fd53:7cb8:383:3::108]
-Dhttps.nonProxyHosts=localhost|127.*|[::1]|::1|fd53:7cb8:383:3::108|[fd53:7cb8:383:3::108]
-Dbench.gateway.enabled=true
OPTIONS
echo "GUEST_GATEWAY_READY b2b_pid=$GATEWAY_B2B_PID b2c_pid=$GATEWAY_B2C_PID api_relay=$API_RELAY_PID hmi_proxy=$GATEWAY_HMI_PORT"
GUEST_GATEWAY_READY=true
