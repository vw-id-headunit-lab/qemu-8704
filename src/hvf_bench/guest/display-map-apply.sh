ctl() {
    /bin/busybox chroot /mnt/agl /usr/bin/env XDG_RUNTIME_DIR=/run/platform/weston WAYLAND_DISPLAY=wayland-0 /usr/bin/LayerManagerControl "$@"
}
ctl get surface 16
ctl create layer 10000 1920 1080
ctl set layer 10000 source region 0 0 1920 1080
ctl set layer 10000 destination region 0 0 1920 1080
ctl set layer 10000 opacity 1.0
ctl set layer 10000 visibility 1
ctl set surface 16 source region 0 0 1920 1080
ctl set surface 16 destination region 0 0 1920 1080
ctl set surface 16 opacity 1.0
ctl set surface 16 visibility 1
ctl set layer 10000 render order 16
ctl set screen 0 render order 10000
ctl set surfaces 16 input focus all
ctl get scene
ctl get surface 16
