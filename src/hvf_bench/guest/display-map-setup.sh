#!/bin/sh
BB=/bin/busybox
ctl() {
    $BB chroot /mnt/agl /usr/bin/env XDG_RUNTIME_DIR=/run/platform/weston WAYLAND_DISPLAY=wayland-0 /usr/bin/LayerManagerControl "$@"
}
for count in $($BB seq 1 90); do
    if [ -f /mnt/agl/tmp/hmi.pid ]; then
        pid=$($BB cat /mnt/agl/tmp/hmi.pid)
        result=$(ctl get surface 16)
        owner=$(echo "$result" | $BB awk '/created by pid:/ {print $5}')
        if [ -n "$pid" ] && [ "$owner" = "$pid" ]; then
            layers=$(ctl get layers)
            case "$layers" in
                '0 Layer(s):'*)
                    $BB sh /display-map-apply
                    after=$(ctl get surface 16)
                    echo "$after"
                    verified_owner=$(echo "$after" | $BB awk '/created by pid:/ {print $5}')
                    if [ "$verified_owner" = "$pid" ] && echo "$after" | $BB grep -q '10000(0x2710)'; then
                        echo 'ORIGINAL_HMI_SURFACE_MAPPED=true'
                        exit 0
                    fi
                    echo 'ORIGINAL_HMI_SURFACE_MAP_UNVERIFIED'
                    exit 1
                    ;;
                *) echo 'DISPLAY_MAP_SKIPPED_EXISTING_LAYERS'; echo "$layers"; exit 1 ;;
            esac
        fi
    fi
    $BB sleep 2
done
echo 'DISPLAY_MAP_NO_CONFIRMED_HMI_SURFACE'
exit 1
