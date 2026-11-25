#!/bin/sh
BB=/bin/busybox
ROOT=/mnt/agl
STATE=$ROOT/tmp/bench-update
$BB mkdir -p "$STATE"
case "${1:-}" in
 --worker)
    mode=$2
    $BB mount --make-rprivate / || exit 20
    $BB mount -t tmpfs bench-update-platform "$ROOT/sys/devices/platform" || exit 21
    $BB mkdir -p "$ROOT/dev/shm" "$ROOT/dev/pts"
    $BB mount -t devpts devpts "$ROOT/dev/pts" || exit 28
    $BB mount -t tmpfs -o mode=1777 bench-update-shm "$ROOT/dev/shm" || exit 26
    $BB mount -t tmpfs bench-update-media "$ROOT/swup" || exit 27
    platform=$ROOT/sys/devices/platform
    $BB mkdir -p "$platform/common_gpio_default" "$platform/10900000.spi/spi_master/spi6/spi6.0" "$platform/19700000.G3D1"
    printf '0x4\n' > "$platform/common_gpio_default/platform_id"
    printf '0x4\n' > "$platform/common_gpio_default/market_id"
    if [ "$mode" = rem ]; then
        download_id=0x101
        $BB cp /rem-input.so "$STATE/rem-input.so" || exit 31
        [ -p "$STATE/pointer" ] || $BB mkfifo "$STATE/pointer" || exit 32
        $BB mount -t tmpfs bench-update-drm "$ROOT/dev/dri" || exit 29
    else
        download_id=0x000
    fi
    printf '%s\n' "$download_id" > "$platform/common_gpio_default/download_id"
    $BB touch "$platform/19700000.G3D1/gpu_active_rate_enable" "$platform/10900000.spi/spi_master/spi6/spi6.0/inject_cmd"
    $BB mount -o bind "$STATE/reset-command" "$platform/10900000.spi/spi_master/spi6/spi6.0/inject_cmd" || exit 22
    $BB touch "$STATE/sysrq"
    $BB mount -o bind "$STATE/sysrq" "$ROOT/proc/sysrq-trigger" || exit 23
    $BB mkdir -p "$ROOT/dev/graphics"
    [ -e "$ROOT/dev/graphics/fb0" ] || $BB ln -s /dev/fb0 "$ROOT/dev/graphics/fb0"
    echo "ORIGINAL_SWUP_BOOT_MODE=$mode DOWNLOAD_ID=$download_id"
    export BENCH_SWUP_MODE=$mode
    exec $BB chroot "$ROOT" /bin/sh -c 'export LD_LIBRARY_PATH=/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib; cd /lge/app_ro/update/swup; exec /usr/bin/script -e -q -c '"'"'if [ "$BENCH_SWUP_MODE" = rem ]; then export LD_PRELOAD=/tmp/bench-update/rem-input.so; fi; exec ./swup'"'"' /dev/null'
    ;;
 --supervise)
    mode=normal
    if [ -f "$STATE/boot-mode" ]; then
        mode=$($BB cat "$STATE/boot-mode")
        case "$mode" in normal|rem) ;; *) exit 30 ;; esac
    fi
    $BB rm -f "$STATE/reset-command"
    $BB mkfifo "$STATE/reset-command" || exit 24
    exec 4<>"$STATE/reset-command"
    while :; do
        printf '%s\n' "$mode" > "$STATE/boot-mode"
        echo "BENCH_NATIVE_UPDATE_MODE=$mode" > /dev/console
        $BB rm -f "$STATE/worker-$mode.exit"
        : > "$STATE/swup-$mode.log"
        (
            $BB unshare -mpf "$BB" sh "$0" --worker "$mode"
            echo "$?" > "$STATE/worker-$mode.exit"
        ) </dev/null >>"$STATE/swup-$mode.log" 2>&1 3>&- 4>&- &
        worker=$!
        printf '%s\n' "$worker" > "$STATE/worker.pid"
        $BB cp /update-watch.py "$STATE/watch.py"
        $BB chroot "$ROOT" /usr/bin/python3 /tmp/bench-update/watch.py "$mode" </dev/null >"$STATE/watch-$mode.log" 2>&1 3>&- 4>&- &
        watcher=$!
        while IFS= read -r request <&4; do
            printf 'REQUEST=%s FROM=%s\n' "$request" "$mode" >> "$STATE/transitions.log"
            case "$request:$mode" in
                enter_rem:normal|enter_normal:rem) ;;
                *) echo BSP_RESET_WITHOUT_MODE_CHANGE >> "$STATE/transitions.log"; continue ;;
            esac
            $BB kill -TERM "$watcher" 2>/dev/null
            wait "$watcher" 2>/dev/null
            native=$($BB pidof swup)
            if [ -n "$native" ]; then
                $BB kill -TERM $native
                $BB sleep 1
                for pid in $native; do
                    $BB kill -0 "$pid" 2>/dev/null && $BB kill -KILL "$pid"
                done
            fi
            wait "$worker"
            printf 'PREVIOUS_WORKER_EXIT=%s\n' "$?" >> "$STATE/transitions.log"
            if [ "$mode" = normal ]; then
                mode=rem
                for name in java weston; do
                    pids=$($BB pidof "$name")
                    [ -z "$pids" ] || $BB kill -STOP $pids
                done
            else
                mode=normal
                for name in weston java; do
                    pids=$($BB pidof "$name")
                    [ -z "$pids" ] || $BB kill -CONT $pids
                done
            fi
            break
        done
    done
    ;;
 *)
    if [ -s "$STATE/supervisor.pid" ] && $BB kill -0 "$($BB cat "$STATE/supervisor.pid")" 2>/dev/null; then
        echo ORIGINAL_SWUP_ALREADY_RUNNING
        exit 0
    fi
    if $BB pidof swup >/dev/null; then
        echo ORIGINAL_SWUP_UNMANAGED
        exit 25
    fi
    "$BB" sh "$0" --supervise </dev/null >"$STATE/supervisor.log" 2>&1 3>&- &
    echo "$!" > "$STATE/supervisor.pid"
    echo ORIGINAL_SWUP_SUPERVISOR=$!
    ;;
esac
