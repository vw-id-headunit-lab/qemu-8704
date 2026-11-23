# One-way host wall-clock feed; original Time service produces HMI updates.
HOST_CLOCK_READY=false
$BB cp /host-clock.py /mnt/agl/tmp/host-clock.py || return 1
CLOCK_DEVICE=
for name in /sys/class/virtio-ports/*/name; do
    if [ "$($BB cat "$name" 2>/dev/null)" = bench.clock ]; then
        directory=${name%/name}
        CLOCK_DEVICE=/dev/${directory##*/}
    fi
done
if [ -z "$CLOCK_DEVICE" ]; then echo 'HOST_CLOCK_DEVICE_MISSING'; return 1; fi
$BB chroot /mnt/agl /usr/bin/python3 /tmp/host-clock.py "$CLOCK_DEVICE" \
    >/mnt/agl/tmp/host-clock.log 2>&1 &
HOST_CLOCK_PID=$!
tries=0
while [ "$tries" -lt 150 ]; do
    if ! $BB kill -0 "$HOST_CLOCK_PID" 2>/dev/null; then
        $BB tail -n 15 /mnt/agl/tmp/host-clock.log
        return 1
    fi
    if [ -f /mnt/agl/tmp/host-clock.ready ]; then
        echo "HOST_CLOCK_READY source=host_system_clock device=$CLOCK_DEVICE pid=$HOST_CLOCK_PID"
        HOST_CLOCK_READY=true
        return 0
    fi
    tries=$((tries+1)); $BB usleep 100000
done
echo 'HOST_CLOCK_FIRST_SAMPLE_TIMEOUT'
$BB tail -n 15 /mnt/agl/tmp/host-clock.log
return 1
