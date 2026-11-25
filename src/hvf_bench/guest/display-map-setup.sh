#!/bin/sh
BB=/bin/busybox
ctl() {
    $BB chroot /mnt/agl /usr/bin/env XDG_RUNTIME_DIR=/run/platform/weston WAYLAND_DISPLAY=wayland-0 /usr/bin/LayerManagerControl "$@"
}
map_hmi() {
    for count in $($BB seq 1 90); do
        if [ -f /mnt/agl/tmp/hmi.pid ]; then
            pid=$($BB cat /mnt/agl/tmp/hmi.pid)
            result=$(ctl get surface 16)
            owner=$(echo "$result" | $BB awk '/created by pid:/ {print $5}')
            if [ -n "$pid" ] && [ "$owner" = "$pid" ]; then
                layers=$(ctl get layers)
                case "$layers" in
                    '0 Layer(s):'*) $BB sh /display-map-apply ;;
                    *)
                        if ! echo "$result" | $BB grep -q '10000(0x2710)'; then
                            echo 'DISPLAY_MAP_SKIPPED_EXISTING_LAYERS';echo "$layers";return 1
                        fi
                        ;;
                esac
                after=$(ctl get surface 16)
                verified_owner=$(echo "$after" | $BB awk '/created by pid:/ {print $5}')
                if [ "$verified_owner" = "$pid" ] && echo "$after" | $BB grep -q '10000(0x2710)'; then
                    echo 'ORIGINAL_HMI_SURFACE_MAPPED=true';return 0
                fi
                echo 'ORIGINAL_HMI_SURFACE_MAP_UNVERIFIED';return 1
            fi
        fi
        $BB sleep 2
    done
    echo 'DISPLAY_MAP_NO_CONFIRMED_HMI_SURFACE';return 1
}
map_hmi || exit 1
last=
while $BB kill -0 "$pid" 2>/dev/null; do
    result=$(ctl get surface 19 2>/dev/null)
    owner=$(echo "$result" | $BB awk '/created by pid:/ {print $5}')
    size=$(echo "$result" | $BB sed -n 's/.*original size: *x=\([0-9][0-9]*\), y=\([0-9][0-9]*\).*/\1 \2/p')
    set -- $size
    if [ "$#" = 2 ] && [ "$1" -gt 0 ] && [ "$2" -gt 0 ] && [ "$($BB readlink /proc/$owner/exe)" = /mnt/agl/lge/navi/naviengine/package/MXNavi/MXNavi ]; then
        key=$owner:$1:$2
        crop=$(echo "$result" | $BB sed -n 's/.*source region: *x=0, y=0, w=\([0-9][0-9]*\), h=\([0-9][0-9]*\).*/\1:\2/p')
        target=$(echo "$result" | $BB sed -n 's/.*destination region: *x=0, y=0, w=\([0-9][0-9]*\), h=\([0-9][0-9]*\).*/\1:\2/p')
        if [ "$key" != "$last" ] || [ "$crop" != "$1:$2" ] || [ "$target" != 1920:1080 ]; then
            ctl set surface 19 source region 0 0 "$1" "$2" &&
            ctl set surface 19 destination region 0 0 1920 1080 &&
            ctl set surface 19 opacity 1.0 &&
            ctl set surface 19 visibility 1 &&
            ctl set layer 10000 render order 19,16 && {
                last=$key;echo "ORIGINAL_NAVIGATION_SURFACE_MAPPED=$key"
            }
        fi
    else
        last=
    fi
    $BB sleep 2
done
