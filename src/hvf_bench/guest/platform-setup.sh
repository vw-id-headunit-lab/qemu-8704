# Sourced in the initramfs. Original services with RAM overlays for bench inputs.
echo 'ORIGINAL_RSI_PLATFORM_BEGIN'
PLATFORM_READY=false
$BB cp /platform-readiness.py /mnt/agl/tmp/bench-readiness.py || return 1
ready() {
    $BB chroot /mnt/agl /usr/bin/python3 /tmp/bench-readiness.py "$@"
}
# Cheap kernel predicates avoid booting the firmware Python for each prerequisite.
# Final HTTP/JSON validation below is still mandatory before starting the JVM.
network_ready() {
    $BB awk '$1 == "fd537cb8038300030000000000000108" && $6 == "eth0.3" && ($5 == "00" || $5 == "80") {ok=1} END {exit !ok}' /proc/net/if_inet6
}
registry_ready() {
    $BB kill -0 "$REGISTRY_PID" 2>/dev/null || return 1
    $BB awk '$2 == "00000000000000000000000001000000:0050" && $4 == "0A" {ok=1} END {exit !ok}' /proc/net/tcp6
}
wait_prerequisite() {
    name=$1
    count=0
    while [ "$count" -lt 100 ]; do
        if "${name}_ready"; then echo "PLATFORM_READY_CHECK=$name"; return 0; fi
        if [ "$name" = registry ] && ! $BB kill -0 "$REGISTRY_PID" 2>/dev/null; then
            echo 'PLATFORM_READY_FAILED=registry process exited'; return 1
        fi
        count=$((count + 1))
        $BB usleep 100000
    done
    echo "PLATFORM_READY_FAILED=$name timeout"
    return 1
}
$BB modprobe ipv6
$BB modprobe dummy
$BB ip link add eth0.3 type dummy
$BB ip link set eth0.3 up
$BB ip -6 addr add fd53:7cb8:383:3::108/64 dev eth0.3
wait_prerequisite network || return 1
echo 'REGISTRY_BENCH_IPV6'
$BB ip -6 addr show dev eth0.3
$BB cat > /mnt/agl/tmp/bench-rsi-services <<'SERVICES'
language:system
vehicledynamics:car
usermanagement:auth
displaymanagement:system
parking:car
SERVICES
$BB mount -o bind /mnt/agl/tmp/bench-rsi-services /mnt/agl/lge/app_ro/sai/mockup/services
echo 'RSI_MOCKUP_SERVICES_RAM_OVERRIDE'
for name in settings strategies components requestedSettings changeTransactions; do
    if [ -f "/bench-language-$name.json" ]; then
        $BB cp "/bench-language-$name.json" "/mnt/agl/tmp/bench-language-$name.json"
        $BB mount -o bind "/mnt/agl/tmp/bench-language-$name.json" "/mnt/agl/lge/app_ro/sai/mockup/language/$name.json"
    fi
done
if [ -f /welcome-fixtures.tar ]; then
    $BB mkdir -p /mnt/agl/tmp/bench-full-mockup /mnt/agl/tmp/bench-spec
    $BB cp -a /mnt/agl/lge/app_ro/sai/mockup/. /mnt/agl/tmp/bench-full-mockup/
    $BB tar -xf /welcome-fixtures.tar -C /mnt/agl/tmp/bench-full-mockup
    $BB cat >> /mnt/agl/tmp/bench-full-mockup/services <<'WELCOME_SERVICES'
useraccount:auth
onlineservicedisclaimer:system
PermissionManagement:system
displayintentcenter:system
hvac:car
Time:system
wifi:system
WELCOME_SERVICES
    $BB mount -o bind /mnt/agl/tmp/bench-full-mockup /mnt/agl/lge/app_ro/sai/mockup
    $BB cp -a /mnt/agl/lge/app_ro/sai/spec/. /mnt/agl/tmp/bench-spec/
    $BB cp /vehicle-rsi-setup.py /mnt/agl/tmp/vehicle-rsi-setup.py
    $BB chroot /mnt/agl /usr/bin/python3 /tmp/vehicle-rsi-setup.py \
        /tmp/bench-spec/viwi.service.vehicledynamics/viwi.service.vehicledynamics-0.2.2.json || return 1
    if [ ! -d /mnt/agl/tmp/bench-spec/viwi.service.onlineservicedisclaimer ]; then
    $BB mkdir -p /mnt/agl/tmp/bench-spec/viwi.service.onlineservicedisclaimer
    $BB cp /mnt/agl/tmp/bench-spec/viwi.service.disclaimerinfo/viwi.service.disclaimerinfo-0.0.4.json /mnt/agl/tmp/bench-spec/viwi.service.onlineservicedisclaimer/viwi.service.onlineservicedisclaimer-0.0.4.json
    fi
    $BB mount -o bind /mnt/agl/tmp/bench-spec /mnt/agl/lge/app_ro/sai/spec
    echo 'WELCOME_MOCKUP_AND_ORIGINAL_SPEC_ALIAS_IN_RAM'
fi
$BB chroot /mnt/agl /bin/sh -c '
    export LD_LIBRARY_PATH=/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib
    cd /lge/app_ro/sai
    if [ -f /tmp/libegl-forward-trace.so ]; then
        export LD_PRELOAD=/tmp/libegl-forward-trace.so
        export BENCH_MISSING_IOCTL_BACKOFF_MS=10
    fi
    exec ./MIB_ServiceRegistryApp
' >/mnt/agl/tmp/registry.log 2>&1 &
REGISTRY_PID=$!
wait_prerequisite registry || return 1
$BB chroot /mnt/agl /bin/sh -c '
    export LD_LIBRARY_PATH=/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib
    cd /lge/app_ro/sai
    exec /lge/app_ro/bin/rsi-mockup
' >/mnt/agl/tmp/rsi-mockup.log 2>&1 &
RSI_MOCKUP_PID=$!
ready services --pid "$REGISTRY_PID" --pid "$RSI_MOCKUP_PID" || return 1
for pid in "$REGISTRY_PID" "$RSI_MOCKUP_PID"; do
    echo "ORIGINAL_RSI_PID=$pid"
    if [ -f "/proc/$pid/status" ]; then
        $BB head -n 3 "/proc/$pid/status"
    else
        wait "$pid"
        echo "ORIGINAL_RSI_PROCESS_EXIT=$?"
    fi
done
echo 'RSI_LISTENERS_BEGIN'
$BB netstat -lntp
echo 'RSI_LISTENERS_END'
echo 'ORIGINAL_RSI_PLATFORM_END'
PLATFORM_READY=true
