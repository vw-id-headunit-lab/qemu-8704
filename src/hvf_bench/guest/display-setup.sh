# Sourced by the guest bootstrap after the original root and HMI partitions mount.
WESTON_READY=false
echo 'VIRTIO_DISPLAY_SETUP_BEGIN'
$BB modprobe virtio_pci
$BB modprobe virtio_gpu
$BB modprobe evdev
$BB modprobe virtio_input
$BB mdev -s
echo 'DISPLAY_DEVICE_NODES'
$BB ls -l /dev/fb* /dev/dri/* /dev/tty0 /dev/tty1 /dev/input/event* 2>/dev/null
$BB chmod 0700 /mnt/agl/run/platform/weston
if [ -x /mnt/agl/lib/systemd/systemd-udevd ]; then
    $BB mkdir -p /mnt/agl/run/udev
    $BB chroot /mnt/agl /lib/systemd/systemd-udevd --daemon
    $BB mkdir -p /mnt/agl/run/udev/rules.d
    $BB cat >/mnt/agl/run/udev/rules.d/99-bench-input.rules <<'INPUT_RULES'
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="QEMU Virtio Tablet", ENV{ID_INPUT}="1", ENV{ID_INPUT_MOUSE}="1", TAG+="seat"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="QEMU Virtio Keyboard", ENV{ID_INPUT}="1", ENV{ID_INPUT_KEYBOARD}="1", TAG+="seat"
INPUT_RULES
    $BB chroot /mnt/agl /usr/bin/env SYSTEMD_IGNORE_CHROOT=1 /bin/udevadm control --reload-rules
    $BB chroot /mnt/agl /usr/bin/env SYSTEMD_IGNORE_CHROOT=1 /bin/udevadm trigger --subsystem-match=input
    $BB chroot /mnt/agl /usr/bin/env SYSTEMD_IGNORE_CHROOT=1 /bin/udevadm settle --timeout=3
fi
$BB sed 's/mode=1560x700/mode=1920x1080/' /mnt/agl/usr/lib/weston.ini > /mnt/agl/tmp/weston-bench.ini
if [ -f /bench-gpu-virgl ]; then
    # GTK can advertise its initial 640x480 window size before guest boot.
    # A modeline keeps the fixed HMI render target independent of that window.
    $BB sed 's/mode=1920x1080/mode=148.5 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync/' /mnt/agl/tmp/weston-bench.ini > /mnt/agl/tmp/weston-gpu.ini
fi
$BB chroot /mnt/agl /bin/sh -c '
    export XDG_RUNTIME_DIR=/run/platform/weston
    export LD_LIBRARY_PATH=/usr/lib:/lib:/lge/app_ro/lib64
    backend=fbdev-backend.so
    device_options=--device=/dev/fb0
    config=/tmp/weston-bench.ini
    if [ -f /tmp/bench-gpu-virgl ]; then
        export LD_LIBRARY_PATH=/tmp/mesa/usr/lib/aarch64-linux-gnu:/tmp/mesa/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH
        export LD_PRELOAD=/tmp/virgl-drm-compat.so:/tmp/mesa/usr/lib/aarch64-linux-gnu/libgbm.so.1
        export LIBGL_DRIVERS_PATH=/tmp/mesa/usr/lib/aarch64-linux-gnu/dri
        export __EGL_VENDOR_LIBRARY_FILENAMES=/tmp/mesa/usr/share/glvnd/egl_vendor.d/50_mesa.json
        export MESA_LOADER_DRIVER_OVERRIDE=virtio_gpu
        export EGL_PLATFORM=drm
        backend=drm-backend.so
        device_options=
        config=/tmp/weston-gpu.ini
    fi
    exec /usr/bin/weston --backend="$backend" $device_options --tty=1 --config="$config" --socket=wayland-0 --idle-time=0 --log=/tmp/weston-fbdev.log
' &
WESTON_PID=$!
for count in $($BB seq 1 12); do
    if [ -S /mnt/agl/run/platform/weston/wayland-0 ]; then
        $BB sleep 1
        if $BB kill -0 "$WESTON_PID" 2>/dev/null; then WESTON_READY=true; fi
        break
    fi
    if ! $BB kill -0 "$WESTON_PID" 2>/dev/null; then break; fi
    $BB sleep 1
done
echo "WESTON_SOCKET_READY=$WESTON_READY"
if [ -f /mnt/agl/tmp/weston-fbdev.log ]; then
    echo 'WESTON_LOG_BEGIN'
    $BB tail -n 100 /mnt/agl/tmp/weston-fbdev.log
    echo 'WESTON_LOG_END'
fi
if [ "$WESTON_READY" = true ]; then
    echo 'ORIGINAL_WESTON_INFO_BEGIN'
    $BB timeout -s TERM -k 1 5 $BB chroot /mnt/agl /bin/sh -c 'export XDG_RUNTIME_DIR=/run/platform/weston; export WAYLAND_DISPLAY=wayland-0; exec /usr/bin/weston-info'
    echo "ORIGINAL_WESTON_INFO_EXIT=$?"
    echo 'ORIGINAL_WESTON_INFO_END'
fi
echo 'VIRTIO_DISPLAY_SETUP_END'
